import { toExcelError } from "../src/functions/functions";
import {
  AddinError,
  ENVELOPE_CODE_KIND,
  errorForEnvelope,
  errorForHttpStatus,
  timeoutError,
  unreachableError,
} from "../src/lib/errors";

const excelCode = (e: unknown) => (toExcelError(e, []) as unknown as { code: string }).code;

describe("envelope codes map to Excel errors by code only", () => {
  test.each([
    ["SNAPSHOT_UNAVAILABLE", "#N/A"],
    ["SCHEMA_VIOLATION", "#VALUE!"],
    ["UNSUPPORTED_HORIZON", "#VALUE!"],
    ["MODEL_FAILURE", "#VALUE!"],
    ["INTERNAL", "#VALUE!"],
    ["SOMETHING_NEW", "#VALUE!"],
  ])("%s -> %s", (code, expected) => {
    expect(excelCode(errorForEnvelope(code, "message"))).toBe(expected);
  });

  test("the message text never changes the mapping", () => {
    expect(excelCode(errorForEnvelope("SCHEMA_VIOLATION", "SNAPSHOT_UNAVAILABLE no data"))).toBe("#VALUE!");
    expect(excelCode(errorForEnvelope("SNAPSHOT_UNAVAILABLE", "SCHEMA_VIOLATION bad input"))).toBe("#N/A");
  });

  test("a missing or non-string code is #VALUE!", () => {
    expect(excelCode(errorForEnvelope(undefined, "x"))).toBe("#VALUE!");
    expect(excelCode(errorForEnvelope(42, "x"))).toBe("#VALUE!");
  });

  test("messages are short and single-line", () => {
    const e = errorForEnvelope("INTERNAL", "line one\nline two " + "x".repeat(500));
    expect(e.message.length).toBeLessThanOrEqual(200);
    expect(e.message).not.toContain("\n");
  });

  test("the server's codes are all known", () => {
    expect(Object.keys(ENVELOPE_CODE_KIND).sort()).toEqual(
      ["INTERNAL", "MODEL_FAILURE", "SCHEMA_VIOLATION", "SNAPSHOT_UNAVAILABLE", "UNSUPPORTED_HORIZON"].sort(),
    );
  });
});

describe("transport failures", () => {
  test.each([
    [401, "#VALUE!", "AUTH"],
    [403, "#VALUE!", "AUTH"],
    [404, "#VALUE!", "NOT_FOUND"],
    [422, "#VALUE!", "HTTP"],
    [429, "#VALUE!", "BUSY"],
    [500, "#VALUE!", "SERVER"],
    [502, "#VALUE!", "SERVER"],
    [503, "#VALUE!", "SERVER"],
    [504, "#VALUE!", "SERVER"],
  ])("HTTP %d -> %s", (statusCode, expected, code) => {
    const e = errorForHttpStatus(statusCode);
    expect(e.code).toBe(code);
    expect(excelCode(e)).toBe(expected);
  });

  test("unreachable and timed out are #N/A", () => {
    expect(excelCode(unreachableError())).toBe("#N/A");
    expect(excelCode(timeoutError())).toBe("#N/A");
  });

  test("an unexpected exception is #VALUE!", () => {
    expect(excelCode(new TypeError("boom"))).toBe("#VALUE!");
    expect(excelCode("a string")).toBe("#VALUE!");
  });

  test("errors carry their kind", () => {
    expect(new AddinError("na", "X", "m").kind).toBe("na");
  });
});
