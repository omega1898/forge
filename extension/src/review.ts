// Keyboard-first review of a change proposal, bound to the exact digest shown.

export interface Hunk {
  file: string;
  header: string;
  lines: string[];
}

export interface ProposalView {
  proposal_id: string;
  proposal_digest: string;
  decision_id: string;
  hunks: Hunk[];
}

export type Action = "next" | "previous" | "approve" | "reject";

// The same keys package.json binds; every review action has one.
export const KEYMAP: Readonly<Record<string, Action>> = Object.freeze({
  "alt+n": "next", "alt+p": "previous", "alt+a": "approve", "alt+r": "reject",
});

export interface DecisionCall {
  method: "decision.record";
  params: { decision_id: string; disposition: "approved" | "rejected"; reason: string; proposal_digest: string };
}

export function parseUnifiedDiff(text: string): Hunk[] {
  const hunks: Hunk[] = [];
  let file = "";
  let current: Hunk | null = null;
  for (const line of text.split("\n")) {
    if (line.startsWith("+++ ")) {
      file = line.slice(4).replace(/^b\//, "").split("\t")[0];
      current = null;
    } else if (line.startsWith("@@")) {
      current = { file, header: line, lines: [] };
      hunks.push(current);
    } else if (current && /^[ +\-\\]/.test(line)) {
      current.lines.push(line);
    }
  }
  return hunks;
}

export class ReviewSession {
  private position = 0;
  private readonly visited = new Set<number>([0]);
  private finished = false;

  constructor(private readonly proposal: ProposalView) {
    if (proposal.hunks.length === 0) throw new Error("a proposal with no changes cannot be reviewed");
  }

  get current(): Hunk {
    return this.proposal.hunks[this.position];
  }

  announce(): string {
    const hunk = this.current;
    const added = hunk.lines.filter((l) => l.startsWith("+")).length;
    const removed = hunk.lines.filter((l) => l.startsWith("-")).length;
    return `Change ${this.position + 1} of ${this.proposal.hunks.length} in ${hunk.file}: ` +
      `${added} added, ${removed} removed. Seen ${this.visited.size} of ${this.proposal.hunks.length}.`;
  }

  canApprove(): boolean {
    return this.visited.size === this.proposal.hunks.length;
  }

  press(key: string, reason = ""): string | DecisionCall {
    const action = KEYMAP[key];
    if (!action) return `No action is bound to ${key}.`;
    if (this.finished) return "This review is finished.";
    if (action === "next" || action === "previous") {
      const last = this.proposal.hunks.length - 1;
      this.position = action === "next" ? Math.min(this.position + 1, last) : Math.max(this.position - 1, 0);
      this.visited.add(this.position);
      return this.announce();
    }
    if (action === "approve") {
      if (!this.canApprove()) {
        return `Review every change before approving: ${this.visited.size} of ${this.proposal.hunks.length} seen.`;
      }
      return this.decide("approved", reason || "reviewed every change");
    }
    if (!reason.trim()) return "Rejecting needs a reason.";
    return this.decide("rejected", reason.trim());
  }

  private decide(disposition: "approved" | "rejected", reason: string): DecisionCall {
    this.finished = true;
    return { method: "decision.record", params: { decision_id: this.proposal.decision_id, disposition, reason,
      proposal_digest: this.proposal.proposal_digest } };
  }
}
