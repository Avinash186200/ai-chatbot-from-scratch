/**
 * Every failure the add-in can produce, and how it shows in a cell.
 *
 * Two Excel errors only:
 *   #N/A    "no answer": no data published by the as-of date, or the gateway
 *           cannot be reached. IFNA() can handle it.
 *   #VALUE! "this cannot work as asked": bad input, bad configuration, auth,
 *           or a server fault.
 *
 * Envelope errors are mapped by their stable CODE only, never by message
 * text: messages are for people and can change.
 */

export type ExcelErrorKind = "na" | "value";

export class AddinError extends Error {
  constructor(
    readonly kind: ExcelErrorKind,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "AddinError";
  }
}

/** Bad user input: wrong argument type, impossible date. */
export class InputError extends AddinError {
  constructor(message: string) {
    super("value", "INPUT", message);
    this.name = "InputError";
  }
}

/** Bad or missing settings: base URL, token. */
export class ConfigError extends AddinError {
  constructor(message: string) {
    super("value", "CONFIG", message);
    this.name = "ConfigError";
  }
}

/** The caller (an Excel invocation) went away; nobody reads this result. */
export class CancelledError extends AddinError {
  constructor() {
    super("na", "CANCELLED", "Cancelled");
    this.name = "CancelledError";
  }
}

export const ENVELOPE_CODE_KIND: Readonly<Record<string, ExcelErrorKind>> = {
  SNAPSHOT_UNAVAILABLE: "na",
  SCHEMA_VIOLATION: "value",
  UNSUPPORTED_HORIZON: "value",
  MODEL_FAILURE: "value",
  INTERNAL: "value",
};

const MAX_MESSAGE = 200;

function short(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > MAX_MESSAGE ? `${flat.slice(0, MAX_MESSAGE - 3)}...` : flat;
}

export function errorForEnvelope(code: unknown, message: unknown): AddinError {
  const c = typeof code === "string" ? code : "UNKNOWN";
  const kind = ENVELOPE_CODE_KIND[c] ?? "value";
  const text = typeof message === "string" && message ? message : "the tool refused";
  return new AddinError(kind, c, short(`${c}: ${text}`));
}

export function errorForHttpStatus(status: number): AddinError {
  if (status === 401 || status === 403) {
    return new AddinError("value", "AUTH", `Not authorised (HTTP ${status}): check the token in the task pane`);
  }
  if (status === 404) return new AddinError("value", "NOT_FOUND", "The gateway does not serve this tool (HTTP 404)");
  if (status === 429) return new AddinError("value", "BUSY", "The gateway is busy (HTTP 429); recalculate later");
  if (status >= 500) return new AddinError("value", "SERVER", `The gateway failed (HTTP ${status})`);
  return new AddinError("value", "HTTP", `The gateway rejected the request (HTTP ${status})`);
}

export function unreachableError(): AddinError {
  return new AddinError("na", "UNREACHABLE", "Cannot reach the gateway; check the base URL and the network");
}

export function timeoutError(): AddinError {
  return new AddinError("na", "TIMEOUT", "The gateway did not answer in time");
}

export function badResponseError(what: string): AddinError {
  return new AddinError("value", "BAD_RESPONSE", short(`Unexpected response from the gateway: ${what}`));
}
