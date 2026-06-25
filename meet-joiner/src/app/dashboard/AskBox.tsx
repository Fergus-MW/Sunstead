"use client";

import { useState } from "react";
import { INTENT_META, INTENT_TEMPLATES, INTENTS, type Intent } from "./types";

type Sent =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "ok"; taskId: string; topic: string }
  | { kind: "error"; message: string };

// Dispatch a task to the agent suite via the gateway (proxied through /api/tasks).
export default function AskBox({ meetingId }: { meetingId: string }) {
  const [intent, setIntent] = useState<Intent>("echo");
  const [args, setArgs] = useState<string>(INTENT_TEMPLATES.echo);
  const [sent, setSent] = useState<Sent>({ kind: "idle" });

  function onIntentChange(next: Intent) {
    setIntent(next);
    setArgs(INTENT_TEMPLATES[next]);
    setSent({ kind: "idle" });
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    let parsedArgs: unknown;
    try {
      parsedArgs = args.trim() ? JSON.parse(args) : {};
    } catch {
      setSent({ kind: "error", message: "args is not valid JSON" });
      return;
    }
    // Guard the silent no-op: each non-echo intent reads its prompt from one key
    // (question/brief). If that key is missing or blank, the agent falls back to a
    // nonsense prompt and "completes" without doing what was asked — so reject it here.
    const { promptKey } = INTENT_META[intent];
    if (promptKey) {
      const obj =
        parsedArgs && typeof parsedArgs === "object" && !Array.isArray(parsedArgs)
          ? (parsedArgs as Record<string, unknown>)
          : {};
      const v = obj[promptKey];
      if (typeof v !== "string" || !v.trim()) {
        setSent({
          kind: "error",
          message: `“${intent}” reads its prompt from "${promptKey}" — that field is empty. Put your request there.`,
        });
        return;
      }
    }
    setSent({ kind: "sending" });
    try {
      const res = await fetch("/api/tasks", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ intent, args: parsedArgs, meeting_id: meetingId }),
      });
      const data = await res.json();
      if (!res.ok) {
        setSent({ kind: "error", message: data.error ?? "Dispatch failed" });
        return;
      }
      setSent({ kind: "ok", taskId: data.task_id, topic: data.topic });
    } catch (err) {
      setSent({
        kind: "error",
        message: err instanceof Error ? err.message : "Network error",
      });
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-2">
      <label className="text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
        Dispatch a task
      </label>
      <select
        value={intent}
        onChange={(e) => onIntentChange(e.target.value as Intent)}
        className="w-full rounded-md border border-[#f3ead3]/15 bg-black/40 px-3 py-2 text-sm outline-none focus:border-[#f3ead3]/60"
      >
        {INTENTS.map((i) => (
          <option key={i} value={i} className="bg-[#0b1a17]">
            {i}
          </option>
        ))}
      </select>
      <p className="text-[11px] leading-snug text-[#f3ead3]/45">
        {INTENT_META[intent].blurb}
      </p>
      <textarea
        value={args}
        onChange={(e) => setArgs(e.target.value)}
        rows={3}
        spellCheck={false}
        className="w-full rounded-md border border-[#f3ead3]/15 bg-black/40 px-3 py-2 font-mono text-xs outline-none focus:border-[#f3ead3]/60"
      />
      <button
        type="submit"
        disabled={sent.kind === "sending"}
        className="w-full rounded-md bg-[#f3ead3] px-3 py-2 text-sm font-medium text-[#1d2e4a] transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
      >
        {sent.kind === "sending" ? "Dispatching…" : "Send task"}
      </button>
      {sent.kind === "ok" && (
        <p className="text-xs text-emerald-200/80">
          accepted · <span className="font-mono">{sent.taskId}</span> → {sent.topic}
        </p>
      )}
      {sent.kind === "error" && (
        <p className="rounded-md border border-rose-300/30 bg-rose-900/30 p-2 text-xs text-rose-100">
          {sent.message}
        </p>
      )}
    </form>
  );
}
