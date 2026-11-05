// VS Code / VSCodium glue. Everything with logic lives in the other modules,
// which are tested without an editor; this file only wires them to the API.
import { ForgeApi } from "./client";
import { renderMarkdown, webviewCsp, WEBVIEW_OPTIONS } from "./markdown";
import { KEYMAP, ReviewSession } from "./review";
import { EventCursor } from "./sse";
import { TreeNode, viewFromSummary } from "./view";

export function activate(context: any): void {
  const vscode = require("vscode");
  const url = process.env.FORGE_URL;
  const token = process.env.FORGE_TOKEN;
  const projectId = process.env.FORGE_PROJECT_ID;
  if (!url || !token || !projectId) {
    vscode.window.showWarningMessage("FORGE: set FORGE_URL, FORGE_TOKEN and FORGE_PROJECT_ID (see `forge connect`).");
    return;
  }
  const api = new ForgeApi({ url, token });
  let roots: TreeNode[] = [];
  const changed = new vscode.EventEmitter();
  const provider = {
    onDidChangeTreeData: changed.event,
    getChildren: (node?: TreeNode) => (node ? node.children : roots),
    getTreeItem: (node: TreeNode) => {
      const item = new vscode.TreeItem(node.label, node.children.length ? 1 : 0);
      item.id = node.id;
      item.description = node.description;
      item.tooltip = node.tooltip;
      return item;
    },
  };
  const refresh = async () => {
    roots = viewFromSummary(await api.call("project.summary", { project_id: projectId }));
    changed.fire();
  };
  context.subscriptions.push(vscode.window.registerTreeDataProvider("forge.tasks", provider));
  context.subscriptions.push(vscode.commands.registerCommand("forge.refresh", refresh));

  // Follow the event stream; any applied event or reload request refreshes from the record.
  const cursor = new EventCursor(0);
  const stop = new AbortController();
  context.subscriptions.push({ dispose: () => stop.abort() });
  void refresh().then(() => api.follow(cursor, (decision) => {
    if (decision.action !== "skip") void refresh();
  }, stop.signal));

  let session: ReviewSession | null = null;
  const panel = { current: null as any };
  const show = (text: string) => {
    if (!panel.current) {
      panel.current = vscode.window.createWebviewPanel("forge.review", "FORGE review", 1, WEBVIEW_OPTIONS);
    }
    const csp = webviewCsp(panel.current.webview.cspSource);
    panel.current.webview.html = `<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy" ` +
      `content="${csp}"></head><body role="main" aria-live="polite">${renderMarkdown(text)}</body></html>`;
  };
  for (const [key, action] of Object.entries(KEYMAP)) {
    context.subscriptions.push(vscode.commands.registerCommand(`forge.review.${action}`, async () => {
      if (!session) return;
      const reason = action === "reject" ? (await vscode.window.showInputBox({ prompt: "Why reject?" })) ?? "" : "";
      const outcome = session.press(key, reason);
      if (typeof outcome === "string") {
        show(outcome);
      } else {
        await api.call(outcome.method, outcome.params);
        vscode.commands.executeCommand("setContext", "forge.reviewing", false);
        show(`Decision recorded: **${outcome.params.disposition}**.`);
        session = null;
      }
    }));
  }
  context.subscriptions.push(vscode.commands.registerCommand("forge.review.open", (proposal: any) => {
    session = new ReviewSession(proposal);
    vscode.commands.executeCommand("setContext", "forge.reviewing", true);
    show(session.announce());
  }));
}

export function deactivate(): void {}
