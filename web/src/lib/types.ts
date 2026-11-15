// Wire types shared with the server (server/src/analytics_assistant/agent.py,
// web.py and envelopes.py). Keep them in step with the server.

export interface Provenance {
  model_id?: string | null;
  model_version?: string | null;
  as_of?: string | null;
  snapshot_id?: string | null;
  computed_at?: string | null;
  [key: string]: unknown;
}

export interface Envelope {
  tool?: string;
  ok?: boolean;
  payload?: { result?: unknown; error?: { code: string; message: string } };
  provenance?: Provenance;
  notes?: string[];
  [key: string]: unknown;
}

export type AgentEventType =
  | "run.started"
  | "route.decided"
  | "tool.called"
  | "tool.result"
  | "message.completed"
  | "run.failed"
  | "run.finished";

export interface AgentEvent {
  v: number;
  seq: number;
  run_id: string;
  thread_id: string;
  ts: string;
  type: AgentEventType | string;
  data: Record<string, unknown>;
}

export interface Thread {
  thread_id: string;
  user: string;
  title: string;
  created_at: string;
  updated_at: string;
}

/** One entry of a resumed conversation, as returned by GET /api/threads/<id>. */
export type TranscriptItem =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string }
  | { role: "tool"; id: string; name: string; arguments: Record<string, unknown>; envelope: Envelope };

/** What the chat view renders. */
export type ChatItem =
  | { kind: "user"; key: string; text: string }
  | { kind: "assistant"; key: string; text: string }
  | { kind: "route"; key: string; route: string; source: string }
  | {
      kind: "tool";
      key: string;
      id: string;
      name: string;
      arguments: Record<string, unknown>;
      envelope?: Envelope;
      ok?: boolean;
    }
  | { kind: "error"; key: string; text: string };

export interface ServerConfig {
  agent: string;
  llm_provider: string;
  llm_model: string;
  version: string;
}
