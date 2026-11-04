// Markdown for webviews that cannot invoke commands.
//
// Everything is HTML-escaped first, so raw HTML, <script>, event-handler
// attributes and autolinks such as <command:...> become inert text. Only a
// small Markdown subset is then rendered, and a link survives only if its
// target is http or https. command:, vscode:, javascript:, data: and file:
// links are shown as plain text with a note. The webview is also created
// with scripts and command URIs disabled and a restrictive content policy.

export const WEBVIEW_OPTIONS = Object.freeze({ enableScripts: false, enableCommandUris: false, localResourceRoots: [] });

export function webviewCsp(cspSource: string): string {
  return `default-src 'none'; img-src ${cspSource} https:; style-src ${cspSource};`;
}

export function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function inline(raw: string): string {
  const spans: string[] = [];
  // Code spans first, so link syntax inside code stays code.
  let text = raw.replace(/`([^`]+)`/g, (_, code: string) => {
    spans.push(`<code>${escapeHtml(code)}</code>`);
    return `\u0000${spans.length - 1}\u0000`;
  });
  text = escapeHtml(text);
  text = text.replace(/\[([^\]]*)\]\(([^)\s]*)\)/g, (_, label: string, target: string) => {
    const decoded = target.replace(/&amp;/g, "&");
    if (/^https?:\/\/[^\s"'<>]+$/i.test(decoded)) {
      return `<a href="${escapeHtml(decoded)}" rel="noopener noreferrer">${label}</a>`;
    }
    return `${label} <em>(link removed: only http and https links are allowed)</em>`;
  });
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  return text.replace(/\u0000(\d+)\u0000/g, (_, index: string) => spans[Number(index)]);
}

export function renderMarkdown(source: string): string {
  const out: string[] = [];
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  let inCode = false;
  let code: string[] = [];
  let list = false;
  const closeList = () => { if (list) { out.push("</ul>"); list = false; } };
  for (const line of lines) {
    if (line.startsWith("```")) {
      if (inCode) {
        out.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
        code = [];
      } else {
        closeList();
      }
      inCode = !inCode;
      continue;
    }
    if (inCode) { code.push(line); continue; }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    const item = /^\s*[-*]\s+(.*)$/.exec(line);
    if (heading) {
      closeList();
      out.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`);
    } else if (item) {
      if (!list) { out.push("<ul>"); list = true; }
      out.push(`<li>${inline(item[1])}</li>`);
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  if (inCode) out.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  closeList();
  return out.join("\n");
}
