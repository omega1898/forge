// Server-sent events: an incremental parser and a cursor that applies each
// event exactly once, in order, across reconnects.

export interface SseMessage {
  id?: string;
  event: string;
  data: string;
}

export class SseParser {
  private buffer = "";
  private data: string[] = [];
  private event = "";
  private id: string | undefined;

  push(chunk: string): SseMessage[] {
    this.buffer += chunk;
    const out: SseMessage[] = [];
    let newline: number;
    while ((newline = this.buffer.search(/\r\n|\n|\r/)) >= 0) {
      const length = this.buffer.startsWith("\r\n", newline) ? 2 : 1;
      const line = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + length);
      if (line === "") {
        if (this.data.length > 0) {
          out.push({ id: this.id, event: this.event || "message", data: this.data.join("\n") });
        }
        this.data = [];
        this.event = "";
        continue;
      }
      if (line.startsWith(":")) continue; // comment, e.g. keep-alive
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "data") this.data.push(value);
      else if (field === "event") this.event = value;
      else if (field === "id" && !value.includes("\0")) this.id = value;
    }
    return out;
  }
}

export type CursorDecision =
  | { action: "apply"; seq: number; event: Record<string, unknown> }
  | { action: "skip"; reason: string }
  | { action: "reload"; reason: string };

export class EventCursor {
  constructor(public lastSeq = 0) {}

  accept(message: SseMessage): CursorDecision {
    if (message.event === "reload") return { action: "reload", reason: "the server asked for a full refresh" };
    const seq = Number(message.id);
    if (!Number.isSafeInteger(seq) || seq < 1) return { action: "skip", reason: "event without a valid sequence" };
    if (seq <= this.lastSeq) return { action: "skip", reason: `event ${seq} was already applied` };
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(message.data);
    } catch {
      return { action: "reload", reason: `event ${seq} is not valid JSON` };
    }
    if (event.event_seq !== seq) return { action: "reload", reason: `event ${seq} disagrees with its id` };
    if (seq !== this.lastSeq + 1) {
      // A hole means something was missed: refresh state, then continue from here.
      this.lastSeq = seq;
      return { action: "reload", reason: `missed events before ${seq}` };
    }
    this.lastSeq = seq;
    return { action: "apply", seq, event };
  }
}
