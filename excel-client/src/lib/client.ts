/**
 * HTTP client for the tool gateway, built for spreadsheet recalculation:
 *
 * - Dedup: identical in-flight requests (same method, path and canonical
 *   arguments) share one fetch. A recalculation that asks for the same
 *   forecast in 200 cells makes one call. Nothing is cached after it settles.
 * - Concurrency gate: at most `maxConcurrent` (6) distinct requests in flight;
 *   the rest wait in a FIFO queue. A request holds its slot through its
 *   retries.
 * - Timeouts and retries: each attempt is aborted after `attemptTimeoutMs`
 *   (30 s). HTTP 429/502/503/504, network failures and timeouts are retried
 *   with exponential backoff and full jitter (honouring Retry-After), inside
 *   a `totalBudgetMs` (45 s) budget counted from when the request leaves the
 *   queue. Other statuses fail at once.
 * - Cancellation: each caller passes an AbortSignal (wired to Excel's
 *   onCanceled). A cancelled caller stops waiting at once; when the last
 *   caller of a request cancels, a queued request leaves the queue and a
 *   running one is aborted, freeing its slot.
 * - The token is sent only to https:// or loopback base URLs (url.ts), never
 *   follows a redirect, and is redacted from every error this client throws.
 */

import { canonicalJson } from "./canonical";
import {
  AddinError,
  CancelledError,
  ConfigError,
  badResponseError,
  errorForHttpStatus,
  timeoutError,
  unreachableError,
} from "./errors";
import { redact } from "./redact";
import type { Settings } from "./settings";
import { normaliseBaseUrl } from "./url";

export const RETRYABLE_STATUS = new Set([429, 502, 503, 504]);

export interface ClientOptions {
  settings: () => Settings | Promise<Settings>;
  fetch?: typeof fetch;
  maxConcurrent?: number;
  attemptTimeoutMs?: number;
  totalBudgetMs?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  random?: () => number;
  now?: () => number;
}

interface Entry {
  key: string;
  method: "GET" | "POST";
  path: string;
  body: string | undefined;
  subscribers: number;
  state: "queued" | "running" | "done";
  controller: AbortController;
  promise: Promise<unknown>;
  resolve: (v: unknown) => void;
  reject: (e: unknown) => void;
}

type Attempt =
  | { kind: "response"; status: number; type: string; retryAfter: string | null; text: string }
  | { kind: "network" }
  | { kind: "timeout" }
  | { kind: "cancelled" };

function abortableSleep(ms: number, signal: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve(false);
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve(true);
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      resolve(false);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export function parseRetryAfter(value: string | null, now: number): number | undefined {
  if (!value) return undefined;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;
  const at = Date.parse(value);
  return Number.isNaN(at) ? undefined : Math.max(0, at - now);
}

export class GatewayClient {
  readonly maxConcurrent: number;
  readonly attemptTimeoutMs: number;
  readonly totalBudgetMs: number;
  private readonly baseDelayMs: number;
  private readonly maxDelayMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly random: () => number;
  private readonly now: () => number;
  private readonly inflight = new Map<string, Entry>();
  private readonly queue: Entry[] = [];
  private running = 0;
  private lastToken = "";

  constructor(private readonly options: ClientOptions) {
    this.maxConcurrent = options.maxConcurrent ?? 6;
    this.attemptTimeoutMs = options.attemptTimeoutMs ?? 30_000;
    this.totalBudgetMs = options.totalBudgetMs ?? 45_000;
    this.baseDelayMs = options.baseDelayMs ?? 500;
    this.maxDelayMs = options.maxDelayMs ?? 8_000;
    this.fetchImpl = options.fetch ?? ((...args) => globalThis.fetch(...args));
    this.random = options.random ?? Math.random;
    this.now = options.now ?? Date.now;
  }

  /** Requests currently running and waiting; for tests and the task pane. */
  stats(): { running: number; queued: number; inflight: number } {
    return { running: this.running, queued: this.queue.length, inflight: this.inflight.size };
  }

  /** The token last used, so callers can redact their own error strings too. */
  secrets(): string[] {
    return [this.lastToken];
  }

  callTool(tool: string, args: Record<string, unknown>, signal?: AbortSignal): Promise<unknown> {
    if (!/^[a-z][a-z0-9_]*$/.test(tool)) return Promise.reject(new ConfigError("Invalid tool name"));
    return this.request("POST", `/gateway/${tool}`, args, signal);
  }

  getJson(path: string, signal?: AbortSignal): Promise<unknown> {
    return this.request("GET", path, undefined, signal);
  }

  private request(method: "GET" | "POST", path: string, args: unknown, signal?: AbortSignal): Promise<unknown> {
    if (signal?.aborted) return Promise.reject(new CancelledError());
    const body = method === "POST" ? canonicalJson(args ?? {}) : undefined;
    const key = `${method} ${path} ${body ?? ""}`;
    let entry = this.inflight.get(key);
    if (!entry) {
      entry = this.enqueue(key, method, path, body);
    }
    const shared = entry;
    shared.subscribers += 1;

    return new Promise((resolve, reject) => {
      let settled = false;
      const onAbort = () => {
        if (settled) return;
        settled = true;
        reject(new CancelledError());
        this.unsubscribe(shared);
      };
      signal?.addEventListener("abort", onAbort, { once: true });
      shared.promise.then(
        (value) => {
          signal?.removeEventListener("abort", onAbort);
          if (!settled) {
            settled = true;
            resolve(value);
          }
        },
        (error) => {
          signal?.removeEventListener("abort", onAbort);
          if (!settled) {
            settled = true;
            reject(error);
          }
        },
      );
    });
  }

  private enqueue(key: string, method: "GET" | "POST", path: string, body: string | undefined): Entry {
    let resolve!: (v: unknown) => void;
    let reject!: (e: unknown) => void;
    const promise = new Promise<unknown>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    promise.catch(() => undefined); // settled even when every caller has left
    const entry: Entry = {
      key, method, path, body, subscribers: 0, state: "queued",
      controller: new AbortController(), promise, resolve, reject,
    };
    this.inflight.set(key, entry);
    this.queue.push(entry);
    void Promise.resolve().then(() => this.pump());
    return entry;
  }

  private unsubscribe(entry: Entry): void {
    entry.subscribers -= 1;
    if (entry.subscribers > 0 || entry.state === "done") return;
    if (this.inflight.get(entry.key) === entry) this.inflight.delete(entry.key);
    if (entry.state === "queued") {
      const i = this.queue.indexOf(entry);
      if (i >= 0) this.queue.splice(i, 1);
      entry.state = "done";
      entry.reject(new CancelledError());
    } else {
      entry.controller.abort(); // the running loop sees this and frees its slot
    }
  }

  private pump(): void {
    while (this.running < this.maxConcurrent && this.queue.length > 0) {
      const entry = this.queue.shift()!;
      entry.state = "running";
      this.running += 1;
      this.execute(entry)
        .then(entry.resolve, (error) => entry.reject(this.sanitise(error)))
        .finally(() => {
          entry.state = "done";
          this.running -= 1;
          if (this.inflight.get(entry.key) === entry) this.inflight.delete(entry.key);
          this.pump();
        });
    }
  }

  /** Every error leaving the client is an AddinError with the token redacted. */
  private sanitise(error: unknown): AddinError {
    const secrets = [this.lastToken];
    if (error instanceof AddinError) {
      const clean = redact(error.message, secrets);
      if (clean === error.message) return error;
      const copy = new AddinError(error.kind, error.code, clean);
      Object.setPrototypeOf(copy, Object.getPrototypeOf(error));
      return copy;
    }
    const message = error instanceof Error ? error.message : String(error);
    return new AddinError("value", "CLIENT", redact(`Add-in error: ${message}`, secrets));
  }

  private async execute(entry: Entry): Promise<unknown> {
    const signal = entry.controller.signal;
    const settings = await this.options.settings();
    const token = (settings.token ?? "").trim();
    this.lastToken = token || this.lastToken;
    if (!token) throw new ConfigError("Set the gateway token in the task pane");
    const base = normaliseBaseUrl(settings.baseUrl);
    const url = `${base}${entry.path}`;
    const headers: Record<string, string> = { Authorization: `Bearer ${token}`, Accept: "application/json" };
    if (entry.body !== undefined) headers["Content-Type"] = "application/json";
    const init: RequestInit = {
      method: entry.method,
      headers,
      body: entry.body,
      redirect: "manual", // never carry the token to wherever a redirect points
      credentials: "omit",
      cache: "no-store",
    };

    const deadline = this.now() + this.totalBudgetMs;
    let lastError: AddinError = timeoutError();
    for (let attempt = 0; ; attempt += 1) {
      if (signal.aborted) throw new CancelledError();
      const remaining = deadline - this.now();
      if (remaining <= 0) throw lastError;
      const outcome = await this.attempt(url, init, Math.min(this.attemptTimeoutMs, remaining), signal);
      let retryAfterMs: number | undefined;
      switch (outcome.kind) {
        case "cancelled":
          throw new CancelledError();
        case "timeout":
          lastError = timeoutError();
          break;
        case "network":
          lastError = unreachableError();
          break;
        case "response":
          if (outcome.type === "opaqueredirect" || (outcome.status >= 300 && outcome.status < 400)) {
            throw new ConfigError("The gateway redirected; set its final https:// address in the task pane");
          }
          if (outcome.status >= 200 && outcome.status < 300) {
            try {
              return JSON.parse(outcome.text);
            } catch {
              throw badResponseError("the body is not JSON");
            }
          }
          if (!RETRYABLE_STATUS.has(outcome.status)) throw errorForHttpStatus(outcome.status);
          lastError = errorForHttpStatus(outcome.status);
          retryAfterMs = parseRetryAfter(outcome.retryAfter, this.now());
          break;
      }
      const backoff = this.random() * Math.min(this.maxDelayMs, this.baseDelayMs * 2 ** attempt);
      const delay = Math.max(backoff, retryAfterMs ?? 0);
      if (this.now() + delay >= deadline) throw lastError;
      if (!(await abortableSleep(delay, signal))) throw new CancelledError();
    }
  }

  private async attempt(url: string, init: RequestInit, timeoutMs: number, outer: AbortSignal): Promise<Attempt> {
    const controller = new AbortController();
    let timedOut = false;
    const onOuterAbort = () => controller.abort();
    outer.addEventListener("abort", onOuterAbort, { once: true });
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    // Settle on abort even if a fetch implementation ignores its signal.
    const aborted = new Promise<never>((_, reject) => {
      controller.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
    });
    aborted.catch(() => undefined);
    try {
      const response = await Promise.race([this.fetchImpl(url, { ...init, signal: controller.signal }), aborted]);
      const text =
        response.status >= 200 && response.status < 300 ? await Promise.race([response.text(), aborted]) : "";
      return {
        kind: "response",
        status: response.status,
        type: response.type,
        retryAfter: response.headers?.get?.("retry-after") ?? null,
        text,
      };
    } catch {
      if (outer.aborted) return { kind: "cancelled" };
      if (timedOut) return { kind: "timeout" };
      return { kind: "network" };
    } finally {
      clearTimeout(timer);
      outer.removeEventListener("abort", onOuterAbort);
    }
  }
}
