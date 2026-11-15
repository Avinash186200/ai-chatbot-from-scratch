// Client for the server's /api routes. All URLs are relative: the UI is
// served from the same origin as the API (Vite proxy in development, nginx
// in Docker), so the bearer token never goes to another host.

import { SSEParser } from "./sse";
import type { AgentEvent, ServerConfig, Thread, TranscriptItem } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiOptions {
  token: string;
  user: string;
  fetchImpl?: typeof fetch;
}

function headers(token: string, json = false): HeadersInit {
  const h: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (json) h["Content-Type"] = "application/json";
  return h;
}

async function failure(resp: Response): Promise<ApiError> {
  // Only the status and the server's short "detail" are shown. The server
  // never echoes the token, and we never put it into a message ourselves.
  let detail = "";
  try {
    const body = (await resp.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail.slice(0, 200);
  } catch {
    /* not JSON */
  }
  if (resp.status === 401) return new ApiError("The server rejected the token.", 401);
  return new ApiError(detail ? `HTTP ${resp.status}: ${detail}` : `HTTP ${resp.status}`, resp.status);
}

export class ApiClient {
  private readonly fetchImpl: typeof fetch;

  constructor(private readonly opts: ApiOptions) {
    this.fetchImpl = opts.fetchImpl ?? ((...args) => fetch(...args));
  }

  private query(): string {
    return `user=${encodeURIComponent(this.opts.user)}`;
  }

  async config(): Promise<ServerConfig> {
    const resp = await this.fetchImpl("/api/config", { headers: headers(this.opts.token) });
    if (!resp.ok) throw await failure(resp);
    return (await resp.json()) as ServerConfig;
  }

  async listThreads(): Promise<Thread[]> {
    const resp = await this.fetchImpl(`/api/threads?${this.query()}`, { headers: headers(this.opts.token) });
    if (!resp.ok) throw await failure(resp);
    return ((await resp.json()) as { threads: Thread[] }).threads;
  }

  async createThread(title?: string): Promise<Thread> {
    const resp = await this.fetchImpl("/api/threads", {
      method: "POST",
      headers: headers(this.opts.token, true),
      body: JSON.stringify({ user: this.opts.user, title }),
    });
    if (!resp.ok) throw await failure(resp);
    return (await resp.json()) as Thread;
  }

  async getThread(threadId: string): Promise<Thread & { messages: TranscriptItem[] }> {
    const resp = await this.fetchImpl(`/api/threads/${encodeURIComponent(threadId)}?${this.query()}`, {
      headers: headers(this.opts.token),
    });
    if (!resp.ok) throw await failure(resp);
    return (await resp.json()) as Thread & { messages: TranscriptItem[] };
  }

  /**
   * POST /api/chat and call onEvent for every streamed agent event.
   * Resolves with the thread id when the stream ends.
   */
  async chat(
    message: string,
    threadId: string | null,
    onEvent: (event: AgentEvent) => void,
    signal?: AbortSignal,
  ): Promise<string | null> {
    const resp = await this.fetchImpl("/api/chat", {
      method: "POST",
      headers: { ...headers(this.opts.token, true), Accept: "text/event-stream" },
      body: JSON.stringify({ message, thread_id: threadId ?? undefined, user: this.opts.user }),
      signal,
    });
    if (!resp.ok) throw await failure(resp);
    if (!resp.body) throw new ApiError("The server sent no stream.", resp.status);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    const parser = new SSEParser();
    const emit = (raw: string) => {
      try {
        onEvent(JSON.parse(raw) as AgentEvent);
      } catch {
        /* a malformed event is skipped, not rendered */
      }
    };
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const m of parser.push(decoder.decode(value, { stream: true }))) emit(m.data);
    }
    for (const m of parser.push(decoder.decode())) emit(m.data);
    for (const m of parser.end()) emit(m.data);
    return resp.headers.get("X-Thread-Id");
  }
}
