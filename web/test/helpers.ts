import type { AgentEvent } from "../src/lib/types";

export function sseBody(events: Partial<AgentEvent>[], chunkSize = 17): ReadableStream<Uint8Array> {
  const text = events
    .map((e, i) => `event: message\nid: ${i + 1}\ndata: ${JSON.stringify({ v: 1, seq: i + 1, run_id: "r", thread_id: "thread-0001", ts: "t", data: {}, ...e })}\n\n`)
    .join("");
  const bytes = new TextEncoder().encode(text);
  let offset = 0;
  return new ReadableStream({
    pull(controller) {
      if (offset >= bytes.length) {
        controller.close();
        return;
      }
      // Odd chunk sizes split lines and multi-byte characters on purpose.
      controller.enqueue(bytes.slice(offset, offset + chunkSize));
      offset += chunkSize;
    },
  });
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export const PROVENANCE = {
  model_id: "ar1-placeholder",
  model_version: "1.1.0",
  as_of: "2023-06-30",
  snapshot_id: "snap-1a2b3c4d",
  computed_at: "2025-03-03T12:00:00+00:00",
};

export const FORECAST_ENVELOPE = {
  tool: "forecast_series",
  ok: true,
  payload: { result: { series: "RATE_LONG", horizon: 5 } },
  provenance: PROVENANCE,
};

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>;

/** A fetch mock that routes by "METHOD path" and records every call. */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fn = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    calls.push({ url, init });
    const method = (init.method ?? "GET").toUpperCase();
    const path = url.split("?")[0];
    const handler = routes[`${method} ${path}`];
    if (!handler) return json({ detail: "Not Found" }, 404);
    return handler(url, init);
  };
  return { fetch: fn as unknown as typeof fetch, calls };
}
