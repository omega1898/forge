// Minimal declarations for the few Node.js APIs this extension uses, so it
// builds offline with no third-party packages.
declare const process: { env: Record<string, string | undefined>; exitCode?: number; stdout: { write(s: string): void } };
declare function setTimeout(callback: () => void, ms: number): unknown;
declare function clearTimeout(handle: unknown): void;
declare class AbortController { readonly signal: AbortSignal; abort(): void; }
interface AbortSignal { readonly aborted: boolean; addEventListener(type: "abort", listener: () => void): void; }
declare const require: (name: string) => any;
declare const Buffer: { byteLength(text: string, encoding: string): number };
declare module "node:http" {
  interface IncomingMessage {
    statusCode?: number;
    setEncoding(encoding: string): void;
    on(event: "data", listener: (chunk: string) => void): this;
    on(event: "end" | "close", listener: () => void): this;
    on(event: "error", listener: (error: Error) => void): this;
  }
  interface ClientRequest {
    on(event: "error", listener: (error: Error) => void): this;
    write(data: string): void;
    end(): void;
    destroy(): void;
  }
  interface RequestOptions {
    host: string; port: number; method: string; path: string; headers: Record<string, string>; timeout?: number;
  }
  function request(options: RequestOptions, callback: (response: IncomingMessage) => void): ClientRequest;
}
declare module "node:test" {
  function test(name: string, fn: () => void | Promise<void>): void;
  export = test;
}
declare module "node:assert/strict" {
  interface Assert {
    (value: unknown, message?: string): void;
    equal(actual: unknown, expected: unknown, message?: string): void;
    deepEqual(actual: unknown, expected: unknown, message?: string): void;
    ok(value: unknown, message?: string): void;
    match(value: string, pattern: RegExp, message?: string): void;
    doesNotMatch(value: string, pattern: RegExp, message?: string): void;
    throws(fn: () => unknown, pattern?: RegExp): void;
  }
  const assert: Assert;
  export = assert;
}
