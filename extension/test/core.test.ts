import assert = require("node:assert/strict");
import test = require("node:test");
import { escapeHtml, renderMarkdown, webviewCsp, WEBVIEW_OPTIONS } from "../src/markdown";
import { KEYMAP, parseUnifiedDiff, ReviewSession } from "../src/review";
import { EventCursor, SseParser } from "../src/sse";
import { itemIds, viewFromSummary } from "../src/view";

const pkg = require("../../package.json");

test("SSE parser handles chunk boundaries, comments, CRLF and multi-line data", () => {
  const parser = new SseParser();
  const first = parser.push(": keep-alive\n\nid: 1\nevent: task.created\nda");
  assert.deepEqual(first, []);
  const second = parser.push('ta: {"event_seq": 1}\r\n\r\nid: 2\ndata: line one\ndata: line two\n\n');
  assert.deepEqual(second, [
    { id: "1", event: "task.created", data: '{"event_seq": 1}' },
    { id: "2", event: "message", data: "line one\nline two" },
  ]);
});

test("the cursor applies each event once and in order, and asks for a reload on a gap", () => {
  const cursor = new EventCursor(4);
  const event = (seq: number) => ({ id: String(seq), event: "x", data: JSON.stringify({ event_seq: seq }) });
  assert.equal(cursor.accept(event(4)).action, "skip");          // replayed after reconnect
  assert.equal(cursor.accept(event(5)).action, "apply");
  assert.equal(cursor.accept(event(5)).action, "skip");
  assert.equal(cursor.accept(event(7)).action, "reload");        // 6 is missing
  assert.equal(cursor.lastSeq, 7);
  assert.equal(cursor.accept({ id: "8", event: "x", data: '{"event_seq": 9}' }).action, "reload");
  assert.equal(cursor.accept({ id: "zz", event: "x", data: "{}" }).action, "skip");
  assert.equal(cursor.accept({ event: "reload", data: "{}" }).action, "reload");
});

const HOSTILE = [
  "[run](command:workbench.action.terminal.new)",
  "[run](  command:workbench.action.terminal.sendSequence?%7B%22text%22%3A%22rm%20-rf%20~%22%7D)",
  "<command:workbench.action.reloadWindow>",
  "[x](vscode://file/etc/passwd)", "[x](javascript:alert(1))", "[x](JaVaScRiPt:alert(1))",
  "[x](data:text/html;base64,PHNjcmlwdD4=)", "[x](file:///etc/passwd)",
  "<script>alert(1)</script>", "<img src=x onerror=alert(1)>", '<a href="command:x">click</a>',
  "<iframe src=https://evil.test></iframe>", "[x](https://ok.test/\"onmouseover=\"alert(1))",
  "```\n<script>alert(1)</script>\n```", "`[x](command:y)`",
];

test("malicious Markdown cannot invoke commands or run script", () => {
  for (const source of HOSTILE) {
    const html = renderMarkdown(source);
    assert.doesNotMatch(html, /href="(?!https?:)/i, source);
    assert.doesNotMatch(html, /<(script|img|iframe|a\s+href="command)/i, source);
    assert.doesNotMatch(html, /<[a-z][^>]*\son\w+\s*=/i, source);     // no event handler inside a real tag
  }
  assert.match(renderMarkdown("See [docs](https://forge.test/a?b=1&c=2)."),
    /<a href="https:\/\/forge\.test\/a\?b=1&amp;c=2" rel="noopener noreferrer">docs<\/a>/);
  assert.match(renderMarkdown("# Title\n- **bold** `code`"), /<h1>Title<\/h1>\n<ul>\n<li><strong>bold<\/strong> <code>code<\/code><\/li>/);
  assert.equal(WEBVIEW_OPTIONS.enableCommandUris, false);
  assert.equal(WEBVIEW_OPTIONS.enableScripts, false);
  assert.match(webviewCsp("vscode-resource:"), /default-src 'none'/);
  assert.equal(escapeHtml(`<"'&>`), "&lt;&quot;&#39;&amp;&gt;");
});

const DIFF = [
  "--- a/parser.c", "+++ b/parser.c", "@@ -10,3 +10,4 @@", " a", "-b", "+c", "+d",
  "@@ -40,2 +41,2 @@", " x", "-y", "+z", "--- a/tags.c", "+++ b/tags.c", "@@ -1,1 +1,2 @@", " k", "+l",
].join("\n");

test("keyboard alone completes a review, and approval needs every change seen", () => {
  const session = new ReviewSession({ proposal_id: "proposal_1", proposal_digest: "sha256:abc", decision_id: "decision_1",
    hunks: parseUnifiedDiff(DIFF) });
  assert.match(session.announce(), /Change 1 of 3 in parser\.c: 2 added, 1 removed/);
  assert.match(String(session.press("alt+a")), /Review every change before approving: 1 of 3 seen/);
  session.press("alt+n");
  assert.match(String(session.press("alt+n")), /Change 3 of 3 in tags\.c/);
  assert.match(String(session.press("alt+p")), /Change 2 of 3/);
  const decision = session.press("alt+a");
  assert.deepEqual(decision, { method: "decision.record", params: { decision_id: "decision_1", disposition: "approved",
    reason: "reviewed every change", proposal_digest: "sha256:abc" } });
  assert.equal(session.press("alt+r", "too late"), "This review is finished.");
  assert.equal(session.press("ctrl+q"), "No action is bound to ctrl+q.");
});

test("rejecting needs a reason; every action has a key in the manifest", () => {
  const session = new ReviewSession({ proposal_id: "p", proposal_digest: "sha256:d", decision_id: "d",
    hunks: parseUnifiedDiff(DIFF) });
  assert.equal(session.press("alt+r", "  "), "Rejecting needs a reason.");
  const rejected: any = session.press("alt+r", "changes the wire format");
  assert.equal(rejected.params.disposition, "rejected");
  const bound = new Map(pkg.contributes.keybindings.map((k: any) => [k.key, k.command]));
  for (const [key, action] of Object.entries(KEYMAP)) assert.equal(bound.get(key), `forge.review.${action}`);
});

test("the tree shows the summary's sections and items exactly", () => {
  const summary = {
    running: { total: 1, items: [{ task_id: "task_a", title: "Parse tags", status: "running" }] },
    ready: { total: 0, items: [] },
    blocked: { total: 3, items: [{ task_id: "task_b", title: "Release", reason: "waiting for a decision" }] },
    pending_decisions: { total: 1, items: [{ decision_id: "decision_c", question: "Approve?", decision_kind: "approval" }] },
  };
  const tree = viewFromSummary(summary);
  assert.deepEqual(tree.map((n) => n.label), ["Running (1)", "Blocked (3)", "Decisions needed (1)"]);
  assert.deepEqual(itemIds(tree), ["task_a", "task_b", "decision_c"]);
  assert.equal(tree[1].children[1].label, "2 more");
});
