/**
 * The as-of argument: an Excel date serial (what a date cell holds) or an
 * ISO "YYYY-MM-DD" string, turned into the ISO date the gateway expects.
 *
 * Excel's 1900 date system counts days from 1900-01-01 = 1, and keeps a
 * deliberate bug inherited from Lotus 1-2-3: it treats 1900 as a leap year,
 * so serial 60 is "1900-02-29", a day that never existed. Serials 1-59 are
 * therefore one day "behind" serials 61 and up. We count from 1899-12-31 below
 * 60 and from 1899-12-30 above it, and reject 60 outright.
 *
 * The 1904 date system (an option on older Mac workbooks) is not handled.
 */

import { InputError } from "./errors";

const MS_PER_DAY = 86_400_000;
const EPOCH_BEFORE_BUG = Date.UTC(1899, 11, 31); // serial 1 -> 1900-01-01
const EPOCH_AFTER_BUG = Date.UTC(1899, 11, 30); // serial 61 -> 1900-03-01
export const FAKE_LEAP_DAY_SERIAL = 60;
export const MAX_SERIAL = 2_958_465; // 9999-12-31

export function serialToIsoDate(serial: number): string {
  if (typeof serial !== "number" || !Number.isFinite(serial)) {
    throw new InputError("asOf must be a date");
  }
  const day = Math.floor(serial); // a time of day is ignored
  if (day < 1 || day > MAX_SERIAL) {
    throw new InputError("asOf is not a valid Excel date (leave it out for the latest data)");
  }
  if (day === FAKE_LEAP_DAY_SERIAL) {
    throw new InputError("asOf is 1900-02-29, which does not exist");
  }
  const epoch = day < FAKE_LEAP_DAY_SERIAL ? EPOCH_BEFORE_BUG : EPOCH_AFTER_BUG;
  return new Date(epoch + day * MS_PER_DAY).toISOString().slice(0, 10);
}

export function isValidIsoDate(text: string): boolean {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!m) return false;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const date = new Date(Date.UTC(y, mo - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === mo - 1 && date.getUTCDate() === d;
}

/** ISO date for the gateway, or undefined when asOf was left out. */
export function normaliseAsOf(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value === "number") return serialToIsoDate(value);
  if (typeof value === "string") {
    const text = value.trim();
    if (!text) return undefined;
    if (isValidIsoDate(text)) return text;
    throw new InputError("asOf must be a date cell or a YYYY-MM-DD string");
  }
  throw new InputError("asOf must be a date cell or a YYYY-MM-DD string");
}
