"use client";

import { useState } from "react";

type Status =
  | { kind: "idle" }
  | { kind: "joining" }
  | { kind: "joined"; meetingId: string }
  | { kind: "error"; message: string };

const HORIZON_ART = String.raw`
      ▲      ▲▲       ▲      ▲▲▲      ▲     ▲▲
     ▲▲▲    ▲▲▲▲    ▲▲▲    ▲▲▲▲▲    ▲▲▲   ▲▲▲▲
    ▲▲▲▲▲  ▲▲▲▲▲▲  ▲▲▲▲▲  ▲▲▲▲▲▲▲  ▲▲▲▲▲ ▲▲▲▲▲▲
 ══════════════════════════════════════════════════
 · · · · · · · · · · · · · · · · · · · · · · · · ·
`;

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
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-gradient-to-b from-[#2a0f3d] via-[#5b1a3a] to-[#1a0b2e] px-6 text-amber-50">
      {/* Lava-lamp morphing blobs */}
      <div
        aria-hidden
        className="lava-blob lava-a left-[-10%] top-[-10%] h-[55vmax] w-[55vmax] bg-amber-400/70"
      />
      <div
        aria-hidden
        className="lava-blob lava-b right-[-15%] top-[10%] h-[50vmax] w-[50vmax] bg-rose-500/60"
      />
      <div
        aria-hidden
        className="lava-blob lava-c left-[10%] bottom-[-20%] h-[60vmax] w-[60vmax] bg-fuchsia-700/55"
      />
      <div
        aria-hidden
        className="lava-blob lava-a right-[5%] bottom-[-10%] h-[40vmax] w-[40vmax] bg-indigo-600/50"
      />

      {/* The midnight sun, never setting */}
      <div
        aria-hidden
        className="midnight-sun pointer-events-none absolute left-1/2 top-[18%] h-32 w-32 -translate-x-1/2 rounded-full bg-gradient-to-b from-amber-100 via-amber-300 to-orange-500"
      />

      <div className="relative z-10 w-full max-w-xl space-y-7 pt-24">
        <div className="space-y-2 text-center">
          <p className="text-[10px] uppercase tracking-[0.5em] text-amber-200/80">
            Sunstead · Lapland · 66°33′N
          </p>
          <h1 className="font-serif text-3xl font-semibold tracking-tight text-amber-50 drop-shadow-[0_2px_20px_rgba(251,146,60,0.5)] sm:text-4xl">
            The sun never sets on this meeting
          </h1>
          <p className="text-sm italic text-amber-100/70">
            Paste a Google Meet link. We&apos;ll send an envoy across the
            tundra.
          </p>
        </div>

        <form
          onSubmit={onSubmit}
          className="space-y-3 rounded-2xl border border-amber-100/20 bg-black/30 p-4 shadow-[0_30px_80px_-20px_rgba(0,0,0,0.6)] backdrop-blur-xl"
        >
          <input
            type="url"
            required
            value={link}
            onChange={(e) => setLink(e.target.value)}
            placeholder="https://meet.google.com/abc-defg-hij"
            className="w-full rounded-lg border border-amber-100/20 bg-black/30 px-3 py-2 text-sm text-amber-50 placeholder:text-amber-100/40 outline-none focus:border-rose-300 focus:ring-2 focus:ring-rose-400/40"
          />
          <button
            type="submit"
            disabled={status.kind === "joining" || !link}
            className="w-full rounded-lg bg-gradient-to-r from-amber-400 via-rose-500 to-fuchsia-600 px-3 py-2.5 text-sm font-medium text-amber-50 shadow-[0_8px_30px_-5px_rgba(244,114,182,0.5)] transition-all hover:brightness-110 hover:shadow-[0_8px_40px_-5px_rgba(244,114,182,0.7)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {status.kind === "joining"
              ? "Tracking your envoy across the tundra…"
              : "Send the envoy ☼"}
          </button>
        </form>

        {status.kind === "joined" && (
          <div className="rounded-xl border border-emerald-300/30 bg-emerald-500/10 p-4 text-center text-sm text-emerald-100 shadow backdrop-blur-md">
            <pre
              aria-hidden
              className="mb-2 select-none whitespace-pre text-center text-[10px] leading-tight text-emerald-200/70"
            >
              {`  ╭─ aurora ─╮\n   〜〜〜〜〜〜\n  ╰──────────╯`}
            </pre>
            The envoy has reached the meeting hall.
            <div className="mt-1 font-mono text-xs text-emerald-200/80">
              meeting · {status.meetingId}
            </div>
          </div>
        )}
        {status.kind === "error" && (
          <div className="rounded-xl border border-rose-300/30 bg-rose-500/10 p-3 text-center text-sm text-rose-100 shadow backdrop-blur-md">
            ❄ {status.message}
          </div>
        )}

        <pre
          aria-hidden
          className="select-none whitespace-pre text-center text-[10px] leading-tight text-indigo-100/40 sm:text-xs"
        >
          {HORIZON_ART}
        </pre>

        <p className="text-center text-[10px] uppercase tracking-[0.3em] text-amber-100/50">
          24h daylight · midnight sun · perpetual quorum
        </p>
      </div>
    </main>
  );
}
