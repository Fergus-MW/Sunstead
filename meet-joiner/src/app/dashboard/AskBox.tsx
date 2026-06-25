"use client";

import { useState, type FormEvent } from "react";
import { INTENT_META, INTENT_TEMPLATES, INTENTS, type Intent } from "./types";

type Sent =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "ok"; message: string; taskId?: string; topic?: string }
  | { kind: "error"; message: string };

const DEFAULT_INTENT: Intent = "ask";
type DispatchMode = "planner" | "direct";

function parseTemplate(intent: Intent): Record<string, unknown> {
  try {
    const parsed = JSON.parse(INTENT_TEMPLATES[intent]);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // The templates are local constants; fall back defensively if one is edited badly.
  }
  return {};
}

function formatArgs(args: Record<string, unknown>): string {
  return JSON.stringify(args, null, 2);
}

function promptKeyFor(intent: Intent): string | null {
  return INTENT_META[intent].promptKey ?? (intent === "echo" ? "text" : null);
}

function promptFromArgs(intent: Intent, args: Record<string, unknown>): string {
  const key = promptKeyFor(intent);
  if (!key) return "";
  const value = args[key];
  return typeof value === "string" ? value : "";
}

function argsWithPrompt(intent: Intent, prompt: string): Record<string, unknown> {
  const args = parseTemplate(intent);
  const key = promptKeyFor(intent);
  if (key) args[key] = prompt;
  return args;
}

function intentLabel(intent: Intent): string {
  return intent.replaceAll("_", " ");
}

// Dispatch a task to the agent suite via the gateway (proxied through /api/tasks).
export default function AskBox({ meetingId }: { meetingId: string }) {
  const initialArgs = parseTemplate(DEFAULT_INTENT);
  const [mode, setMode] = useState<DispatchMode>("planner");
  const [intent, setIntent] = useState<Intent>(DEFAULT_INTENT);
  const [prompt, setPrompt] = useState(() => promptFromArgs(DEFAULT_INTENT, initialArgs));
  const [rawArgs, setRawArgs] = useState(() => formatArgs(initialArgs));
  const [advanced, setAdvanced] = useState(false);
  const [sent, setSent] = useState<Sent>({ kind: "idle" });

  function onIntentChange(next: Intent) {
    const nextArgs = parseTemplate(next);
    setIntent(next);
    setPrompt(promptFromArgs(next, nextArgs));
    setRawArgs(formatArgs(nextArgs));
    setSent({ kind: "idle" });
  }

  function onAdvancedChange(next: boolean) {
    if (next) {
      setRawArgs(formatArgs(argsWithPrompt(intent, prompt)));
    } else {
      try {
        const parsed = JSON.parse(rawArgs);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          setPrompt(promptFromArgs(intent, parsed as Record<string, unknown>));
        }
      } catch {
        // Keep the last plain prompt if the advanced draft is not parseable yet.
      }
    }
    setAdvanced(next);
    setSent({ kind: "idle" });
  }

  function readAdvancedArgs(): Record<string, unknown> | null {
    try {
      const parsed = rawArgs.trim() ? JSON.parse(rawArgs) : {};
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setSent({ kind: "error", message: "Args JSON must be an object." });
        return null;
      }
      return parsed as Record<string, unknown>;
    } catch {
      setSent({ kind: "error", message: "Args JSON is not valid." });
      return null;
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (mode === "planner") {
      const text = prompt.trim();
      if (!text) {
        setSent({ kind: "error", message: "Enter a request before sending." });
        return;
      }
      setSent({ kind: "sending" });
      try {
        const res = await fetch("/api/transcript", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ text, meeting_id: meetingId, speaker: "Dashboard", is_final: true }),
        });
        const data = await res.json();
        if (!res.ok) {
          setSent({ kind: "error", message: data.error ?? "Planner dispatch failed" });
          return;
        }
        setSent({ kind: "ok", message: `sent to planner - ${data.topic ?? "meeting.transcript"}` });
      } catch (err) {
        setSent({
          kind: "error",
          message: err instanceof Error ? err.message : "Network error",
        });
      }
      return;
    }

    const parsedArgs = advanced ? readAdvancedArgs() : argsWithPrompt(intent, prompt.trim());
    if (!parsedArgs) return;

    const promptKey = promptKeyFor(intent);
    if (promptKey) {
      const value = parsedArgs[promptKey];
      if (typeof value !== "string" || !value.trim()) {
        setSent({ kind: "error", message: `Enter a ${promptKey} before sending.` });
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
      setSent({ kind: "ok", message: "accepted", taskId: data.task_id, topic: data.topic });
    } catch (err) {
      setSent({
        kind: "error",
        message: err instanceof Error ? err.message : "Network error",
      });
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <label className="text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
          Dispatch a task
        </label>
        {mode === "direct" && (
          <label className="inline-flex cursor-pointer items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-[#f3ead3]/45">
            <input
              type="checkbox"
              checked={advanced}
              onChange={(e) => onAdvancedChange(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-[#f3ead3]/20 bg-black/40 accent-[#f3ead3]"
            />
            Advanced JSON
          </label>
        )}
      </div>
      <div className="grid grid-cols-2 overflow-hidden rounded-md border border-[#f3ead3]/15 bg-black/30 text-xs">
        {(["planner", "direct"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => {
              setMode(m);
              setSent({ kind: "idle" });
            }}
            className={`px-3 py-1.5 uppercase tracking-[0.18em] transition-colors ${
              mode === m ? "bg-[#f3ead3]/15 text-[#f3ead3]" : "text-[#f3ead3]/45 hover:bg-[#f3ead3]/8"
            }`}
          >
            {m}
          </button>
        ))}
      </div>
      {mode === "direct" && (
        <select
          value={intent}
          onChange={(e) => onIntentChange(e.target.value as Intent)}
          className="w-full rounded-md border border-[#f3ead3]/15 bg-black/40 px-3 py-2 text-sm capitalize outline-none focus:border-[#f3ead3]/60"
        >
          {INTENTS.map((i) => (
            <option key={i} value={i} className="bg-[#0b1a17]">
              {intentLabel(i)}
            </option>
          ))}
        </select>
      )}
      <p className="text-[11px] leading-snug text-[#f3ead3]/45">
        {mode === "planner"
          ? "Natural language request. The planner can split it into web, research, data, KG, or meeting-ops tasks."
          : INTENT_META[intent].blurb}
      </p>
      {advanced && mode === "direct" ? (
        <textarea
          aria-label="Args JSON"
          value={rawArgs}
          onChange={(e) => setRawArgs(e.target.value)}
          rows={5}
          spellCheck={false}
          className="w-full rounded-md border border-[#f3ead3]/15 bg-black/40 px-3 py-2 font-mono text-xs outline-none focus:border-[#f3ead3]/60"
        />
      ) : (
        <textarea
          aria-label="Task prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={4}
          className="w-full rounded-md border border-[#f3ead3]/15 bg-black/40 px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-[#f3ead3]/25 focus:border-[#f3ead3]/60"
          placeholder="What should the agent do?"
        />
      )}
      <button
        type="submit"
        disabled={sent.kind === "sending"}
        className="w-full rounded-md bg-[#f3ead3] px-3 py-2 text-sm font-medium text-[#1d2e4a] transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
      >
        {sent.kind === "sending" ? "Dispatching..." : mode === "planner" ? "Send to planner" : "Send task"}
      </button>
      {sent.kind === "ok" && (
        <p className="text-xs text-emerald-200/80">
          {sent.message}
          {sent.taskId && <> - <span className="font-mono">{sent.taskId}</span></>}
          {sent.topic && <> - {sent.topic}</>}
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
