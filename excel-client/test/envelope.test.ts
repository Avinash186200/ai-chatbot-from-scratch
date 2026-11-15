import { AddinError } from "../src/lib/errors";
import { PROVENANCE_FIELDS, rectangular, resultTable, shapeEnvelope, toolList } from "../src/lib/envelope";
import { okEnvelope, refusal } from "./helpers";

const forecast = {
  series: "RATE_LONG",
  last_observation: "2023-06-29",
  last_value: 3.4444,
  horizon: 2,
  phi: 0.1,
  path: [
    { step: 1, point: 3.45, lower_90: 3.3, upper_90: 3.6 },
    { step: 2, point: 3.46, lower_90: 3.25, upper_90: 3.67 },
  ],
};

describe("shapeEnvelope", () => {
  test("result table, blank row, provenance rows", () => {
    const grid = shapeEnvelope(okEnvelope(forecast));
    expect(grid).toEqual([
      ["step", "point", "lower_90", "upper_90"],
      [1, 3.45, 3.3, 3.6],
      [2, 3.46, 3.25, 3.67],
      ["series", "RATE_LONG", "", ""],
      ["last_observation", "2023-06-29", "", ""],
      ["last_value", 3.4444, "", ""],
      ["horizon", 2, "", ""],
      ["phi", 0.1, "", ""],
      ["", "", "", ""],
      ["model_id", "placeholder-ar1", "", ""],
      ["model_version", "1.1.0", "", ""],
      ["as_of", "2023-06-30", "", ""],
      ["snapshot_id", "snap-0123456789abcdef", "", ""],
      ["computed_at", "2025-03-03T12:00:00+00:00", "", ""],
    ]);
  });

  test("a flat result becomes name/value rows", () => {
    const grid = shapeEnvelope(okEnvelope({ series: "POLICY_RATE", regime: "on_hold", net_change: 0 }));
    expect(grid.slice(0, 4)).toEqual([
      ["series", "POLICY_RATE"],
      ["regime", "on_hold"],
      ["net_change", 0],
      ["", ""],
    ]);
    expect(grid.slice(4).map((r) => r[0])).toEqual([...PROVENANCE_FIELDS]);
  });

  test("missing provenance fields are blank, never 'undefined' or 'null'", () => {
    const env = { ok: true, payload: { result: { x: 1 } }, provenance: null };
    const grid = shapeEnvelope(env);
    expect(grid.slice(-5).map((r) => r[1])).toEqual(["", "", "", "", ""]);
    expect(JSON.stringify(grid)).not.toMatch(/undefined|null/);
  });

  test("every row has the same width", () => {
    const grid = shapeEnvelope(okEnvelope(forecast));
    expect(new Set(grid.map((r) => r.length)).size).toBe(1);
  });

  test("a refusal throws the mapped error", () => {
    expect(() => shapeEnvelope(refusal("SNAPSHOT_UNAVAILABLE", "no data"))).toThrow(AddinError);
    try {
      shapeEnvelope(refusal("SNAPSHOT_UNAVAILABLE", "no data"));
    } catch (e) {
      expect((e as AddinError).kind).toBe("na");
      expect((e as AddinError).message).toBe("SNAPSHOT_UNAVAILABLE: no data");
    }
  });

  test.each([null, "text", { ok: "yes" }, { ok: true }, { ok: true, payload: {} }, { ok: false, payload: {} }])(
    "malformed body %p is a #VALUE! BAD_RESPONSE",
    (body) => {
      try {
        shapeEnvelope(body);
        throw new Error("expected a throw");
      } catch (e) {
        expect(e).toBeInstanceOf(AddinError);
        expect((e as AddinError).code).toBe("BAD_RESPONSE");
        expect((e as AddinError).kind).toBe("value");
      }
    },
  );
});

describe("resultTable", () => {
  test("cells: null to blank, nested values to JSON, non-finite numbers to blank", () => {
    expect(resultTable({ a: null, b: { c: 1 }, d: [1, 2], e: NaN })).toEqual([
      ["a", ""],
      ["b", '{"c":1}'],
      ["d", "[1,2]"],
      ["e", ""],
    ]);
  });

  test("array of records with differing keys", () => {
    expect(resultTable([{ a: 1 }, { b: 2 }])).toEqual([
      ["a", "b"],
      [1, ""],
      ["", 2],
    ]);
  });

  test("empty array and scalars", () => {
    expect(resultTable([])).toEqual([["(no rows)"]]);
    expect(resultTable(5)).toEqual([[5]]);
    expect(resultTable(["x", "y"])).toEqual([["x"], ["y"]]);
  });

  test("rectangular pads short rows", () => {
    expect(rectangular([[1, 2, 3], [], ["a"]])).toEqual([
      [1, 2, 3],
      ["", "", ""],
      ["a", "", ""],
    ]);
  });
});

describe("toolList", () => {
  test("reads operationId and description from the OpenAPI paths", () => {
    const spec = {
      openapi: "3.1.0",
      paths: {
        "/health": { post: { operationId: "health", description: "Server liveness." } },
        "/forecast_series": { post: { operationId: "forecast_series", description: "Forecast." } },
      },
    };
    expect(toolList(spec)).toEqual([
      ["tool", "description"],
      ["health", "Server liveness."],
      ["forecast_series", "Forecast."],
    ]);
  });

  test("rejects a non-OpenAPI body", () => {
    expect(() => toolList({ tools: [] })).toThrow(AddinError);
  });
});
