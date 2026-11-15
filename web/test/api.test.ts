import { describe, expect, it } from "vitest";
import { ApiClient, ApiError } from "../src/lib/api";
import type { AgentEvent } from "../src/lib/types";
import { FORECAST_ENVELOPE, json, mockFetch, sseBody } from "./helpers";

const TOKEN = "tok-secret-value";

describe("ApiClient", () => {
  it("streams chat events in order and returns the thread id", async () => {
    const { fetch, calls } = mockFetch({
      "POST /api/chat": () =>
        new Response(
          sseBody([
            { type: "run.started", data: { message: "q" } },
            { type: "tool.called", data: { id: "c1", name: "forecast_series", arguments: { horizon: 5 } } },
            { type: "tool.result", data: { id: "c1", name: "forecast_series", ok: true, envelope: FORECAST_ENVELOPE } },
            { type: "message.completed", data: { content: "RATE_LONG at 3.1 — as of 2023-06-30" } },
            { type: "run.finished", data: { status: "completed" } },
          ], 7),
          { headers: { "Content-Type": "text/event-stream", "X-Thread-Id": "thread-0001" } },
        ),
    });
    const events: AgentEvent[] = [];
    const client = new ApiClient({ token: TOKEN, user: "ana", fetchImpl: fetch });
    const threadId = await client.chat("q", null, (e) => events.push(e));
    expect(threadId).toBe("thread-0001");
    expect(events.map((e) => e.type)).toEqual(["run.started", "tool.called", "tool.result", "message.completed", "run.finished"]);
    expect(events[3].data.content).toBe("RATE_LONG at 3.1 — as of 2023-06-30");
    const init = calls[0].init;
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    expect(JSON.parse(String(init.body))).toEqual({ message: "q", user: "ana" });
    expect(calls[0].url).toBe("/api/chat"); // same origin, relative URL
  });

  it("sends the thread id when resuming", async () => {
    const { fetch, calls } = mockFetch({
      "POST /api/chat": () => new Response(sseBody([{ type: "run.finished" }])),
    });
    await new ApiClient({ token: TOKEN, user: "u", fetchImpl: fetch }).chat("next", "thread-0001", () => {});
    expect(JSON.parse(String(calls[0].init.body)).thread_id).toBe("thread-0001");
  });

  it("lists, creates and fetches threads for the user label", async () => {
    const thread = { thread_id: "t-00000001", user: "a b", title: "x", created_at: "c", updated_at: "u" };
    const { fetch, calls } = mockFetch({
      "GET /api/threads": () => json({ threads: [thread] }),
      "POST /api/threads": () => json(thread, 201),
      "GET /api/threads/t-00000001": () => json({ ...thread, messages: [] }),
    });
    const client = new ApiClient({ token: TOKEN, user: "a b", fetchImpl: fetch });
    expect(await client.listThreads()).toEqual([thread]);
    expect((await client.createThread()).thread_id).toBe("t-00000001");
    expect((await client.getThread("t-00000001")).messages).toEqual([]);
    expect(calls[0].url).toBe("/api/threads?user=a%20b");
    expect(calls[2].url).toBe("/api/threads/t-00000001?user=a%20b");
  });

  it("maps 401 to a token message that never contains the token", async () => {
    const { fetch } = mockFetch({ "GET /api/threads": () => json({ detail: `bad ${TOKEN}` }, 401) });
    const err = await new ApiClient({ token: TOKEN, user: "u", fetchImpl: fetch }).listThreads().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(err.message).not.toContain(TOKEN);
  });

  it("shows the server's detail for other errors", async () => {
    const { fetch } = mockFetch({ "POST /api/chat": () => json({ detail: "message is too long" }, 413) });
    const err = await new ApiClient({ token: TOKEN, user: "u", fetchImpl: fetch }).chat("x", null, () => {}).catch((e) => e);
    expect(err.message).toBe("HTTP 413: message is too long");
  });

  it("skips malformed events instead of failing the stream", async () => {
    const body = new TextEncoder().encode('data: {not json}\n\ndata: {"type":"run.finished","seq":2,"data":{}}\n\n');
    const { fetch } = mockFetch({ "POST /api/chat": () => new Response(body) });
    const types: string[] = [];
    await new ApiClient({ token: TOKEN, user: "u", fetchImpl: fetch }).chat("x", null, (e) => types.push(e.type));
    expect(types).toEqual(["run.finished"]);
  });
});
