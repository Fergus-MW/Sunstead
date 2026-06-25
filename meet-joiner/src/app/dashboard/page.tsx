"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AppHeader from "../_components/AppHeader";
import Markdown from "../_components/Markdown";
import { fmtTime, relativeTime } from "../_components/format";
import AskBox from "./AskBox";
import { useStream, type ConnState, type TaskTrace } from "./useStream";
import { eventStyle, type Artifact, type Envelope, type Verdict } from "./types";

// One recorded step in a task's lifecycle — the granular progression (delegated →
// received → working… → done) that used to be collapsed into a single latest status.
type ActivityStep = { status: string; detail?: string; ts: string };

type TaskRow = {
  taskId: string;
  intentHint?: string;
  args?: Record<string, unknown>;
  requestedBy?: string;
  parentTaskId?: string;
  effort?: string;
  createdTs?: string;
  latestStatus: string;
  detail?: string;
  terminal?: "completed" | "failed";
  result?: Record<string, unknown> | null;
  artifacts: Artifact[];
  error?: string | null;
  verdict?: Verdict | null;
  lastTs: string;
  activityCount: number;
  steps: ActivityStep[]; // oldest -> newest, for the per-card timeline
  events: Envelope[];
};

// Append a step, collapsing a repeat of the previous status+detail into a ts bump so a
// chatty agent emitting the same "querying…" activity N times reads as one live step.
function pushStep(steps: ActivityStep[], step: ActivityStep): void {
  const last = steps[steps.length - 1];
  if (last && last.status === step.status && last.detail === step.detail) {
    last.ts = step.ts;
    return;
  }
  steps.push(step);
}

const STATUS_DOT: Record<ConnState, string> = {
  connecting: "bg-amber-300",
  open: "bg-emerald-400",
  closed: "bg-rose-400",
};

// A task in flight with no update for this long is "stuck" — surfaced in the digest.
const STUCK_MS = 30_000;

// Fallback meeting id when the page is opened without a ?meeting_id= param.
const DEFAULT_MEETING_ID = "mtg_dev";

// Newest-first comparator for ISO timestamps. Compare as epoch millis, not as
// strings: envelopes come from different services (gateway vs runner) whose ts
// may differ in zone/precision (`Z` vs offset, millis vs micros), which a raw
// string compare would silently misorder.
const byTsDesc = (a: string, b: string) => new Date(b).getTime() - new Date(a).getTime();

// Liveness age: a long-streaming task stays "live" via its trace deltas even when no
// activity event fires, so pass the task's last trace ts to avoid a false "stalled".
const liveAgeMs = (lastTs: string, traceTs: number, now: number) =>
  now - Math.max(new Date(lastTs).getTime(), traceTs || 0);

// A verdict only counts as a real grounding signal when there was evidence to check.
const isChecked = (v?: Verdict | null) => !!v && v.evidence_count > 0;
const isFlagged = (v?: Verdict | null) => isChecked(v) && !v!.grounded;

// Fold the event stream into one row per task_id.
function deriveTasks(events: Envelope[]): TaskRow[] {
  const map = new Map<string, TaskRow>();
  // events arrive newest-first; replay oldest-first for correct state.
  for (const env of [...events].reverse()) {
    const p = env.payload as Record<string, unknown>;
    const taskId = typeof p.task_id === "string" ? p.task_id : undefined;
    if (!taskId) continue;
    const row =
      map.get(taskId) ??
      ({
        taskId,
        latestStatus: "received",
        artifacts: [],
        lastTs: env.ts,
        activityCount: 0,
        steps: [],
        events: [],
      } as TaskRow);
    row.events.push(env);

    if (env.type === "task.create") {
      // The dispatch moment (now broadcast by the gateway). It's the earliest event, so a
      // just-dispatched task appears immediately with its intent + a "dispatched" pill —
      // a later "received"/activity overwrites the status as the runner picks it up.
      if (typeof p.intent === "string") row.intentHint = p.intent;
      if (p.args && typeof p.args === "object" && !Array.isArray(p.args)) {
        row.args = p.args as Record<string, unknown>;
      }
      if (typeof p.requested_by === "string") row.requestedBy = p.requested_by;
      if (typeof p.parent_task_id === "string") row.parentTaskId = p.parent_task_id;
      if (typeof p.effort === "string") row.effort = p.effort;
      row.createdTs = row.createdTs ?? env.ts;
      row.latestStatus = "dispatched";
      pushStep(row.steps, { status: "dispatched", ts: env.ts });
    } else if (env.type === "activity") {
      const status = (p.status as string) ?? row.latestStatus;
      const detail = typeof p.detail === "string" ? p.detail : undefined;
      // The "received" activity carries the intent as its detail — keep it as a label,
      // not as the running status detail (the next activity would overwrite it anyway).
      if (status === "received" && detail) row.intentHint = detail;
      else if (detail) row.detail = detail;
      row.latestStatus = status;
      row.activityCount += 1;
      // Record the step itself so the card can show the whole progression, not just the
      // latest. "received" carries the intent as detail (now the hint) — omit it here.
      pushStep(row.steps, { status, detail: status === "received" ? undefined : detail, ts: env.ts });
    } else if (env.type === "task.completed" || env.type === "task.failed") {
      row.terminal = env.type === "task.completed" ? "completed" : "failed";
      row.latestStatus = (p.status as string) ?? row.terminal;
      row.result = (p.result as Record<string, unknown>) ?? null;
      row.artifacts = (p.artifacts as Artifact[]) ?? [];
      row.error = (p.error as string) ?? null;
      row.verdict = (p.verdict as Verdict) ?? row.verdict ?? null;
      pushStep(row.steps, { status: row.latestStatus, detail: row.error ?? undefined, ts: env.ts });
    } else if (env.type === "verdict") {
      // The grounding check runs off the critical path (DESIGN §7), so the verdict arrives
      // as its own event a beat after the answer — apply it without touching terminal state.
      row.verdict = (p.verdict as Verdict) ?? row.verdict;
    } else if (env.type === "control" && !row.terminal) {
      // A stop was issued — reflect it on the card until the terminal (cancelled) result lands,
      // including for a card reconstructed from replay where our optimistic button state is gone.
      row.latestStatus = "cancelling";
    }
    row.lastTs = env.ts;
    map.set(taskId, row);
  }
  return [...map.values()].sort((a, b) => byTsDesc(a.lastTs, b.lastTs));
}

// A feed chain: one task's lifecycle (dispatched → received → activity… → done) as a
// single grouped strand, instead of those steps scattered through a flat list. Events
// without a task_id (transcript, kg.update) group by type so the feed reads as a few
// living strands rather than one undifferentiated stream.
type FeedChain = {
  key: string;
  taskId?: string;
  intent?: string;
  steps: Envelope[]; // oldest → newest
  latestTs: string; // newest step's ts (sort key)
};

function deriveChains(events: Envelope[]): FeedChain[] {
  const map = new Map<string, FeedChain>();
  for (const env of events) {
    // events are newest-first, so the first one seen per key is the most recent.
    const p = env.payload as Record<string, unknown>;
    const taskId = typeof p.task_id === "string" ? p.task_id : undefined;
    const key = taskId ?? `feed:${env.type}`;
    let c = map.get(key);
    if (!c) {
      c = { key, taskId, steps: [], latestTs: env.ts };
      map.set(key, c);
    }
    if (taskId && env.type === "task.create" && typeof p.intent === "string") c.intent = p.intent;
    c.steps.push(env);
  }
  return [...map.values()]
    .map((c) => ({ ...c, steps: c.steps.slice().reverse() })) // newest-first → oldest-first
    .sort((a, b) => byTsDesc(a.latestTs, b.latestTs));
}

// One step's display: a colored kind + the human-readable status + its detail/text.
function stepView(env: Envelope): { label: string; color: string; status: string; detail: string } {
  const p = env.payload as Record<string, unknown>;
  const style = eventStyle(env.type);
  const status = (p.status as string) ?? (typeof p.intent === "string" ? (p.intent as string) : "");
  const detail =
    (p.detail as string) ?? (p.error as string) ?? (p.text as string) ?? "";
  return { label: style.label, color: style.color, status, detail };
}

// Left-border accent: a flagged answer outranks terminal state — it's the thing to look at.
function accentFor(row: TaskRow): string {
  if (isFlagged(row.verdict)) return "#e0a23f"; // grounding flag
  if (row.terminal === "completed") return "#6fcf97";
  if (row.terminal === "failed") return "#e06f6f";
  return "#ffd57a"; // in flight
}

function StatusPill({ row }: { row: TaskRow }) {
  const cls =
    row.terminal === "completed"
      ? "bg-emerald-900/40 text-emerald-200 border-emerald-300/30"
      : row.terminal === "failed"
        ? "bg-rose-900/40 text-rose-200 border-rose-300/30"
        : "bg-amber-900/30 text-amber-200 border-amber-300/30";
  return (
    <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide ${cls}`}>
      {row.latestStatus}
    </span>
  );
}

// The grounding verdict, as a scannable chip. Hover for the verifier's note.
function VerdictBadge({ v }: { v?: Verdict | null }) {
  if (!v) return null;
  const checked = isChecked(v);
  const flagged = checked && !v.grounded;
  const pct = Math.round((v.confidence ?? 0) * 100);
  const cls = !checked
    ? "border-[#f3ead3]/15 text-[#f3ead3]/40"
    : flagged
      ? "border-amber-300/40 bg-amber-900/25 text-amber-200"
      : "border-emerald-300/30 bg-emerald-900/25 text-emerald-200";
  const icon = !checked ? "○" : flagged ? "⚠" : "✓";
  const label = !checked ? "unchecked" : flagged ? "unsupported" : "grounded";
  const detail = !checked ? "" : flagged ? `${pct}%` : `${v.evidence_count} refs`;
  return (
    <span
      title={v.note ?? undefined}
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] ${cls}`}
    >
      <span aria-hidden>{icon}</span>
      {label}
      {detail && <span className="opacity-60">· {detail}</span>}
    </span>
  );
}

// Compact result rendering — a key/value list rather than a raw JSON dump.
function ResultView({ result }: { result: Record<string, unknown> }) {
  const entries = Object.entries(result);
  return (
    <dl className="mt-2 space-y-1 rounded bg-black/30 p-2 text-[11px]">
      {entries.map(([k, v]) => {
        const isString = typeof v === "string";
        const val = typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
        return (
          <div key={k} className="flex gap-2">
            <dt className="w-20 shrink-0 text-[#f3ead3]/40">{k}</dt>
            <dd className="min-w-0 flex-1 break-words [overflow-wrap:anywhere] text-[#f3ead3]/80">
              {/* Agent text (e.g. `answer`) is markdown — render it; keep scalars literal. */}
              {isString ? <Markdown text={val} className="space-y-1" /> : val}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

// Blinking block caret that trails live-streaming text.
function StreamCaret() {
  return (
    <span className="ml-0.5 inline-block h-3 w-[3px] translate-y-[2px] animate-pulse bg-current align-baseline" />
  );
}

// Auto-scrolling box that follows streamed content as it grows. By default it shows
// the raw text; pass `render` to format the body (e.g. Markdown) while keeping the
// follow-scroll + trailing caret behaviour for the live stream.
function StreamBox({
  body,
  streaming,
  className,
  render,
}: {
  body: string;
  streaming: boolean;
  className: string;
  render?: (body: string) => React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [body]);
  return (
    <div ref={ref} className={className}>
      {render ? render(body) : body}
      {streaming && <StreamCaret />}
    </div>
  );
}

// While a transcript is still streaming, only the tail is on screen (the boxes are
// max-h-* and auto-scroll to the bottom), so we hand Markdown just the tail. That caps
// each re-parse at O(tail) instead of O(full buffer) — otherwise a long answer's
// per-token parse cost grows without bound. The full text is rendered once it's terminal.
const STREAM_TAIL = 4000;
function streamTail(s: string): string {
  if (s.length <= STREAM_TAIL) return s;
  const cut = s.length - STREAM_TAIL;
  // Prefer a blank-line (paragraph) boundary so the window never opens mid-block — a
  // code fence or list item started above the cut would otherwise render as plain text.
  // Fall back to a line boundary, then a hard cut, if neither is found in the window.
  const para = s.indexOf("\n\n", cut);
  if (para !== -1) return s.slice(para + 2);
  const nl = s.indexOf("\n", cut);
  return s.slice(nl === -1 ? cut : nl + 1);
}

// Live reasoning + output for a task: summarized chain-of-thought streams above the
// answer as the model generates (agent.trace). Collapsible; open by default.
function TraceView({ trace, terminal }: { trace?: TaskTrace; terminal?: boolean }) {
  if (!trace || (!trace.thinking && !trace.text)) return null;
  const streaming = !terminal;
  const thinking = streaming ? streamTail(trace.thinking) : trace.thinking;
  const text = streaming ? streamTail(trace.text) : trace.text;
  return (
    <details open className="group mt-2">
      <summary className="flex cursor-pointer select-none list-none items-center gap-1.5 text-[9px] uppercase tracking-[0.2em] text-[#b69cff]/70">
        <span className="inline-block transition-transform group-open:rotate-90" aria-hidden>
          ▸
        </span>
        Reasoning
        {streaming && (
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[#b69cff]" />
        )}
      </summary>
      <div className="mt-2 space-y-2">
        {trace.thinking && (
          <StreamBox
            body={thinking}
            streaming={streaming && !trace.text}
            className="max-h-32 overflow-y-auto whitespace-pre-wrap rounded border border-[#b69cff]/15 bg-[#b69cff]/[0.06] p-2 font-mono text-[10px] italic leading-relaxed text-[#f3ead3]/55"
          />
        )}
        {trace.text && (
          <div>
            <div className="mb-1 text-[9px] uppercase tracking-[0.2em] text-[#f3ead3]/40">
              Output{streaming ? " · streaming" : ""}
            </div>
            <StreamBox
              body={text}
              streaming={streaming}
              render={(b) => <Markdown text={b} className="space-y-1.5" />}
              className="max-h-40 space-y-1.5 overflow-y-auto break-words [overflow-wrap:anywhere] rounded border border-[#f3ead3]/10 bg-black/40 p-2 text-[10px] leading-relaxed text-[#f3ead3]/75"
            />
          </div>
        )}
      </div>
    </details>
  );
}

// Dot color for one step. Terminal states read by outcome; in-flight steps glow amber,
// with the most recent one brighter so the eye lands on "where it is now".
function stepColor(status: string, isLast: boolean): string {
  if (status === "completed") return "#6fcf97";
  if (status === "failed" || status === "cancelled") return "#e06f6f";
  return isLast ? "#ffd57a" : "#f3ead3"; // current step vs a settled past one
}

// The per-task step timeline: the in-between progression (delegated → received →
// working… → done), not just the latest status. Collapsible; open while the task is
// live so you can watch it move, collapsed once terminal (the result is the focus then).
function StepsTimeline({ steps, terminal }: { steps: ActivityStep[]; terminal?: boolean }) {
  if (steps.length <= 1) return null; // a lone "dispatched" step is just the status pill
  return (
    <details open={!terminal} className="group mt-2">
      <summary className="flex cursor-pointer select-none list-none items-center gap-1.5 text-[9px] uppercase tracking-[0.2em] text-[#f3ead3]/45">
        <span className="inline-block transition-transform group-open:rotate-90" aria-hidden>
          ▸
        </span>
        Steps
        <span className="text-[#f3ead3]/30">({steps.length})</span>
      </summary>
      <div className="ml-[3px] mt-2 max-h-44 overflow-y-auto border-l border-[#f3ead3]/10 pl-3">
        {steps.map((s, i) => {
          const isLast = i === steps.length - 1;
          const color = stepColor(s.status, isLast);
          return (
            <div key={`${s.ts}:${i}`} className="relative py-0.5 text-[11px] leading-snug">
              <span
                className={`absolute -left-[15px] top-[6px] inline-block h-1.5 w-1.5 rounded-full ring-2 ring-[#0b1a17] ${
                  isLast && !terminal ? "animate-pulse" : ""
                }`}
                style={{ background: color }}
              />
              <span className="uppercase tracking-wide" style={{ color }}>
                {s.status}
              </span>
              {s.detail && (
                <span className="break-words [overflow-wrap:anywhere] text-[#f3ead3]/55"> · {s.detail}</span>
              )}
            </div>
          );
        })}
      </div>
    </details>
  );
}

type Tone = "default" | "active" | "good" | "warn" | "bad" | "muted";
const TONE: Record<Tone, string> = {
  default: "text-[#f3ead3]/80",
  active: "text-amber-200",
  good: "text-emerald-200",
  warn: "text-amber-300",
  bad: "text-rose-300",
  muted: "text-[#f3ead3]/35",
};

// One number in the digest bar — the conductor's-eye glance.
type TaskFilter = "all" | "active" | "stuck" | "grounded" | "flagged" | "completed" | "failed";

const FILTER_LABEL: Record<TaskFilter, string> = {
  all: "all",
  active: "active",
  stuck: "stuck",
  grounded: "grounded",
  flagged: "flagged",
  completed: "done",
  failed: "failed",
};

function taskMatchesFilter(row: TaskRow, filter: TaskFilter, trace: TaskTrace | undefined, now: number): boolean {
  if (filter === "all") return true;
  if (filter === "active") return !row.terminal;
  if (filter === "stuck") return !row.terminal && liveAgeMs(row.lastTs, trace?.ts ?? 0, now) > STUCK_MS;
  if (filter === "grounded") return isChecked(row.verdict) && !!row.verdict?.grounded;
  if (filter === "flagged") return isFlagged(row.verdict);
  return row.terminal === filter;
}

function Stat({
  label,
  value,
  tone,
  pulse,
  selected,
  onClick,
}: {
  label: string;
  value: number;
  tone: Tone;
  pulse?: boolean;
  selected?: boolean;
  onClick?: () => void;
}) {
  const className = `flex min-w-[3.5rem] flex-col items-center px-3 py-1 ${
    onClick ? "rounded transition-colors hover:bg-[#f3ead3]/8 focus:outline-none focus:ring-1 focus:ring-[#f3ead3]/30" : ""
  } ${selected ? "bg-[#f3ead3]/10" : ""}`;
  const content = (
    <>
      <span
        className={`text-lg font-semibold leading-none tabular-nums ${TONE[tone]} ${pulse && value > 0 ? "animate-pulse" : ""}`}
      >
        {value}
      </span>
      <span className="mt-1 text-[9px] uppercase tracking-[0.2em] text-[#f3ead3]/40">{label}</span>
    </>
  );
  if (!onClick) return <div className={className}>{content}</div>;
  return (
    <button type="button" onClick={onClick} aria-pressed={selected} className={className}>
      {content}
    </button>
  );
}

type Digest = {
  active: number;
  stuck: number;
  completed: number;
  failed: number;
  grounded: number;
  flagged: number;
};

// One chain in the live feed: a header (what it is + latest state + age) over a small
// vertical timeline of its steps, so each task reads as a strand you can follow.
function FeedChainView({ chain, now }: { chain: FeedChain; now: number }) {
  const head = stepView(chain.steps[chain.steps.length - 1]);
  return (
    <div className="rounded-md border border-[#f3ead3]/10 bg-black/20">
      <div className="flex items-center gap-2 px-2 py-1.5">
        <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: head.color }} />
        {chain.intent ? (
          <span className="shrink-0 rounded bg-[#f3ead3]/8 px-1.5 py-0.5 text-[10px] text-[#f3ead3]/70">
            {chain.intent}
          </span>
        ) : (
          <span className="shrink-0 text-[10px] font-medium uppercase tracking-wide" style={{ color: head.color }}>
            {head.label}
          </span>
        )}
        {chain.taskId && (
          <span className="truncate font-mono text-[10px] text-[#f3ead3]/35">{chain.taskId}</span>
        )}
        {head.status && (
          <span className="ml-auto shrink-0 text-[10px]" style={{ color: head.color }}>
            {head.status}
          </span>
        )}
        <span
          className={`${head.status ? "" : "ml-auto "}shrink-0 text-[10px] text-[#f3ead3]/30`}
          title={fmtTime(chain.latestTs)}
        >
          {relativeTime(chain.latestTs, now)}
        </span>
      </div>
      <div className="ml-[11px] border-l border-[#f3ead3]/10 pb-1.5 pl-3 pr-2">
        {chain.steps.map((env) => {
          const s = stepView(env);
          const txt = [s.status, s.detail].filter(Boolean).join(" · ");
          return (
            <div key={env.id} className="relative py-0.5 text-[11px] leading-snug">
              <span
                className="absolute -left-[15px] top-[6px] inline-block h-1.5 w-1.5 rounded-full ring-2 ring-[#0b1a17]"
                style={{ background: s.color }}
              />
              <span className="text-[10px] uppercase tracking-wide" style={{ color: s.color }}>
                {s.label}
              </span>
              {txt && (
                <span className="break-words [overflow-wrap:anywhere] text-[#f3ead3]/55"> {txt}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function compactText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

const TASK_TOPIC_BY_INTENT: Record<string, string> = {
  echo: "agent.tasks.dev",
  build_website: "agent.tasks.web",
  update_website: "agent.tasks.web",
  analyze: "agent.tasks.data",
  summarize_metrics: "agent.tasks.data",
  query_data: "agent.tasks.data",
  read_git: "agent.tasks.git",
  blame: "agent.tasks.git",
  who_changed: "agent.tasks.git",
  recent_changes: "agent.tasks.git",
  ask: "agent.tasks.git",
  recap: "agent.tasks.ops",
  action_items: "agent.tasks.ops",
  decisions: "agent.tasks.ops",
  research: "agent.tasks.research",
};

type DetailTab = "overview" | "timeline" | "reasoning" | "evidence" | "raw";

function argString(args: Record<string, unknown> | undefined, ...keys: string[]): string {
  if (!args) return "";
  for (const key of keys) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function taskPrompt(row: TaskRow): string {
  return argString(row.args, "question", "brief", "text", "q") || row.detail || "";
}

function resultString(row: TaskRow, ...keys: string[]): string {
  if (!row.result) return "";
  for (const key of keys) {
    const value = row.result[key];
    if (typeof value === "string" && value.trim()) return compactText(value);
  }
  return "";
}

function outputPreview(row: TaskRow): string {
  return resultString(row, "answer", "summary", "insight", "url") || row.error || "";
}

function topicForEnvelope(env: Envelope, row?: TaskRow): string {
  if (env.type === "task.create") {
    const intent = typeof env.payload.intent === "string" ? env.payload.intent : row?.intentHint;
    return intent ? (TASK_TOPIC_BY_INTENT[intent] ?? "agent.tasks.*") : "agent.tasks.*";
  }
  if (env.type === "activity") return "agent.activity";
  if (env.type === "trace") return "agent.trace";
  if (env.type === "control") return "agent.control";
  if (env.type === "transcript.final" || env.type === "transcript.partial") return "meeting.transcript";
  if (env.type === "kg.update") return "kg.updates";
  if (env.type === "verdict" || env.type === "task.completed" || env.type === "task.failed") return "agent.results";
  return "unknown";
}

function eventSummary(env: Envelope): string {
  const p = env.payload;
  const parts = [
    typeof p.intent === "string" ? p.intent : undefined,
    typeof p.status === "string" ? p.status : undefined,
    typeof p.detail === "string" ? p.detail : undefined,
    typeof p.error === "string" ? p.error : undefined,
    typeof p.text === "string" ? p.text : undefined,
  ].filter(Boolean);
  return parts.join(" - ");
}

function TaskListItem({
  row,
  trace,
  now,
  selected,
  onSelect,
}: {
  row: TaskRow;
  trace?: TaskTrace;
  now: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const prompt = taskPrompt(row);
  const preview = outputPreview(row);
  const stalled = !row.terminal && liveAgeMs(row.lastTs, trace?.ts ?? 0, now) > STUCK_MS;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full rounded-md border bg-black/20 p-3 text-left transition-colors ${
        selected ? "border-[#f3ead3]/35 bg-[#f3ead3]/8" : "border-[#f3ead3]/10 hover:border-[#f3ead3]/25"
      }`}
      style={{ borderLeft: `3px solid ${accentFor(row)}` }}
    >
      <div className="flex min-w-0 items-center gap-2">
        {row.intentHint && (
          <span className="shrink-0 rounded bg-[#f3ead3]/8 px-1.5 py-0.5 text-[10px] text-[#f3ead3]/65">
            {row.intentHint}
          </span>
        )}
        <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-[#f3ead3]/35">{row.taskId}</span>
        <StatusPill row={row} />
      </div>
      {prompt && <p className="mt-2 line-clamp-2 text-xs leading-snug text-[#f3ead3]/80">{prompt}</p>}
      {preview && <p className="mt-1 line-clamp-2 text-[11px] leading-snug text-[#f3ead3]/45">{preview}</p>}
      <div className="mt-2 flex items-center gap-2 text-[10px] text-[#f3ead3]/30">
        <span title={fmtTime(row.lastTs)}>{relativeTime(row.lastTs, now)}</span>
        <span>{row.activityCount} updates</span>
        {row.artifacts.length > 0 && <span>{row.artifacts.length} artifacts</span>}
        {stalled && <span className="rounded-full bg-rose-900/30 px-1.5 text-rose-300">stalled</span>}
        <span className="ml-auto flex shrink-0 items-center gap-1">
          <VerdictBadge v={row.verdict} />
        </span>
      </div>
    </button>
  );
}

function MessageFlow({ row, trace }: { row: TaskRow; trace?: TaskTrace }) {
  const traceSeen = !!trace && (!!trace.text || !!trace.thinking);
  return (
    <div className="space-y-1.5">
      {row.events.map((env) => {
        const style = eventStyle(env.type);
        return (
          <div key={env.id} className="grid grid-cols-[8.5rem_7rem_minmax(0,1fr)] gap-2 rounded border border-[#f3ead3]/10 bg-black/20 px-2 py-1.5 text-[11px]">
            <span className="truncate font-mono text-[#f3ead3]/35">{topicForEnvelope(env, row)}</span>
            <span className="truncate uppercase tracking-wide" style={{ color: style.color }}>
              {style.label}
            </span>
            <span className="min-w-0 truncate text-[#f3ead3]/60">{eventSummary(env) || fmtTime(env.ts)}</span>
          </div>
        );
      })}
      {traceSeen && (
        <div className="grid grid-cols-[8.5rem_7rem_minmax(0,1fr)] gap-2 rounded border border-[#b69cff]/15 bg-[#b69cff]/[0.06] px-2 py-1.5 text-[11px]">
          <span className="truncate font-mono text-[#f3ead3]/35">agent.trace</span>
          <span className="truncate uppercase tracking-wide text-[#b69cff]">trace</span>
          <span className="min-w-0 truncate text-[#f3ead3]/60">
            {trace.lastSeq} deltas - {trace.thinking.length} thinking chars - {trace.text.length} output chars
          </span>
        </div>
      )}
    </div>
  );
}

function ArtifactLinks({ artifacts }: { artifacts: Artifact[] }) {
  if (artifacts.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {artifacts.map((a) =>
        a.kind === "url" || a.kind === "image" ? (
          <a
            key={`${a.kind}:${a.value}`}
            href={a.value}
            target="_blank"
            rel="noreferrer"
            className="inline-flex max-w-full items-center gap-1 truncate rounded-full border border-sky-300/30 bg-sky-900/20 px-2 py-0.5 text-xs text-sky-200 hover:border-sky-300/60 hover:text-sky-100"
          >
            {a.kind}: {a.value.replace(/^https?:\/\//, "")}
          </a>
        ) : (
          <span
            key={`${a.kind}:${a.value}`}
            className="truncate rounded-full border border-[#f3ead3]/15 px-2 py-0.5 text-xs text-[#f3ead3]/70"
          >
            {a.kind}: {a.value}
          </span>
        ),
      )}
    </div>
  );
}

function TaskDetail({
  row,
  trace,
  now,
  isStopping,
  onStop,
}: {
  row: TaskRow | null;
  trace?: TaskTrace;
  now: number;
  isStopping: boolean;
  onStop: (taskId: string) => void;
}) {
  const [tab, setTab] = useState<DetailTab>("overview");
  if (!row) {
    return (
      <section className="flex min-h-0 flex-1 items-center justify-center p-8 text-center text-sm text-[#f3ead3]/35">
        Select a task to inspect its prompt, output, timeline, reasoning, evidence, and raw stream messages.
      </section>
    );
  }
  const prompt = taskPrompt(row);
  const tabs: DetailTab[] = ["overview", "timeline", "reasoning", "evidence", "raw"];
  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-[#f3ead3]/10 p-4">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              {row.intentHint && (
                <span className="rounded bg-[#f3ead3]/8 px-1.5 py-0.5 text-[10px] text-[#f3ead3]/65">
                  {row.intentHint}
                </span>
              )}
              <span className="font-mono text-[10px] text-[#f3ead3]/35">{row.taskId}</span>
              <VerdictBadge v={row.verdict} />
              <StatusPill row={row} />
            </div>
            <h2 className="mt-2 break-words text-sm font-medium leading-snug text-[#f3ead3]">
              {prompt || row.detail || "No prompt captured"}
            </h2>
          </div>
          {!row.terminal && (
            <button
              onClick={() => onStop(row.taskId)}
              disabled={isStopping}
              className="rounded border border-rose-300/30 px-2 py-1 text-xs text-rose-200/80 hover:border-rose-300/60 hover:text-rose-100 disabled:opacity-40"
            >
              {isStopping ? "stopping..." : "stop"}
            </button>
          )}
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {tabs.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={`rounded border px-2 py-1 text-[10px] uppercase tracking-[0.18em] ${
                tab === t
                  ? "border-[#f3ead3]/45 bg-[#f3ead3]/10 text-[#f3ead3]"
                  : "border-[#f3ead3]/15 text-[#f3ead3]/45 hover:border-[#f3ead3]/35 hover:text-[#f3ead3]/75"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {tab === "overview" && (
          <div className="space-y-4">
            <dl className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
              <Info label="requested by" value={row.requestedBy ?? "unknown"} />
              <Info label="effort" value={row.effort ?? "standard"} />
              <Info label="created" value={row.createdTs ? relativeTime(row.createdTs, now) : "unknown"} />
              <Info label="last event" value={relativeTime(row.lastTs, now)} />
            </dl>
            {row.result && Object.keys(row.result).length > 0 && <ResultView result={row.result} />}
            <ArtifactLinks artifacts={row.artifacts} />
            {row.error && (
              <p className="rounded border border-rose-300/20 bg-rose-900/20 p-2 text-xs text-rose-200/90">
                {row.error}
              </p>
            )}
          </div>
        )}
        {tab === "timeline" && (
          <div className="space-y-4">
            <MessageFlow row={row} trace={trace} />
            <StepsTimeline steps={row.steps} terminal={!!row.terminal} />
          </div>
        )}
        {tab === "reasoning" && (
          trace ? (
            <TraceView trace={trace} terminal={!!row.terminal} />
          ) : (
            <p className="py-12 text-center text-sm text-[#f3ead3]/35">No trace deltas captured for this task.</p>
          )
        )}
        {tab === "evidence" && <EvidenceView row={row} />}
        {tab === "raw" && (
          <pre className="max-h-full overflow-auto rounded border border-[#f3ead3]/10 bg-black/30 p-3 text-[11px] leading-relaxed text-[#f3ead3]/65">
            {JSON.stringify({ args: row.args, result: row.result, events: row.events }, null, 2)}
          </pre>
        )}
      </div>
    </section>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-[#f3ead3]/10 bg-black/20 p-2">
      <dt className="text-[9px] uppercase tracking-[0.18em] text-[#f3ead3]/35">{label}</dt>
      <dd className="mt-1 truncate text-[#f3ead3]/75">{value}</dd>
    </div>
  );
}

function EvidenceView({ row }: { row: TaskRow }) {
  const sources = Array.isArray(row.result?.sources) ? row.result.sources : [];
  return (
    <div className="space-y-3">
      {row.verdict ? (
        <div className="rounded border border-[#f3ead3]/10 bg-black/25 p-3 text-sm">
          <VerdictBadge v={row.verdict} />
          {row.verdict.note && <p className="mt-2 text-xs leading-relaxed text-[#f3ead3]/60">{row.verdict.note}</p>}
        </div>
      ) : (
        <p className="rounded border border-[#f3ead3]/10 bg-black/20 p-3 text-sm text-[#f3ead3]/40">
          No verifier result for this task.
        </p>
      )}
      {sources.length > 0 && (
        <div className="space-y-1.5">
          <h3 className="text-[10px] uppercase tracking-[0.24em] text-[#f3ead3]/45">Sources</h3>
          {sources.map((source, i) => {
            const url = typeof source === "object" && source !== null && "url" in source ? String(source.url) : "";
            const title = typeof source === "object" && source !== null && "title" in source ? String(source.title) : url;
            if (!url) return null;
            return (
              <a
                key={`${url}:${i}`}
                href={url}
                target="_blank"
                rel="noreferrer"
                className="block rounded border border-[#f3ead3]/10 bg-black/20 px-2 py-1.5 text-xs text-sky-200 hover:border-sky-300/40"
              >
                <span className="block truncate">{title}</span>
                <span className="block truncate font-mono text-[10px] text-[#f3ead3]/35">{url}</span>
              </a>
            );
          })}
        </div>
      )}
      <ArtifactLinks artifacts={row.artifacts} />
    </div>
  );
}

type StreamStat = {
  topic: string;
  count: number;
  latestTs: string;
  detail: string;
};

function StreamOverview({
  events,
  tasks,
  traces,
  now,
}: {
  events: Envelope[];
  tasks: TaskRow[];
  traces: Record<string, TaskTrace>;
  now: number;
}) {
  const rows = useMemo<StreamStat[]>(() => {
    const taskById = new Map(tasks.map((task) => [task.taskId, task]));
    const stats = new Map<string, StreamStat>();
    const touch = (topic: string, ts: string, detail: string, count = 1) => {
      const existing = stats.get(topic);
      if (!existing) {
        stats.set(topic, { topic, count, latestTs: ts, detail });
        return;
      }
      existing.count += count;
      if (new Date(ts).getTime() >= new Date(existing.latestTs).getTime()) {
        existing.latestTs = ts;
        existing.detail = detail;
      }
    };

    for (const env of events) {
      const taskId = typeof env.payload.task_id === "string" ? env.payload.task_id : undefined;
      const row = taskId ? taskById.get(taskId) : undefined;
      const style = eventStyle(env.type);
      touch(topicForEnvelope(env, row), env.ts, eventSummary(env) || style.label);
    }

    for (const [taskId, trace] of Object.entries(traces)) {
      if (!trace.ts) continue;
      const row = taskById.get(taskId);
      const label = row?.intentHint ? `${row.intentHint} trace` : `${taskId} trace`;
      touch("agent.trace", new Date(trace.ts).toISOString(), label, trace.lastSeq);
    }

    return [...stats.values()].sort((a, b) => byTsDesc(a.latestTs, b.latestTs));
  }, [events, tasks, traces]);

  return (
    <div className="border-b border-[#f3ead3]/10 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
          Streams <span className="text-[#f3ead3]/30">({rows.length})</span>
        </h2>
        <span className="text-[10px] text-[#f3ead3]/30">Kafka</span>
      </div>
      {rows.length === 0 ? (
        <p className="rounded border border-[#f3ead3]/10 bg-black/20 px-2 py-3 text-center text-[11px] text-[#f3ead3]/35">
          Waiting for stream traffic.
        </p>
      ) : (
        <div className="space-y-1.5">
          {rows.slice(0, 7).map((row) => (
            <div key={row.topic} className="rounded border border-[#f3ead3]/10 bg-black/20 px-2 py-1.5">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-[#f3ead3]/70">{row.topic}</span>
                <span className="shrink-0 text-[10px] tabular-nums text-[#f3ead3]/35">{row.count}</span>
              </div>
              <div className="mt-0.5 flex items-center gap-2 text-[10px] text-[#f3ead3]/30">
                <span className="min-w-0 flex-1 truncate">{row.detail}</span>
                <span className="shrink-0" title={fmtTime(row.latestTs)}>
                  {relativeTime(row.latestTs, now)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const [meetingId, setMeetingId] = useState("");
  // A 2s tick so relative times and "stuck" status stay live even when no events arrive.
  const [now, setNow] = useState(() => Date.now());
  const [filter, setFilter] = useState<TaskFilter>("all");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 2000);
    return () => clearInterval(id);
  }, []);

  // Seed the meeting filter from ?meeting_id= (the landing page links here scoped
  // to the call the envoy just joined), so the avatar's delegated results show up
  // without the user retyping the id. Done on mount to avoid a hydration mismatch.
  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("meeting_id");
    // Reading window post-hydration is the point (a lazy initializer runs at static
    // prerender with no window, never seeing ?meeting_id=), so this setState is intended.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (fromUrl) setMeetingId(fromUrl);
  }, []);

  const { events, traces, state, clear } = useStream({ meetingId: meetingId || undefined });
  const tasks = useMemo(() => deriveTasks(events), [events]);
  // The live feed, grouped into per-task (and per-stream) chains rather than a flat list.
  const chains = useMemo(() => deriveChains(events), [events]);

  // Client-side conductor: the fleet glance, recomputed as tasks change and time passes.
  const digest = useMemo<Digest>(() => {
    const d: Digest = { active: 0, stuck: 0, completed: 0, failed: 0, grounded: 0, flagged: 0 };
    for (const t of tasks) {
      if (!t.terminal) {
        d.active += 1;
        if (liveAgeMs(t.lastTs, traces[t.taskId]?.ts ?? 0, now) > STUCK_MS) d.stuck += 1;
      } else if (t.terminal === "completed") d.completed += 1;
      else d.failed += 1;
      if (isChecked(t.verdict)) {
        if (t.verdict!.grounded) d.grounded += 1;
        else d.flagged += 1;
      }
    }
    return d;
  }, [tasks, traces, now]);
  const filteredTasks = useMemo(
    () => tasks.filter((task) => taskMatchesFilter(task, filter, traces[task.taskId], now)),
    [tasks, filter, traces, now],
  );
  const selectFilter = useCallback((next: TaskFilter) => {
    setFilter((current) => (current === next ? "all" : next));
  }, []);
  const selectedTask = useMemo(
    () => filteredTasks.find((task) => task.taskId === selectedTaskId) ?? filteredTasks[0] ?? null,
    [filteredTasks, selectedTaskId],
  );

  // Operator stop (docs/DESIGN.md §7): POST to the control channel; the cancelled result
  // arrives back over the WS like any other terminal state. Optimistically disable the button.
  const [stopping, setStopping] = useState<Set<string>>(new Set());
  const unstop = useCallback((taskId: string) => {
    setStopping((s) => {
      if (!s.has(taskId)) return s;
      const n = new Set(s);
      n.delete(taskId);
      return n;
    });
  }, []);
  // Stable across renders so streamed trace deltas do not also churn the stop handler.
  const stop = useCallback(
    async (taskId: string) => {
      setStopping((s) => new Set(s).add(taskId));
      try {
        const res = await fetch("/api/control", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ task_id: taskId, meeting_id: meetingId || DEFAULT_MEETING_ID, reason: "operator stop" }),
        });
        // Leave "stopping…" showing until the task goes terminal — the button only renders while
        // in-flight, so it unmounts when the cancelled result lands on the WS (no cleanup needed).
        if (!res.ok) throw new Error(`control ${res.status}`);
      } catch {
        // The request never reached the gateway — re-enable the button so the operator can retry,
        // rather than leaving it stuck disabled with no cancellation in flight.
        unstop(taskId);
      }
    },
    [meetingId, unstop],
  );

  const statusBadge = (
    <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
      <span
        className={`inline-block h-2 w-2 rounded-full ${STATUS_DOT[state]} ${state === "open" ? "" : "animate-pulse"}`}
      />
      {state}
    </span>
  );

  return (
    <main className="flex h-screen flex-col bg-[#0b1a17] text-[#f3ead3]">
      <AppHeader
        title="Sunstead · Mission Control"
        status={statusBadge}
        nav={[
          { href: "/graph", label: "Graph" },
          { href: "/", label: "← Meeting hall" },
        ]}
      />

      {/* Digest bar — filter + the conductor's-eye fleet glance */}
      <div className="flex flex-wrap items-center gap-4 border-b border-[#f3ead3]/10 bg-black/20 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <label className="text-[9px] uppercase tracking-[0.25em] text-[#f3ead3]/40">Meeting</label>
          <input
            value={meetingId}
            onChange={(e) => setMeetingId(e.target.value)}
            placeholder="all"
            className="w-40 rounded-md border border-[#f3ead3]/15 bg-black/40 px-2.5 py-1 text-xs outline-none placeholder:text-[#f3ead3]/25 focus:border-[#f3ead3]/50"
          />
        </div>
        <div className="ml-auto flex max-w-full items-stretch overflow-x-auto divide-x divide-[#f3ead3]/10">
          <Stat label="all" value={tasks.length} tone={filter === "all" ? "default" : "muted"} selected={filter === "all"} onClick={() => setFilter("all")} />
          <Stat label="active" value={digest.active} tone={digest.active ? "active" : "muted"} selected={filter === "active"} onClick={() => selectFilter("active")} />
          <Stat label="stuck" value={digest.stuck} tone={digest.stuck ? "bad" : "muted"} pulse selected={filter === "stuck"} onClick={() => selectFilter("stuck")} />
          <Stat label="grounded" value={digest.grounded} tone={digest.grounded ? "good" : "muted"} selected={filter === "grounded"} onClick={() => selectFilter("grounded")} />
          <Stat label="flagged" value={digest.flagged} tone={digest.flagged ? "warn" : "muted"} pulse selected={filter === "flagged"} onClick={() => selectFilter("flagged")} />
          <Stat label="done" value={digest.completed} tone={digest.completed ? "good" : "muted"} selected={filter === "completed"} onClick={() => selectFilter("completed")} />
          <Stat label="failed" value={digest.failed} tone={digest.failed ? "bad" : "muted"} selected={filter === "failed"} onClick={() => selectFilter("failed")} />
        </div>
      </div>

      {state !== "open" && (
        <div className="border-b border-amber-300/15 bg-amber-900/15 px-4 py-1.5 text-[11px] text-amber-200/80">
          Gateway not connected — start it with <span className="font-mono">make gateway</span> in{" "}
          <span className="font-mono">agent-system/</span> (or set{" "}
          <span className="font-mono">NEXT_PUBLIC_GATEWAY_WS_URL</span>). Reconnecting…
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[minmax(320px,400px)_minmax(0,1fr)_24rem]">
        {/* Stable task list */}
        <section className="flex min-h-0 flex-col border-r border-[#f3ead3]/10">
          <div className="flex items-center justify-between gap-3 border-b border-[#f3ead3]/10 px-4 py-2">
            <h2 className="flex items-center gap-2 text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
              Agents
              <span className="text-[#f3ead3]/30">
                ({filter === "all" ? tasks.length : `${filteredTasks.length}/${tasks.length}`})
              </span>
            </h2>
            {filter !== "all" && (
              <button
                type="button"
                onClick={() => setFilter("all")}
                className="rounded border border-[#f3ead3]/15 px-2 py-0.5 text-[10px] uppercase tracking-[0.18em] text-[#f3ead3]/55 hover:border-[#f3ead3]/40 hover:text-[#f3ead3]"
              >
                {FILTER_LABEL[filter]} x
              </button>
            )}
          </div>
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
            {tasks.length === 0 ? (
              <p className="px-1 py-16 text-center text-sm text-[#f3ead3]/35">
                No agents running. Dispatch one from the right, or wait for the avatar to delegate from the meeting.
              </p>
            ) : filteredTasks.length === 0 ? (
              <p className="px-1 py-16 text-center text-sm text-[#f3ead3]/35">
                No {FILTER_LABEL[filter]} agents match this view.
              </p>
            ) : (
              filteredTasks.map((row) => (
                <TaskListItem
                  key={row.taskId}
                  row={row}
                  trace={traces[row.taskId]}
                  now={now}
                  selected={selectedTask?.taskId === row.taskId}
                  onSelect={() => setSelectedTaskId(row.taskId)}
                />
              ))
            )}
          </div>
        </section>

        <TaskDetail
          row={selectedTask}
          trace={selectedTask ? traces[selectedTask.taskId] : undefined}
          now={now}
          isStopping={selectedTask ? stopping.has(selectedTask.taskId) : false}
          onStop={stop}
        />

        {/* Right rail: dispatch + stream telemetry */}
        <aside className="flex min-h-0 flex-col border-t border-[#f3ead3]/10 xl:border-l xl:border-t-0">
          <div className="border-b border-[#f3ead3]/10 p-4">
            <AskBox meetingId={meetingId || DEFAULT_MEETING_ID} />
          </div>
          <StreamOverview events={events} tasks={tasks} traces={traces} now={now} />
          <div className="flex items-center justify-between border-b border-[#f3ead3]/10 px-4 py-2">
            <h2 className="text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
              Live feed <span className="text-[#f3ead3]/30">({chains.length})</span>
            </h2>
            <button
              onClick={clear}
              className="rounded border border-[#f3ead3]/15 px-2 py-0.5 text-[10px] text-[#f3ead3]/60 hover:border-[#f3ead3]/40 hover:text-[#f3ead3]"
            >
              clear
            </button>
          </div>
          <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-3 text-[11px]">
            {chains.length === 0 && (
              <p className="px-1 py-8 text-center text-[#f3ead3]/35">
                Streaming agent.results + agent.activity…
              </p>
            )}
            {chains.map((chain) => (
              <FeedChainView key={chain.key} chain={chain} now={now} />
            ))}
          </div>
        </aside>
      </div>
    </main>
  );
}
