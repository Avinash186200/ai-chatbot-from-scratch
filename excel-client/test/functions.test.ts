import { FUNCTION_IDS, createFunctions, registerFunctions, type Invocation } from "../src/functions/functions";
import metadata from "../src/functions/functions.json";
import { BASE, deferred, flush, json, makeClient, mockFetch, okEnvelope, refusal } from "./helpers";

function setup(handler: Parameters<typeof mockFetch>[0]) {
  const mock = mockFetch(handler);
  const client = makeClient(mock.fn);
  return { ...mock, client, fns: createFunctions(client) };
}

const body = (init: RequestInit) => JSON.parse(init.body as string);

describe("=AA.FORECAST(series, horizon, [asOf])", () => {
  test("sends the server's argument names, converting a date serial", async () => {
    const { fns, calls } = setup(() => json(okEnvelope({ path: [{ step: 1, point: 3 }] })));
    const grid = await fns.forecast("rate_long", 5, 45107);
    expect(calls[0].url).toBe(`${BASE}/gateway/forecast_series`);
    expect(body(calls[0].init)).toEqual({ series: "RATE_LONG", horizon: 5, as_of: "2023-06-30" });
    expect(grid[0]).toEqual(["step", "point"]);
    expect(grid.some((r) => r[0] === "snapshot_id")).toBe(true);
  });

  test("an omitted asOf is not sent (the server uses the latest data)", async () => {
    const { fns, calls } = setup(() => json(okEnvelope({ x: 1 })));
    await fns.forecast("RATE_LONG", 5, null);
    await fns.forecast("RATE_LONG", 5, undefined);
    await fns.forecast("RATE_LONG", 5, "");
    expect(calls.map((c) => body(c.init))).toEqual(Array(3).fill({ series: "RATE_LONG", horizon: 5 }));
  });

  test.each([
    ["RATE_LONG", 5.5, undefined],
    ["RATE_LONG", "5", undefined],
    ["", 5, undefined],
    ["RATE LONG", 5, undefined],
    [42, 5, undefined],
    ["RATE_LONG", 5, "30/06/2023"],
    ["RATE_LONG", 5, 60],
  ])("bad input (%p, %p, %p) is #VALUE! without a request", async (series, horizon, asOf) => {
    const { fns, calls } = setup(() => json({}));
    const err = await fns.forecast(series, horizon, asOf).catch((e) => e);
    expect(err.code).toBe("#VALUE!");
    expect(calls).toHaveLength(0);
  });

  test("refusal codes become Excel errors", async () => {
    const na = setup(() => json(refusal("SNAPSHOT_UNAVAILABLE", "no data published")));
    await expect(na.fns.forecast("RATE_LONG", 5, "2019-01-02")).rejects.toMatchObject({ code: "#N/A" });
    const bad = setup(() => json(refusal("UNSUPPORTED_HORIZON", "horizon must be between 1 and 20")));
    const err = await bad.fns.forecast("RATE_LONG", 50).catch((e) => e);
    expect(err.code).toBe("#VALUE!");
    expect(err.message).toContain("UNSUPPORTED_HORIZON");
  });
});

describe("=AA.REGIME(series, [asOf])", () => {
  test("posts series and as_of to detect_regime", async () => {
    const { fns, calls } = setup(() => json(okEnvelope({ series: "POLICY_RATE", regime: "on_hold" })));
    const grid = await fns.regime("POLICY_RATE", "2023-06-30");
    expect(calls[0].url).toBe(`${BASE}/gateway/detect_regime`);
    expect(body(calls[0].init)).toEqual({ series: "POLICY_RATE", as_of: "2023-06-30" });
    expect(grid[1]).toEqual(["regime", "on_hold"]);
  });
});

describe("=AA.TOOLS()", () => {
  test("spills the tool list from the OpenAPI document", async () => {
    const { fns, calls } = setup(() =>
      json({ paths: { "/health": { post: { operationId: "health", description: "Liveness." } } } }),
    );
    expect(await fns.tools()).toEqual([
      ["tool", "description"],
      ["health", "Liveness."],
    ]);
    expect(calls[0].url).toBe(`${BASE}/gateway/openapi.json`);
    expect(calls[0].init.method).toBe("GET");
  });
});

describe("Excel invocation cancellation", () => {
  test("onCanceled aborts the request", async () => {
    const gate = deferred<Response>();
    const { fns, calls } = setup(() => gate.promise);
    const invocation: Invocation = {};
    const p = fns.forecast("RATE_LONG", 5, undefined, invocation);
    await flush();
    expect(typeof invocation.onCanceled).toBe("function");
    invocation.onCanceled!();
    await expect(p).rejects.toBeDefined();
    expect(calls[0].init.signal!.aborted).toBe(true);
  });
});

describe("registration", () => {
  test("associates every id in functions.json", () => {
    const api = (globalThis as unknown as { CustomFunctions: { associate: jest.Mock } }).CustomFunctions;
    api.associate.mockClear();
    const { fns } = setup(() => json({}));
    expect(registerFunctions(fns)).toBe(true);
    const associated = api.associate.mock.calls.map((c) => c[0]).sort();
    expect(associated).toEqual(metadata.functions.map((f) => f.id).sort());
    expect([...FUNCTION_IDS].sort()).toEqual(associated);
  });

  test("metadata: every function spills a matrix and is cancelable", () => {
    for (const f of metadata.functions) {
      expect(f.result.dimensionality).toBe("matrix");
      expect(f.options.cancelable).toBe(true);
    }
    const forecast = metadata.functions.find((f) => f.id === "FORECAST")!;
    expect(forecast.parameters.map((p) => p.name)).toEqual(["series", "horizon", "asOf"]);
    expect(forecast.parameters[2]).toMatchObject({ optional: true, type: "any" });
  });
});
