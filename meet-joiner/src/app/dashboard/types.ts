// Mirrors agent-system/shared/contracts.py — the sunstead.v1 envelope and the
// payloads that arrive on the gateway's WS /stream (agent.results + agent.activity).

export type Artifact = { kind: "url" | "image" | "text" | "json"; value: string };

export type ActivityPayload = {
  task_id: string;
  status: string;
  detail?: string | null;
};

export type TaskResultPayload = {
  task_id: string;
  status: "completed" | "failed";
  result?: Record<string, unknown> | null;
  artifacts?: Artifact[];
  error?: string | null;
};

// A streamed reasoning ("thinking") or output ("text") delta. seq is monotonic per
// task_id so the client can apply deltas in order and drop replays idempotently.
export type TracePayload = {
  task_id: string;
  seq: number;
  phase: "thinking" | "text";
  delta: string;
};

export type MessageType =
  | "transcript.partial"
  | "transcript.final"
  | "meeting.event"
  | "task.create"
  | "task.completed"
  | "task.failed"
  | "activity"
  | "trace"
  | "kg.update";

export type Envelope = {
  schema: string;
  id: string;
  type: MessageType;
  meeting_id: string;
  ts: string;
  payload: Record<string, unknown>;
};

// The controlled intent vocab (contracts.py TaskIntent) for the ask box.
export const INTENTS = [
  "echo",
  "build_website",
  "update_website",
  "analyze",
  "summarize_metrics",
  "query_data",
  "read_git",
  "blame",
  "who_changed",
  "recent_changes",
] as const;

export type Intent = (typeof INTENTS)[number];

// A sensible default args template per intent, to prefill the ask box.
export const INTENT_TEMPLATES: Record<Intent, string> = {
  echo: '{ "text": "hello from the dashboard" }',
  build_website: '{ "brief": "one-page landing for Sunstead", "style": "dark" }',
  update_website: '{ "url": "", "change": "" }',
  analyze: '{ "question": "" }',
  summarize_metrics: '{ "metric": "" }',
  query_data: '{ "query": "" }',
  read_git: '{ "path": "" }',
  blame: '{ "path": "", "line": 1 }',
  who_changed: '{ "question": "who last touched the auth module?" }',
  recent_changes: '{ "since": "1 week ago" }',
};

export function isResult(t: MessageType): boolean {
  return t === "task.completed" || t === "task.failed";
}
