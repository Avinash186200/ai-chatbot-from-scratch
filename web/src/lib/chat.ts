// Pure state helpers: turn streamed events and resumed transcripts into
// the items the chat view renders, and collect provenance for every number.

import type { AgentEvent, ChatItem, Envelope, Provenance, TranscriptItem } from "./types";

let counter = 0;
const key = (prefix: string) => `${prefix}-${++counter}`;

/** Apply one streamed agent event to the list of chat items. */
export function applyEvent(items: ChatItem[], event: AgentEvent): ChatItem[] {
  const d = event.data;
  switch (event.type) {
    case "route.decided":
      return [...items, { kind: "route", key: key("route"), route: String(d.route ?? ""), source: String(d.source ?? "") }];
    case "tool.called":
      return [
        ...items,
        {
          kind: "tool",
          key: key("tool"),
          id: String(d.id ?? ""),
          name: String(d.name ?? ""),
          arguments: (d.arguments as Record<string, unknown>) ?? {},
        },
      ];
    case "tool.result": {
      const id = String(d.id ?? "");
      const index = findLastIndex(items, (it) => it.kind === "tool" && it.id === id && it.envelope === undefined);
      if (index === -1) return items;
      const next = items.slice();
      const current = next[index] as Extract<ChatItem, { kind: "tool" }>;
      next[index] = { ...current, envelope: d.envelope as Envelope, ok: Boolean(d.ok) };
      return next;
    }
    case "message.completed":
      return [...items, { kind: "assistant", key: key("assistant"), text: String(d.content ?? "") }];
    case "run.failed":
      return [...items, { kind: "error", key: key("error"), text: String(d.error ?? "The run failed.") }];
    default:
      return items;
  }
}

function findLastIndex<T>(list: T[], pred: (item: T) => boolean): number {
  for (let i = list.length - 1; i >= 0; i--) if (pred(list[i])) return i;
  return -1;
}

/** A resumed conversation's transcript as chat items. */
export function fromTranscript(messages: TranscriptItem[]): ChatItem[] {
  return messages.map((m): ChatItem => {
    if (m.role === "user") return { kind: "user", key: key("user"), text: m.content };
    if (m.role === "assistant") return { kind: "assistant", key: key("assistant"), text: m.content };
    return {
      kind: "tool",
      key: key("tool"),
      id: m.id,
      name: m.name,
      arguments: m.arguments ?? {},
      envelope: m.envelope,
      ok: Boolean(m.envelope?.ok),
    };
  });
}

export function userItem(text: string): ChatItem {
  return { kind: "user", key: key("user"), text };
}

export interface ProvenanceRow {
  key: string;
  tool: string;
  model: string;
  version: string;
  asOf: string;
  snapshotId: string;
  computedAt: string;
}

/** One provenance row per successful model/data tool result, newest last. */
export function provenanceRows(items: ChatItem[]): ProvenanceRow[] {
  const rows: ProvenanceRow[] = [];
  for (const it of items) {
    if (it.kind !== "tool" || !it.envelope?.ok) continue;
    const p: Provenance | undefined = it.envelope.provenance;
    if (!p || (!p.snapshot_id && !p.as_of)) continue;
    rows.push({
      key: it.key,
      tool: it.name,
      model: p.model_id ?? "-",
      version: p.model_version ?? "-",
      asOf: p.as_of ?? "-",
      snapshotId: p.snapshot_id ?? "-",
      computedAt: p.computed_at ?? "-",
    });
  }
  return rows;
}
