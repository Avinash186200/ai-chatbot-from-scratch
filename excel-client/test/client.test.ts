import { CancelledError, type AddinError } from "../src/lib/errors";
import { parseRetryAfter } from "../src/lib/client";
import { BASE, TOKEN, deferred, flush, hangingFetch, json, makeClient, mockFetch, okEnvelope, status } from "./helpers";

afterEach(() => {
  jest.useRealTimers();
});

describe("request shape", () => {
  test("POST /gateway/<tool> with bearer token, JSON body and no redirects", async () => {
    const { fn, calls } = mockFetch(() => json(okEnvelope({ x: 1 })));
    const client = makeClient(fn);
    await client.callTool("forecast_series", { series: "RATE_LONG", horizon: 5, as_of: "2023-06-30" });
    expect(calls).toHaveLength(1);
    const { url, init } = calls[0];
    expect(url).toBe(`${BASE}/gateway/forecast_series`);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({ as_of: "2023-06-30", horizon: 5, series: "RATE_LONG" });
    expect(init.redirect).toBe("manual");
    expect(init.credentials).toBe("omit");
  });

  test("a trailing slash or path prefix on the base URL is handled", async () => {
    const { fn, calls } = mockFetch(() => json({ paths: {} }));
    const client = makeClient(fn, { settings: () => ({ baseUrl: "https://host.example.test/aa/", token: TOKEN }) });
    await client.getJson("/gateway/openapi.json");
    expect(calls[0].url).toBe("https://host.example.test/aa/gateway/openapi.json");
    expect(calls[0].init.method).toBe("GET");
    expect(calls[0].init.body).toBeUndefined();
  });

  test("an invalid tool name never reaches fetch", async () => {
    const { fn, calls } = mockFetch(() => json({}));
    await expect(makeClient(fn).callTool("../admin", {})).rejects.toMatchObject({ code: "CONFIG" });
    expect(calls).toHaveLength(0);
  });

  test("a redirect is refused, not followed and not retried", async () => {
    const redirect = { status: 0, type: "opaqueredirect", headers: new Headers(), text: async () => "" };
    const { fn, calls } = mockFetch(() => redirect as unknown as Response);
    await expect(makeClient(fn).callTool("health", {})).rejects.toMatchObject({ code: "CONFIG", kind: "value" });
    expect(calls).toHaveLength(1);
  });

  test("a non-JSON 200 body is #VALUE! BAD_RESPONSE", async () => {
    const { fn } = mockFetch(() => new Response("<html>proxy login</html>", { status: 200 }));
    await expect(makeClient(fn).callTool("health", {})).rejects.toMatchObject({ code: "BAD_RESPONSE", kind: "value" });
  });
});

describe("dedup of identical in-flight requests", () => {
  test("same tool and canonical args share one fetch", async () => {
    const gate = deferred<Response>();
    const { fn, calls } = mockFetch(() => gate.promise);
    const client = makeClient(fn);
    const a = client.callTool("forecast_series", { series: "RATE_LONG", horizon: 5 });
    const b = client.callTool("forecast_series", { horizon: 5, series: "RATE_LONG" });
    const c = client.callTool("forecast_series", { horizon: 5, series: "RATE_LONG", as_of: undefined });
    await flush();
    expect(calls).toHaveLength(1);
    gate.resolve(json(okEnvelope({ v: 1 })));
    const results = await Promise.all([a, b, c]);
    expect(results[0]).toEqual(results[1]);
    expect(results[1]).toEqual(results[2]);
  });

  test("different args, different tools or a different method are separate", async () => {
    const { fn, calls } = mockFetch(() => json(okEnvelope({})));
    const client = makeClient(fn);
    await Promise.all([
      client.callTool("forecast_series", { series: "RATE_LONG", horizon: 5 }),
      client.callTool("forecast_series", { series: "RATE_LONG", horizon: 6 }),
      client.callTool("detect_regime", { series: "RATE_LONG" }),
      client.getJson("/gateway/openapi.json"),
    ]);
    expect(calls).toHaveLength(4);
  });

  test("nothing is cached once a request settles", async () => {
    const { fn, calls } = mockFetch(() => json(okEnvelope({})));
    const client = makeClient(fn);
    await client.callTool("health", {});
    await client.callTool("health", {});
    expect(calls).toHaveLength(2);
    expect(client.stats()).toEqual({ running: 0, queued: 0, inflight: 0 });
  });

  test("a shared failure reaches every caller", async () => {
    const { fn, calls } = mockFetch(() => status(500));
    const client = makeClient(fn);
    const results = await Promise.allSettled([client.callTool("health", {}), client.callTool("health", {})]);
    expect(calls).toHaveLength(1);
    expect(results.map((r) => r.status)).toEqual(["rejected", "rejected"]);
  });
});

describe("concurrency gate", () => {
  test("at most 6 distinct requests in flight; the rest queue in order", async () => {
    const gates = Array.from({ length: 10 }, () => deferred<Response>());
    const { fn, calls } = mockFetch((_, i) => gates[i].promise);
    const client = makeClient(fn);
    const all = Array.from({ length: 10 }, (_, i) => client.callTool("forecast_series", { horizon: i + 1 }));
    await flush();
    expect(calls).toHaveLength(6);
    expect(client.stats()).toEqual({ running: 6, queued: 4, inflight: 10 });

    gates[0].resolve(json(okEnvelope({})));
    await all[0];
    await flush();
    expect(calls).toHaveLength(7);
    expect(JSON.parse(calls[6].init.body as string).horizon).toBe(7);

    for (const g of gates.slice(1)) g.resolve(json(okEnvelope({})));
    await Promise.all(all);
    expect(calls).toHaveLength(10);
    expect(client.stats()).toEqual({ running: 0, queued: 0, inflight: 0 });
  });

  test("a failed request frees its slot", async () => {
    const { fn, calls } = mockFetch((_, i) => (i === 0 ? status(400) : json(okEnvelope({}))));
    const client = makeClient(fn, { maxConcurrent: 1 });
    const results = await Promise.allSettled([
      client.callTool("forecast_series", { horizon: 1 }),
      client.callTool("forecast_series", { horizon: 2 }),
    ]);
    expect(results.map((r) => r.status)).toEqual(["rejected", "fulfilled"]);
    expect(calls).toHaveLength(2);
  });
});

describe("cancellation", () => {
  test("an already-cancelled call never fetches", async () => {
    const { fn, calls } = mockFetch(() => json({}));
    const ac = new AbortController();
    ac.abort();
    await expect(makeClient(fn).callTool("health", {}, ac.signal)).rejects.toBeInstanceOf(CancelledError);
    expect(calls).toHaveLength(0);
  });

  test("a cancelled queued call drops out of the queue and is never fetched", async () => {
    const gates = Array.from({ length: 8 }, () => deferred<Response>());
    const { fn, calls } = mockFetch((_, i) => gates[i].promise);
    const client = makeClient(fn);
    const running = Array.from({ length: 6 }, (_, i) => client.callTool("forecast_series", { horizon: i + 1 }));
    const ac = new AbortController();
    const cancelled = client.callTool("forecast_series", { horizon: 7 }, ac.signal);
    const later = client.callTool("forecast_series", { horizon: 8 });
    await flush();
    expect(client.stats().queued).toBe(2);

    ac.abort();
    await expect(cancelled).rejects.toBeInstanceOf(CancelledError);
    expect(client.stats().queued).toBe(1);

    gates[0].resolve(json(okEnvelope({})));
    await running[0];
    await flush();
    const horizons = calls.map((c) => JSON.parse(c.init.body as string).horizon);
    expect(horizons).toEqual([1, 2, 3, 4, 5, 6, 8]);
    for (const g of gates.slice(1)) g.resolve(json(okEnvelope({})));
    await Promise.all([...running, later]);
  });

  test("cancelling one of two callers of a shared request leaves the other running", async () => {
    const gate = deferred<Response>();
    const { fn, calls } = mockFetch(() => gate.promise);
    const client = makeClient(fn);
    const ac = new AbortController();
    const first = client.callTool("health", {}, ac.signal);
    const second = client.callTool("health", {});
    await flush();
    ac.abort();
    await expect(first).rejects.toBeInstanceOf(CancelledError);
    expect(calls[0].init.signal!.aborted).toBe(false);
    gate.resolve(json(okEnvelope({ fine: true })));
    await expect(second).resolves.toMatchObject({ ok: true });
  });

  test("cancelling the only caller aborts the fetch and frees the slot", async () => {
    const hang = hangingFetch();
    const second = mockFetch(() => json(okEnvelope({})));
    const fn = ((url: string, init: RequestInit) =>
      String(init.body).includes('"horizon":1') ? hang.fn(url, init) : second.fn(url, init)) as typeof fetch;
    const client = makeClient(fn, { maxConcurrent: 1 });
    const ac = new AbortController();
    const first = client.callTool("forecast_series", { horizon: 1 }, ac.signal);
    const queued = client.callTool("forecast_series", { horizon: 2 });
    await flush();
    expect(client.stats()).toMatchObject({ running: 1, queued: 1 });
    ac.abort();
    await expect(first).rejects.toBeInstanceOf(CancelledError);
    await expect(queued).resolves.toMatchObject({ ok: true });
    expect(hang.calls[0].init.signal!.aborted).toBe(true);
    expect(hang.calls).toHaveLength(1); // not retried
  });

  test("cancelling during a retry backoff stops the retries", async () => {
    jest.useFakeTimers();
    const { fn, calls } = mockFetch(() => status(503));
    const client = makeClient(fn);
    const ac = new AbortController();
    const p = client.callTool("health", {}, ac.signal);
    const check = expect(p).rejects.toBeInstanceOf(CancelledError);
    await jest.advanceTimersByTimeAsync(100); // first attempt failed, now backing off
    ac.abort();
    await check;
    await jest.advanceTimersByTimeAsync(60_000);
    expect(calls).toHaveLength(1);
  });
});

describe("timeouts and retries", () => {
  test("each attempt times out at 30 s and the whole call stops within the 45 s budget", async () => {
    jest.useFakeTimers();
    const { fn, calls } = hangingFetch();
    const client = makeClient(fn);
    const start = Date.now();
    const p = client.callTool("health", {});
    let settledAt = 0;
    const check = expect(
      p.finally(() => {
        settledAt = Date.now();
      }),
    ).rejects.toMatchObject({ code: "TIMEOUT", kind: "na" });

    await jest.advanceTimersByTimeAsync(29_999);
    expect(calls).toHaveLength(1);
    expect(calls[0].init.signal!.aborted).toBe(false);
    await jest.advanceTimersByTimeAsync(1);
    expect(calls[0].init.signal!.aborted).toBe(true);

    await jest.advanceTimersByTimeAsync(20_000);
    await check;
    expect(calls).toHaveLength(2); // one retry, cut short by the budget
    expect(settledAt - start).toBeLessThanOrEqual(45_000);
  });

  test("503 then 200: retried after a jittered backoff", async () => {
    jest.useFakeTimers();
    const { fn, calls } = mockFetch((_, i) => (i === 0 ? status(503) : json(okEnvelope({ v: 2 }))));
    const client = makeClient(fn, { random: () => 0.5 });
    const p = client.callTool("health", {});
    await jest.advanceTimersByTimeAsync(249); // 0.5 * 500 ms
    expect(calls).toHaveLength(1);
    await jest.advanceTimersByTimeAsync(1);
    await expect(p).resolves.toMatchObject({ ok: true });
    expect(calls).toHaveLength(2);
  });

  test.each([429, 502, 503, 504])("HTTP %d is retried", async (code) => {
    jest.useFakeTimers();
    const { fn, calls } = mockFetch((_, i) => (i < 2 ? status(code) : json(okEnvelope({}))));
    const p = makeClient(fn).callTool("health", {});
    await jest.advanceTimersByTimeAsync(5_000);
    await expect(p).resolves.toMatchObject({ ok: true });
    expect(calls).toHaveLength(3);
  });

  test.each([400, 401, 403, 404, 422, 500, 501])("HTTP %d is not retried", async (code) => {
    const { fn, calls } = mockFetch(() => status(code));
    await expect(makeClient(fn).callTool("health", {})).rejects.toMatchObject({ kind: "value" });
    expect(calls).toHaveLength(1);
  });

  test("Retry-After is honoured", async () => {
    jest.useFakeTimers();
    const { fn, calls } = mockFetch((_, i) => (i === 0 ? status(429, { "Retry-After": "2" }) : json(okEnvelope({}))));
    const p = makeClient(fn).callTool("health", {});
    await jest.advanceTimersByTimeAsync(1_999);
    expect(calls).toHaveLength(1);
    await jest.advanceTimersByTimeAsync(1);
    await expect(p).resolves.toMatchObject({ ok: true });
  });

  test("a Retry-After beyond the budget fails at once with the last error", async () => {
    jest.useFakeTimers();
    const { fn, calls } = mockFetch(() => status(503, { "Retry-After": "120" }));
    const p = makeClient(fn).callTool("health", {});
    const check = expect(p).rejects.toMatchObject({ code: "SERVER", kind: "value" });
    await jest.advanceTimersByTimeAsync(10);
    await check;
    expect(calls).toHaveLength(1);
  });

  test("network errors are retried with growing backoff until the budget, then #N/A", async () => {
    jest.useFakeTimers();
    const times: number[] = [];
    const { fn, calls } = mockFetch(() => {
      times.push(Date.now());
      throw new TypeError("fetch failed");
    });
    const start = Date.now();
    const p = makeClient(fn, { random: () => 1 }).callTool("health", {});
    const check = expect(p).rejects.toMatchObject({ code: "UNREACHABLE", kind: "na" });
    await jest.advanceTimersByTimeAsync(45_000);
    await check;
    const gaps = times.slice(1).map((t, i) => t - times[i]);
    expect(gaps.slice(0, 5)).toEqual([500, 1000, 2000, 4000, 8000]); // capped at 8 s after this
    expect(gaps.every((g) => g <= 8000)).toBe(true);
    expect(times[times.length - 1] - start).toBeLessThan(45_000);
    expect(calls.length).toBeGreaterThan(5);
  });

  test("jitter scales the backoff: random() = 0 retries without waiting", async () => {
    const { fn, calls } = mockFetch((_, i) => (i < 3 ? status(503) : json(okEnvelope({}))));
    const started = Date.now();
    await expect(makeClient(fn, { random: () => 0 }).callTool("health", {})).resolves.toMatchObject({ ok: true });
    expect(calls).toHaveLength(4);
    expect(Date.now() - started).toBeLessThan(1000);
  });

  test("parseRetryAfter reads seconds and HTTP dates", () => {
    const now = Date.parse("2025-03-03T12:00:00Z");
    expect(parseRetryAfter("3", now)).toBe(3000);
    expect(parseRetryAfter("Mon, 03 Mar 2025 12:00:05 GMT", now)).toBe(5000);
    expect(parseRetryAfter("soon", now)).toBeUndefined();
    expect(parseRetryAfter(null, now)).toBeUndefined();
  });
});

describe("configuration", () => {
  test("no token: refused before any fetch", async () => {
    const { fn, calls } = mockFetch(() => json({}));
    const client = makeClient(fn, { settings: () => ({ baseUrl: BASE, token: "  " }) });
    await expect(client.callTool("health", {})).rejects.toMatchObject({ code: "CONFIG", kind: "value" });
    expect(calls).toHaveLength(0);
  });

  test("settings are read for every request (a new token applies at once)", async () => {
    const { fn, calls } = mockFetch(() => json(okEnvelope({})));
    let token = "first-token-value";
    const client = makeClient(fn, { settings: async () => ({ baseUrl: BASE, token }) });
    await client.callTool("health", {});
    token = "second-token-value";
    await client.callTool("health", {});
    const auth = calls.map((c) => (c.init.headers as Record<string, string>).Authorization);
    expect(auth).toEqual(["Bearer first-token-value", "Bearer second-token-value"]);
  });

  test("errors are AddinErrors", async () => {
    const { fn } = mockFetch(() => status(404));
    const err = (await makeClient(fn).callTool("health", {}).catch((e) => e)) as AddinError;
    expect(err.name).toBe("AddinError");
    expect(err.code).toBe("NOT_FOUND");
  });
});
