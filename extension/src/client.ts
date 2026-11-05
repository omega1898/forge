// Loopback JSON-RPC and event-stream client for the FORGE controller.
import * as http from "node:http";
import { CursorDecision, EventCursor, SseParser } from "./sse";

export interface Connection {
  url: string;
  token: string;
}

export class RpcError extends Error {
  constructor(public readonly code: number, message: string, public readonly data?: unknown) {
    super(message);
  }
}

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"]);

export class ForgeApi {
  readonly host: string;
  readonly port: number;
  private counter = 0;

  constructor(private readonly connection: Connection) {
    const match = /^http:\/\/([^/:]+|\[::1\]):(\d+)\/?$/.exec(connection.url);
    if (!match || !LOOPBACK.has(match[1])) {
      throw new Error("FORGE is reached only on this machine's loopback interface");
    }
    this.host = match[1] === "[::1]" ? "::1" : match[1];
    this.port = Number(match[2]);
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return { Host: `${this.host === "::1" ? "[::1]" : this.host}:${this.port}`,
             Authorization: `Bearer ${this.connection.token}`, ...extra };
  }

  call(method: string, params: Record<string, unknown> = {}): Promise<any> {
    const body = JSON.stringify({ jsonrpc: "2.0", id: `vscodium-${++this.counter}`, method, params });
    return new Promise((resolve, reject) => {
      const request = http.request({ host: this.host, port: this.port, method: "POST", path: "/rpc", timeout: 30000,
        headers: this.headers({ "Content-Type": "application/json",
                                 "Content-Length": String(Buffer.byteLength(body, "utf8")) }) }, (response) => {
        let text = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => { text += chunk; });
        response.on("end", () => {
          if (response.statusCode !== 200) return reject(new RpcError(response.statusCode ?? 0, text.slice(0, 500)));
          try {
            const reply = JSON.parse(text);
            if (reply.error) reject(new RpcError(reply.error.code, reply.error.message, reply.error.data));
            else resolve(reply.result);
          } catch (error) {
            reject(error);
          }
        });
      });
      request.on("error", reject);
      request.write(body);
      request.end();
    });
  }

  mutate(method: string, projectId: string, params: Record<string, unknown>, key: string): Promise<any> {
    return this.call(method, { project_id: projectId, idempotency_key: key, params });
  }

  /** One stream connection, resuming after cursor.lastSeq. Resolves when the server closes it. */
  streamOnce(cursor: EventCursor, onDecision: (d: CursorDecision) => void, signal?: AbortSignal): Promise<void> {
    return new Promise((resolve) => {
      const parser = new SseParser();
      const request = http.request({ host: this.host, port: this.port, method: "GET", path: "/events",
        headers: this.headers({ Accept: "text/event-stream", "Last-Event-ID": String(cursor.lastSeq) }) },
        (response) => {
          response.setEncoding("utf8");
          response.on("data", (chunk) => {
            for (const message of parser.push(chunk)) onDecision(cursor.accept(message));
          });
          response.on("end", () => resolve());
          response.on("close", () => resolve());
        });
      request.on("error", () => resolve());
      signal?.addEventListener("abort", () => { request.destroy(); resolve(); });
      request.end();
    });
  }

  /** Follow events until aborted, reconnecting with Last-Event-ID so nothing is missed or repeated. */
  async follow(cursor: EventCursor, onDecision: (d: CursorDecision) => void, signal: AbortSignal,
               retryMs = 500): Promise<void> {
    while (!signal.aborted) {
      await this.streamOnce(cursor, onDecision, signal);
      if (signal.aborted) return;
      await new Promise<void>((resolve) => setTimeout(resolve, retryMs));
    }
  }
}
