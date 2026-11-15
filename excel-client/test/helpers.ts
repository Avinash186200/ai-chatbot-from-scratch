import { GatewayClient, type ClientOptions } from "../src/lib/client";

export const TOKEN = "tok_9f8e7d6c5b4a-SECRET";
export const BASE = "https://gateway.example.test";

export interface FetchCall {
  url: string;
  init: RequestInit;
}

/** A fetch mock that records calls and answers with the given handler. */
export function mockFetch(handler: (call: FetchCall, index: number) => Promise<Response> | Response) {
  const calls: FetchCall[] = [];
  const fn = jest.fn((url: string | URL | Request, init?: RequestInit) => {
    const call = { url: String(url), init: init ?? {} };
    const index = calls.push(call) - 1;
    return Promise.resolve().then(() => handler(call, index));
  });
  return { fn: fn as unknown as typeof fetch, calls };
}

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

export function status(code: number, headers: Record<string, string> = {}): Response {
  return new Response(code === 204 ? null : "error body", { status: code, headers });
}

/** A fetch that never answers until its signal aborts, like a hung server. */
export function hangingFetch() {
  return mockFetch(
    (call) =>
      new Promise<Response>((_, reject) => {
        const signal = call.init.signal!;
        signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      }),
  );
}

/** A response whose resolution the test controls. */
export function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

export function okEnvelope(result: unknown, provenance: Record<string, unknown> = {}) {
  return {
    tool: "forecast_series",
    ok: true,
    payload: { result },
    notes: null,
    provenance: {
      model_id: "placeholder-ar1",
      model_version: "1.1.0",
      as_of: "2023-06-30",
      snapshot_id: "snap-0123456789abcdef",
      computed_at: "2025-03-03T12:00:00+00:00",
      ...provenance,
    },
  };
}

export function refusal(code: string, message: string) {
  return { tool: "forecast_series", ok: false, payload: { error: { code, message } }, notes: null, provenance: null };
}

export function makeClient(fetchImpl: typeof fetch, options: Partial<ClientOptions> = {}) {
  return new GatewayClient({
    settings: () => ({ baseUrl: BASE, token: TOKEN }),
    fetch: fetchImpl,
    random: () => 0.5,
    ...options,
  });
}

/** Let queued microtasks and promise chains run (real timers only). */
export async function flush(times = 5): Promise<void> {
  for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0));
}
