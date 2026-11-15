import { isValidIsoDate, normaliseAsOf, serialToIsoDate } from "../src/lib/dates";
import { InputError } from "../src/lib/errors";

describe("Excel 1900 date serials", () => {
  test.each([
    [1, "1900-01-01"],
    [31, "1900-01-31"],
    [59, "1900-02-28"], // last real day before the fictitious leap day
    [61, "1900-03-01"], // first day after it
    [366, "1900-12-31"],
    [367, "1901-01-01"],
    [36526, "2000-01-01"],
    [44927, "2023-01-01"],
    [45000, "2023-03-15"],
    [45107, "2023-06-30"],
    [45351, "2024-02-29"], // a real leap day
    [2958465, "9999-12-31"],
  ])("serial %d is %s", (serial, iso) => {
    expect(serialToIsoDate(serial)).toBe(iso);
  });

  test("serial 60 is the fictitious 1900-02-29 and is rejected", () => {
    expect(() => serialToIsoDate(60)).toThrow(InputError);
    expect(() => serialToIsoDate(60)).toThrow(/1900-02-29/);
  });

  test("a time of day is ignored", () => {
    expect(serialToIsoDate(45107.75)).toBe("2023-06-30");
    expect(serialToIsoDate(59.999)).toBe("1900-02-28");
  });

  test.each([0, -1, 0.5, 2958466, NaN, Infinity])("serial %p is out of range", (serial) => {
    expect(() => serialToIsoDate(serial)).toThrow(InputError);
  });
});

describe("asOf argument", () => {
  test("omitted or blank means latest", () => {
    expect(normaliseAsOf(undefined)).toBeUndefined();
    expect(normaliseAsOf(null)).toBeUndefined();
    expect(normaliseAsOf("")).toBeUndefined();
    expect(normaliseAsOf("   ")).toBeUndefined();
  });

  test("serial or ISO string", () => {
    expect(normaliseAsOf(45107)).toBe("2023-06-30");
    expect(normaliseAsOf(" 2023-06-30 ")).toBe("2023-06-30");
  });

  test.each(["30/06/2023", "2023-6-30", "2023-02-30", "2023-13-01", "20230630", "2023-06-30T00:00:00", "latest"])(
    "string %p is rejected",
    (text) => {
      expect(() => normaliseAsOf(text)).toThrow(InputError);
    },
  );

  test.each([true, {}, [45107]])("%p is rejected", (value) => {
    expect(() => normaliseAsOf(value)).toThrow(InputError);
  });

  test("ISO validation checks the calendar", () => {
    expect(isValidIsoDate("2024-02-29")).toBe(true);
    expect(isValidIsoDate("2023-02-29")).toBe(false);
    expect(isValidIsoDate("1900-02-29")).toBe(false);
  });
});
