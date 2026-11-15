import type { ChatItem } from "../lib/types";

type ToolItem = Extract<ChatItem, { kind: "tool" }>;

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** One tool call: name, arguments, status and the full result envelope. */
export function ToolCallCard({ item }: { item: ToolItem }) {
  const pending = item.envelope === undefined;
  const error = item.envelope?.payload?.error;
  const status = pending ? "running" : item.ok ? "ok" : `refused: ${error?.code ?? "error"}`;
  const p = item.envelope?.provenance;
  return (
    <article className={`tool-card ${pending ? "pending" : item.ok ? "ok" : "refused"}`} aria-label={`Tool call ${item.name}`}>
      <header>
        <span className="tool-name">{item.name}</span>
        <span className="tool-status">{status}</span>
      </header>
      <details open={!pending && !item.ok}>
        <summary>Arguments</summary>
        <pre>{pretty(item.arguments)}</pre>
      </details>
      {!pending && (
        <details>
          <summary>Result</summary>
          <pre>{pretty(item.envelope)}</pre>
        </details>
      )}
      {error && <p className="tool-error">{error.message}</p>}
      {p && (p.snapshot_id || p.as_of) && (
        <p className="tool-prov">
          as of {p.as_of ?? "-"} · snapshot {p.snapshot_id ?? "-"}
          {p.model_id ? ` · ${p.model_id} ${p.model_version ?? ""}` : ""}
        </p>
      )}
    </article>
  );
}
