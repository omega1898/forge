// The editor's tree, built from the same project.summary record the CLI prints.

export interface TreeNode {
  id: string;
  label: string;
  description?: string;
  tooltip?: string;
  children: TreeNode[];
}

const SECTIONS: Array<[string, string]> = [
  ["running", "Running"], ["ready", "Ready"], ["waiting_on_tasks", "Waiting on other tasks"],
  ["waiting_for_user", "Waiting for you"], ["blocked", "Blocked"], ["pending_decisions", "Decisions needed"],
  ["done", "Done"], ["ended", "Ended"], ["decided", "Decided"],
];

export function viewFromSummary(summary: any): TreeNode[] {
  const roots: TreeNode[] = [];
  for (const [key, label] of SECTIONS) {
    const section = summary[key];
    if (!section || section.total === 0) continue;
    const children: TreeNode[] = section.items.map((item: any) => ({
      id: item.task_id ?? item.decision_id,
      label: item.title ?? item.question,
      description: item.reason || item.status || item.decision_kind || "",
      tooltip: item.waiting_on?.length ? `waiting on ${item.waiting_on.join(", ")}` : undefined,
      children: [],
    }));
    const hidden = section.total - section.items.length;
    if (hidden > 0) children.push({ id: `${key}:more`, label: `${hidden} more`, children: [] });
    roots.push({ id: key, label: `${label} (${section.total})`, children });
  }
  return roots;
}

export function itemIds(nodes: TreeNode[]): string[] {
  return nodes.flatMap((node) => node.children.filter((c) => !c.id.endsWith(":more")).map((c) => c.id));
}
