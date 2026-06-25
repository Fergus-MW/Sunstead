"use client";

import Link from "next/link";
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
    <main className="relative flex min-h-screen flex-col overflow-hidden bg-gradient-to-b from-[#1d2e4a] via-[#3a3658] to-[#5a3d4a] text-[#f3ead3]">
      {/* Orbiting liquid sun */}
      <div className="sun-orbit z-0">
        <div className="sun-body" />
      </div>

      {/* Lapland landscape: layered pine forest */}
      <svg
        aria-hidden
        viewBox="0 0 1440 420"
        preserveAspectRatio="xMidYMax slice"
        className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-[55vh] w-full"
      >
        <defs>
          <linearGradient id="far-hill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#2c4a3a" />
            <stop offset="100%" stopColor="#1a3026" />
          </linearGradient>
          <linearGradient id="mid-forest" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#163025" />
            <stop offset="100%" stopColor="#0c1f18" />
          </linearGradient>
          <linearGradient id="near-forest" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#0a1c14" />
            <stop offset="100%" stopColor="#040b08" />
          </linearGradient>
          <linearGradient id="snow-line" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="rgba(243,234,211,0.35)" />
            <stop offset="100%" stopColor="rgba(243,234,211,0)" />
          </linearGradient>
        </defs>

        {/* Distant rolling hills */}
        <path
          d="M0 220 L60 200 L140 180 L240 200 L340 170 L440 195 L560 175 L680 200 L800 180 L920 205 L1040 175 L1160 200 L1280 185 L1380 205 L1440 195 L1440 420 L0 420 Z"
          fill="url(#far-hill)"
        />

        {/* Subtle snow shimmer */}
        <rect
          x="0"
          y="195"
          width="1440"
          height="35"
          fill="url(#snow-line)"
          className="shimmer"
        />

        {/* Midground pines */}
        <g fill="url(#mid-forest)">
          {Array.from({ length: 26 }).map((_, i) => {
            const x = i * 58 + (i % 3) * 6;
            const h = 60 + ((i * 7) % 40);
            const w = 28 + ((i * 5) % 10);
            return (
              <polygon
                key={`mid-${i}`}
                points={`${x},${260 - h} ${x - w / 2},${260} ${x + w / 2},${260}`}
              />
            );
          })}
          <rect x="0" y="258" width="1440" height="40" />
        </g>

        {/* Foreground pines — taller, denser */}
        <g fill="url(#near-forest)">
          {Array.from({ length: 22 }).map((_, i) => {
            const x = i * 70 + (i % 2) * 18;
            const h = 110 + ((i * 13) % 70);
            const w = 50 + ((i * 9) % 18);
            const tipY = 340 - h;
            // Three-tier pine: stack of triangles
            return (
              <g key={`near-${i}`}>
                <polygon
                  points={`${x},${tipY} ${x - w / 3},${tipY + h * 0.35} ${x + w / 3},${tipY + h * 0.35}`}
                />
                <polygon
                  points={`${x},${tipY + h * 0.2} ${x - w / 2},${tipY + h * 0.65} ${x + w / 2},${tipY + h * 0.65}`}
                />
                <polygon
                  points={`${x},${tipY + h * 0.45} ${x - w / 1.6},${tipY + h} ${x + w / 1.6},${tipY + h}`}
                />
              </g>
            );
          })}
          <rect x="0" y="335" width="1440" height="85" />
        </g>
      </svg>

      {/* Content */}
      <div className="relative z-20 mx-auto flex w-full max-w-xl flex-1 flex-col justify-center px-6 pb-[40vh] pt-20">
        <div className="space-y-2 text-center">
          <p className="text-[10px] uppercase tracking-[0.5em] text-[#f3ead3]/60">
            Sunstead · Lapland · 66°33′N
          </p>
          <h1 className="font-serif text-3xl font-medium tracking-tight text-[#f3ead3] sm:text-4xl">
            The sun never sets on this meeting
          </h1>
          <p className="text-sm italic text-[#f3ead3]/70">
            Paste a Google Meet link. We&apos;ll send an envoy across the
            tundra.
          </p>
        </div>

        <form
          onSubmit={onSubmit}
          className="mt-7 space-y-3 rounded-xl border border-[#f3ead3]/15 bg-black/30 p-4 backdrop-blur-md"
        >
          <input
            type="url"
            required
            value={link}
            onChange={(e) => setLink(e.target.value)}
            placeholder="https://meet.google.com/abc-defg-hij"
            className="w-full rounded-md border border-[#f3ead3]/15 bg-black/40 px-3 py-2 text-sm text-[#f3ead3] placeholder:text-[#f3ead3]/35 outline-none focus:border-[#f3ead3]/60"
          />
          <button
            type="submit"
            disabled={status.kind === "joining" || !link}
            className="w-full rounded-md bg-[#f3ead3] px-3 py-2 text-sm font-medium text-[#1d2e4a] transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {status.kind === "joining"
              ? "Sending envoy across the tundra…"
              : "Send the envoy"}
          </button>
        </form>

        {status.kind === "joined" && (
          <div className="mt-4 rounded-md border border-[#f3ead3]/20 bg-black/30 p-3 text-center text-sm text-[#f3ead3] backdrop-blur-md">
            Envoy has reached the meeting hall.
            <div className="mt-1 font-mono text-xs text-[#f3ead3]/60">
              meeting · {status.meetingId}
            </div>
            <Link
              href={`/dashboard?meeting_id=${encodeURIComponent(status.meetingId)}`}
              className="mt-2 inline-block text-xs underline underline-offset-4 transition-colors hover:text-white"
            >
              Watch the agents work →
            </Link>
          </div>
        )}
        {status.kind === "error" && (
          <div className="mt-4 rounded-md border border-rose-300/30 bg-rose-900/30 p-3 text-center text-sm text-rose-100 backdrop-blur-md">
            {status.message}
          </div>
        )}
      </div>

      <div className="relative z-20 mx-auto mb-3 flex gap-5 text-xs text-[#f3ead3]/60">
        <Link
          href="/graph"
          className="underline-offset-4 transition-colors hover:text-[#f3ead3] hover:underline"
        >
          Explore the knowledge graph →
        </Link>
        <Link
          href="/dashboard"
          className="underline-offset-4 transition-colors hover:text-[#f3ead3] hover:underline"
        >
          Agent dashboard →
        </Link>
      </div>

      <p className="relative z-20 mb-4 text-center text-[10px] uppercase tracking-[0.4em] text-[#f3ead3]/40">
        24h daylight · midnight sun · perpetual quorum
      </p>
    </main>
  );
}
