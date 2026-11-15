import { describe, expect, it } from "vitest";
import { applyEvent, fromTranscript, provenanceRows } from "../src/lib/chat";
import type { AgentEvent, ChatItem } from "../src/lib/types";
import { FORECAST_ENVELOPE, PROVENANCE } from "./helpers";

const ev = (type: string, data: Record<string, unknown>): AgentEvent => ({ v: 1, seq: 1, run_id: "r", thread_id: "t", ts: "", type, data });

describe("chat state", () => {
  it("pairs each tool result with its call and collects provenance", () => {
    let items: ChatItem[] = [];
    items = applyEvent(items, ev("route.decided", { route: "tool", source: "rules" }));
    items = applyEvent(items, ev("tool.called", { id: "a", name: "forecast_series", arguments: { horizon: 5 } }));
    items = applyEvent(items, ev("tool.called", { id: "b", name: "health", arguments: {} }));
    items = applyEvent(items, ev("tool.result", { id: "a", ok: true, envelope: FORECAST_ENVELOPE }));
    items = applyEvent(items, ev("tool.result", { id: "b", ok: false, envelope: { ok: false, payload: { error: { code: "INTERNAL", message: "x" } } } }));
    items = applyEvent(items, ev("message.completed", { content: "done" }));
    expect(items.map((i) => i.kind)).toEqual(["route", "tool", "tool", "assistant"]);
    const [, a, b] = items as Extract<ChatItem, { kind: "tool" }>[];
    expect(a.ok).toBe(true);
    expect(b.ok).toBe(false);
    expect(provenanceRows(items)).toEqual([
      expect.objectContaining({ tool: "forecast_series", model: "ar1-placeholder", version: "1.1.0", asOf: "2023-06-30", snapshotId: "snap-1a2b3c4d" }),
    ]);
  });

  it("turns a resumed transcript into items", () => {
    const items = fromTranscript([
      { role: "user", content: "q" },
      { role: "tool", id: "c", name: "forecast_series", arguments: {}, envelope: { ok: true, provenance: PROVENANCE } },
      { role: "assistant", content: "a" },
    ]);
    expect(items.map((i) => i.kind)).toEqual(["user", "tool", "assistant"]);
    expect(provenanceRows(items)).toHaveLength(1);
  });

  it("records a failed run as an error item", () => {
    const items = applyEvent([], ev("run.failed", { error: "stopped after 6 steps" }));
    expect(items[0]).toMatchObject({ kind: "error", text: "stopped after 6 steps" });
  });
});
