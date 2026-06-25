// Mirrors agent-system/shared/contracts.py — the sunstead.v1 envelope and the
// payloads that arrive on the gateway's WS /stream (agent.results + agent.activity).

export type Artifact = { kind: "url" | "image" | "text" | "json"; value: string };

export type ActivityPayload = {
  task_id: string;
  status: string;
  detail?: string | null;
};

// The grounding verifier's judgement on an answer (docs/DESIGN.md §7). Annotate-only:
// `grounded=false` means a specific claim wasn't supported by the evidence the agent
// retrieved; `evidence_count=0` means there was nothing to check (treat as "unchecked").
export type Verdict = {
  grounded: boolean;
  confidence: number; // 0..1
  evidence_count: number;
  note?: string | null;
};

export type TaskResultPayload = {
  task_id: string;
  status: "completed" | "failed";
  result?: Record<string, unknown> | null;
  artifacts?: Artifact[];
  error?: string | null;
  verdict?: Verdict | null;
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
  | "verdict"
  | "control"
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
  "ask",
] as const;

export type Intent = (typeof INTENTS)[number];

// A sensible default args template per intent, to prefill the ask box.
// Keys MUST match what the receiving agent reads: web-agent → brief/style,
// git-agent + data-agent → question. Mismatched keys are silently dropped and
// the agent falls back to a default/nonsense prompt (see agents/web.py, git.py).
export const INTENT_TEMPLATES: Record<Intent, string> = {
  echo: '{ "text": "hello from the dashboard" }',
  build_website: '{ "brief": "one-page landing for Sunstead", "style": "dark" }',
  update_website: '{ "brief": "make the headline bolder", "style": "dark" }',
  analyze: '{ "question": "analyze this week\'s activity" }',
  summarize_metrics: '{ "question": "summarize the key metrics" }',
  query_data: '{ "question": "how many commits per author?" }',
  read_git: '{ "question": "what does the auth module do?" }',
  blame: '{ "question": "who last changed the session token schema?" }',
  who_changed: '{ "question": "who last touched the auth module?" }',
  recent_changes: '{ "question": "what changed in the last week?" }',
  ask: '{ "question": "what do we know about the auth module?" }',
};

// What each intent does + the arg key that carries the user's prompt — the field the
// receiving agent actually reads (web → brief, git/data → question; echo reads nothing
// meaningful). The ask box shows the blurb and rejects an empty/missing promptKey so a
// real question can't silently no-op (e.g. a question typed into `echo`, or under the
// wrong key). Keep `promptKey` in sync with agents/{web,git,data}.py.
export const INTENT_META: Record<Intent, { blurb: string; promptKey: string | null }> = {
  echo: { blurb: "Smoke test — echoes your args straight back. No agent work.", promptKey: null },
  build_website: { blurb: "Generates and publishes a one-page site.", promptKey: "brief" },
  update_website: { blurb: "Revises the generated site.", promptKey: "brief" },
  analyze: { blurb: "Answers a quantitative question with a chart.", promptKey: "question" },
  summarize_metrics: { blurb: "Charts key metrics from the graph.", promptKey: "question" },
  query_data: { blurb: "Answers a data question with a chart.", promptKey: "question" },
  read_git: { blurb: "Answers a question from the knowledge graph.", promptKey: "question" },
  blame: { blurb: "Finds who last changed something.", promptKey: "question" },
  who_changed: { blurb: "Finds who last touched something.", promptKey: "question" },
  recent_changes: { blurb: "Summarizes recent changes from the graph.", promptKey: "question" },
  ask: { blurb: "Answers a general question from the knowledge graph.", promptKey: "question" },
};

export function isResult(t: MessageType): boolean {
  return t === "task.completed" || t === "task.failed";
}

// Per-event-type presentation: a short label + an accent color, so the live
// feed can be scanned by severity/kind instead of reading as uniform gray.
export type EventStyle = { label: string; color: string };

const EVENT_STYLES: Record<MessageType, EventStyle> = {
  "transcript.partial": { label: "transcript", color: "#7ec8e3" },
  "transcript.final": { label: "transcript", color: "#7ec8e3" },
  "meeting.event": { label: "meeting", color: "#c4a3e0" },
  "task.create": { label: "dispatched", color: "#ffd57a" },
  activity: { label: "activity", color: "#f78f3f" },
  trace: { label: "trace", color: "#b69cff" },
  verdict: { label: "verdict", color: "#6fcf97" },
  control: { label: "control", color: "#e0a23f" },
  "task.completed": { label: "completed", color: "#6fcf97" },
  "task.failed": { label: "failed", color: "#e06f6f" },
  "kg.update": { label: "kg update", color: "#9ad29a" },
};

export function eventStyle(t: MessageType): EventStyle {
  return EVENT_STYLES[t] ?? { label: t, color: "#bcae8a" };
}
