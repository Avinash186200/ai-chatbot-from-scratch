/**
 * The AA.* custom functions.
 *
 *   =AA.FORECAST(series, horizon, [asOf])  -> POST /gateway/forecast_series
 *   =AA.REGIME(series, [asOf])             -> POST /gateway/detect_regime
 *   =AA.TOOLS()                            -> GET  /gateway/openapi.json
 *
 * Argument names sent to the gateway match the server's tool schemas exactly
 * (series, horizon, as_of). Results spill as a 2-D array (see envelope.ts);
 * failures become #N/A or #VALUE! (see errors.ts) with the token redacted.
 */

import type { GatewayClient } from "../lib/client";
import { normaliseAsOf } from "../lib/dates";
import { AddinError, InputError } from "../lib/errors";
import { type Grid, shapeEnvelope, toolList } from "../lib/envelope";
import { redact } from "../lib/redact";

/** The part of CustomFunctions.CancelableInvocation the functions use. */
export interface Invocation {
  onCanceled?: () => void;
}

interface CustomFunctionsApi {
  Error: new (code: unknown, message?: string) => Error;
  ErrorCode: { invalidValue: unknown; notAvailable: unknown };
  associate: (id: string, fn: (...args: never[]) => unknown) => void;
}

function customFunctionsApi(): CustomFunctionsApi | undefined {
  return (globalThis as unknown as { CustomFunctions?: CustomFunctionsApi }).CustomFunctions;
}

/** An Excel error for any failure, with every secret redacted from its message. */
export function toExcelError(error: unknown, secrets: string[]): Error {
  const e =
    error instanceof AddinError
      ? error
      : new AddinError("value", "CLIENT", `Add-in error: ${error instanceof Error ? error.message : String(error)}`);
  const message = redact(e.message, secrets);
  const api = customFunctionsApi();
  if (!api) {
    const plain = new Error(message);
    plain.name = e.kind === "na" ? "#N/A" : "#VALUE!";
    return plain;
  }
  const code = e.kind === "na" ? api.ErrorCode.notAvailable : api.ErrorCode.invalidValue;
  return new api.Error(code, message);
}

export function normaliseSeries(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new InputError("series must be a series name, e.g. RATE_LONG");
  const name = value.trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9_]*$/.test(name)) throw new InputError("series must be a series name, e.g. RATE_LONG");
  return name;
}

export function normaliseHorizon(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new InputError("horizon must be a whole number of business days");
  }
  return value;
}

export interface AaFunctions {
  forecast(series: unknown, horizon: unknown, asOf?: unknown, invocation?: Invocation): Promise<Grid>;
  regime(series: unknown, asOf?: unknown, invocation?: Invocation): Promise<Grid>;
  tools(invocation?: Invocation): Promise<Grid>;
}

export function createFunctions(client: GatewayClient, extraSecrets: () => string[] = () => []): AaFunctions {
  async function run(invocation: Invocation | undefined, work: (signal: AbortSignal) => Promise<Grid>): Promise<Grid> {
    const controller = new AbortController();
    if (invocation) invocation.onCanceled = () => controller.abort();
    try {
      return await work(controller.signal);
    } catch (error) {
      throw toExcelError(error, [...client.secrets(), ...extraSecrets()]);
    }
  }

  return {
    forecast(series, horizon, asOf, invocation) {
      return run(invocation, async (signal) => {
        const args: Record<string, unknown> = {
          series: normaliseSeries(series),
          horizon: normaliseHorizon(horizon),
        };
        const asOfIso = normaliseAsOf(asOf);
        if (asOfIso) args.as_of = asOfIso;
        return shapeEnvelope(await client.callTool("forecast_series", args, signal));
      });
    },
    regime(series, asOf, invocation) {
      return run(invocation, async (signal) => {
        const args: Record<string, unknown> = { series: normaliseSeries(series) };
        const asOfIso = normaliseAsOf(asOf);
        if (asOfIso) args.as_of = asOfIso;
        return shapeEnvelope(await client.callTool("detect_regime", args, signal));
      });
    },
    tools(invocation) {
      return run(invocation, async (signal) => toolList(await client.getJson("/gateway/openapi.json", signal)));
    },
  };
}

/** Function ids, as in functions.json; Excel shows them as AA.<id>. */
export const FUNCTION_IDS = ["FORECAST", "REGIME", "TOOLS"] as const;

export function registerFunctions(fns: AaFunctions): boolean {
  const api = customFunctionsApi();
  if (!api) return false;
  api.associate("FORECAST", fns.forecast as (...args: never[]) => unknown);
  api.associate("REGIME", fns.regime as (...args: never[]) => unknown);
  api.associate("TOOLS", fns.tools as (...args: never[]) => unknown);
  return true;
}
