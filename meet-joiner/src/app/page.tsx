"use client";

import { useState } from "react";

type Status =
  | { kind: "idle" }
  | { kind: "joining" }
  | { kind: "joined"; meetingId: string }
  | { kind: "error"; message: string };

export default function Home() {
  const [link, setLink] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setStatus({ kind: "joining" });
    try {
      const res = await fetch("/api/join", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ link }),
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus({ kind: "error", message: data.error ?? "Failed to join" });
        return;
      }
      setStatus({ kind: "joined", meetingId: data.meetingId });
    } catch (err) {
      setStatus({
        kind: "error",
        message: err instanceof Error ? err.message : "Network error",
      });
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-white px-6 text-zinc-900 dark:bg-black dark:text-zinc-100">
      <div className="w-full max-w-md space-y-6">
        <div className="space-y-2">
          <h1 className="text-xl font-medium">Send the agent to a meeting</h1>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Paste a Google Meet link. The agent will attempt to join.
          </p>
        </div>

        <form onSubmit={onSubmit} className="space-y-3">
          <input
            type="url"
            required
            value={link}
            onChange={(e) => setLink(e.target.value)}
            placeholder="https://meet.google.com/abc-defg-hij"
            className="w-full rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-zinc-900 dark:border-zinc-700 dark:focus:border-zinc-100"
          />
          <button
            type="submit"
            disabled={status.kind === "joining" || !link}
            className="w-full rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
          >
            {status.kind === "joining" ? "Joining…" : "Join meeting"}
          </button>
        </form>

        {status.kind === "joined" && (
          <p className="text-sm text-emerald-600 dark:text-emerald-400">
            Agent dispatched. Meeting id: {status.meetingId}
          </p>
        )}
        {status.kind === "error" && (
          <p className="text-sm text-red-600 dark:text-red-400">
            {status.message}
          </p>
        )}
      </div>
    </main>
  );
}
