"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import AskBox from "./AskBox";
import { useStream, type ConnState } from "./useStream";
import type { Artifact, Envelope } from "./types";

type TaskRow = {
  taskId: string;
  intentHint?: string;
  latestStatus: string;
  detail?: string;
  terminal?: "completed" | "failed";
  result?: Record<string, unknown> | null;
  artifacts: Artifact[];
  error?: string | null;
  lastTs: string;
  activityCount: number;
};

const STATUS_DOT: Record<ConnState, string> = {
  connecting: "bg-amber-300",
  open: "bg-emerald-400",
  closed: "bg-rose-400",
};

function fmtTime(ts: string): string {
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleTimeString();
}

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
      } as TaskRow);

    if (env.type === "activity") {
      row.latestStatus = (p.status as string) ?? row.latestStatus;
      row.detail = (p.detail as string) ?? row.detail;
      row.activityCount += 1;
    } else if (env.type === "task.completed" || env.type === "task.failed") {
      row.terminal = env.type === "task.completed" ? "completed" : "failed";
      row.latestStatus = (p.status as string) ?? row.terminal;
      row.result = (p.result as Record<string, unknown>) ?? null;
      row.artifacts = (p.artifacts as Artifact[]) ?? [];
      row.error = (p.error as string) ?? null;
    }
    row.lastTs = env.ts;
    map.set(taskId, row);
  }
  return [...map.values()].sort((a, b) => (a.lastTs < b.lastTs ? 1 : -1));
}

function StatusPill({ row }: { row: TaskRow }) {
  const cls = row.terminal === "completed"
    ? "bg-emerald-900/40 text-emerald-200 border-emerald-300/30"
    : row.terminal === "failed"
      ? "bg-rose-900/40 text-rose-200 border-rose-300/30"
      : "bg-amber-900/30 text-amber-200 border-amber-300/30";
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide ${cls}`}>
      {row.latestStatus}
    </span>
  );
}

export default function Dashboard() {
  const [meetingId, setMeetingId] = useState("");
  // Seed the meeting filter from ?meeting_id= (the landing page links here scoped
  // to the call the envoy just joined), so the avatar's delegated results show up
  // without the user retyping the id. Done on mount to avoid a hydration mismatch.
  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("meeting_id");
    if (fromUrl) setMeetingId(fromUrl);
  }, []);
  const { events, state, clear } = useStream({ meetingId: meetingId || undefined });
  const tasks = useMemo(() => deriveTasks(events), [events]);

  return (
    <main className="flex h-screen flex-col bg-[#0b1a17] text-[#f3ead3]">
      <header className="flex items-center justify-between border-b border-[#f3ead3]/10 px-5 py-3">
        <div className="flex items-baseline gap-3">
          <h1 className="font-serif text-lg tracking-tight">Sunstead · Agent Dashboard</h1>
          <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
            <span className={`inline-block h-2 w-2 rounded-full ${STATUS_DOT[state]}`} />
            {state}
          </span>
        </div>
        <nav className="flex gap-4 text-xs text-[#f3ead3]/60">
          <Link href="/graph" className="underline-offset-4 hover:text-[#f3ead3] hover:underline">
            Graph
          </Link>
          <Link href="/" className="underline-offset-4 hover:text-[#f3ead3] hover:underline">
            ← Meeting hall
          </Link>
        </nav>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Left rail: filter + ask box */}
        <aside className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto border-r border-[#f3ead3]/10 p-4">
          <div className="space-y-1.5">
            <label className="text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
              Filter by meeting
            </label>
            <input
              value={meetingId}
              onChange={(e) => setMeetingId(e.target.value)}
              placeholder="all meetings"
              className="w-full rounded-md border border-[#f3ead3]/15 bg-black/40 px-3 py-2 text-sm outline-none placeholder:text-[#f3ead3]/30 focus:border-[#f3ead3]/60"
            />
          </div>

          <AskBox meetingId={meetingId || "mtg_dev"} />

          <button
            onClick={clear}
            className="rounded border border-[#f3ead3]/15 px-2 py-1.5 text-xs text-[#f3ead3]/70 hover:border-[#f3ead3]/40 hover:text-[#f3ead3]"
          >
            Clear feed
          </button>

          {state !== "open" && (
            <p className="rounded-md border border-[#f3ead3]/15 bg-black/30 p-2 text-[11px] leading-relaxed text-[#f3ead3]/50">
              Gateway not connected. Start it with{" "}
              <span className="font-mono">make gateway</span> in{" "}
              <span className="font-mono">agent-system/</span>, or set{" "}
              <span className="font-mono">NEXT_PUBLIC_GATEWAY_WS_URL</span>. Reconnecting
              automatically.
            </p>
          )}
        </aside>

        {/* Task board */}
        <section className="flex min-w-0 flex-1 flex-col border-r border-[#f3ead3]/10">
          <h2 className="border-b border-[#f3ead3]/10 px-4 py-2 text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
            Tasks ({tasks.length})
          </h2>
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
            {tasks.length === 0 && (
              <p className="px-1 py-8 text-center text-sm text-[#f3ead3]/35">
                No tasks yet. Dispatch one from the left, or wait for the avatar to delegate.
              </p>
            )}
            {tasks.map((row) => (
              <div
                key={row.taskId}
                className="rounded-md border border-[#f3ead3]/15 bg-black/25 p-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-xs text-[#f3ead3]/80">{row.taskId}</span>
                  <StatusPill row={row} />
                </div>
                {row.detail && (
                  <p className="mt-1 text-xs text-[#f3ead3]/60">{row.detail}</p>
                )}
                {row.error && (
                  <p className="mt-1 text-xs text-rose-200/80">{row.error}</p>
                )}
                {row.result && Object.keys(row.result).length > 0 && (
                  <pre className="mt-2 max-h-32 overflow-auto rounded bg-black/40 p-2 text-[10px] leading-relaxed text-[#f3ead3]/70">
                    {JSON.stringify(row.result, null, 2)}
                  </pre>
                )}
                {row.artifacts.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {row.artifacts.map((a, i) => (
                      <li key={i} className="text-xs">
                        {a.kind === "url" ? (
                          <a
                            href={a.value}
                            target="_blank"
                            rel="noreferrer"
                            className="text-sky-300 underline underline-offset-2 hover:text-sky-200"
                          >
                            {a.value}
                          </a>
                        ) : (
                          <span className="text-[#f3ead3]/70">
                            {a.kind}: {a.value}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-2 text-[10px] text-[#f3ead3]/35">
                  {row.activityCount} updates · {fmtTime(row.lastTs)}
                </p>
              </div>
            ))}
          </div>
        </section>

        {/* Live event feed */}
        <section className="flex w-96 shrink-0 flex-col">
          <h2 className="border-b border-[#f3ead3]/10 px-4 py-2 text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
            Live feed ({events.length})
          </h2>
          <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-3 font-mono text-[11px]">
            {events.length === 0 && (
              <p className="px-1 py-8 text-center text-[#f3ead3]/35">
                Streaming agent.results + agent.activity…
              </p>
            )}
            {events.map((env) => {
              const p = env.payload as Record<string, unknown>;
              const tid = typeof p.task_id === "string" ? p.task_id : "";
              const status = (p.status as string) ?? "";
              const detail = (p.detail as string) ?? (p.error as string) ?? "";
              return (
                <div key={env.id} className="border-b border-[#f3ead3]/5 pb-1">
                  <div className="flex justify-between gap-2 text-[#f3ead3]/80">
                    <span>{env.type}</span>
                    <span className="text-[#f3ead3]/35">{fmtTime(env.ts)}</span>
                  </div>
                  <div className="text-[#f3ead3]/55">
                    {tid && <span className="text-[#f3ead3]/40">{tid} </span>}
                    {status}
                    {detail && ` · ${detail}`}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </main>
  );
}
