/**
 * Shaping gateway responses into spilled ranges.
 *
 * Every tool returns the envelope
 *   {ok, payload: {result} | {error: {code, message}}, provenance, notes}
 * and a successful one spills as one rectangular 2-D array:
 *
 *   result table      (an array of records as header + rows; scalar fields
 *                      of the result as name/value rows below it)
 *   blank row
 *   provenance rows   model_id, model_version, as_of, snapshot_id, computed_at
 */

import { badResponseError, errorForEnvelope } from "./errors";

export type Cell = string | number | boolean;
export type Grid = Cell[][];

export interface Envelope {
  ok: boolean;
  payload: { result?: unknown; error?: { code?: unknown; message?: unknown } };
  provenance?: Record<string, unknown> | null;
  notes?: string | null;
}

export const PROVENANCE_FIELDS = ["model_id", "model_version", "as_of", "snapshot_id", "computed_at"] as const;

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function parseEnvelope(body: unknown): Envelope {
  if (!isRecord(body) || typeof body.ok !== "boolean" || !isRecord(body.payload)) {
    throw badResponseError("not a tool envelope");
  }
  return body as unknown as Envelope;
}

/** The result of a successful envelope, or the mapped error of a refusal. */
export function unwrap(env: Envelope): unknown {
  if (env.ok) {
    if (!("result" in env.payload)) throw badResponseError("ok envelope without a result");
    return env.payload.result;
  }
  const err = env.payload.error;
  if (!isRecord(err)) throw badResponseError("refusal without an error");
  throw errorForEnvelope(err.code, err.message);
}

export function toCell(v: unknown): Cell {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isFinite(v) ? v : "";
  if (typeof v === "string" || typeof v === "boolean") return v;
  return JSON.stringify(v);
}

function recordsTable(rows: unknown[]): Grid {
  const keys: string[] = [];
  for (const row of rows) {
    if (isRecord(row)) for (const k of Object.keys(row)) if (!keys.includes(k)) keys.push(k);
  }
  return [keys, ...rows.map((row) => keys.map((k) => toCell(isRecord(row) ? row[k] : undefined)))];
}

function isRecordArray(v: unknown): v is Record<string, unknown>[] {
  return Array.isArray(v) && v.length > 0 && v.every(isRecord);
}

export function resultTable(result: unknown): Grid {
  if (Array.isArray(result)) {
    if (result.length === 0) return [["(no rows)"]];
    return isRecordArray(result) ? recordsTable(result) : result.map((v) => [toCell(v)]);
  }
  if (!isRecord(result)) return [[toCell(result)]];
  const tableKey = Object.keys(result).find((k) => isRecordArray(result[k]));
  const table = tableKey ? recordsTable(result[tableKey] as unknown[]) : [];
  const scalars: Grid = Object.entries(result)
    .filter(([k]) => k !== tableKey)
    .map(([k, v]) => [k, toCell(v)]);
  return [...table, ...scalars];
}

export function provenanceRows(prov: Record<string, unknown> | null | undefined): Grid {
  return PROVENANCE_FIELDS.map((f) => [f, toCell(prov ? prov[f] : undefined)]);
}

/** Pad every row with "" so Excel gets a rectangle. */
export function rectangular(rows: Grid): Grid {
  const width = Math.max(1, ...rows.map((r) => r.length));
  return rows.map((r) => [...r, ...Array<Cell>(width - r.length).fill("")]);
}

export function shapeEnvelope(body: unknown): Grid {
  const env = parseEnvelope(body);
  const result = unwrap(env);
  return rectangular([...resultTable(result), [], ...provenanceRows(env.provenance)]);
}

/** The tool list from the gateway's OpenAPI document: tool, description. */
export function toolList(spec: unknown): Grid {
  if (!isRecord(spec) || !isRecord(spec.paths)) throw badResponseError("not an OpenAPI document");
  const rows: Grid = [["tool", "description"]];
  for (const [path, item] of Object.entries(spec.paths)) {
    const op = isRecord(item) && isRecord(item.post) ? item.post : {};
    const name = typeof op.operationId === "string" ? op.operationId : path.replace(/^\//, "");
    rows.push([name, toCell(op.description ?? op.summary ?? "")]);
  }
  return rows;
}
