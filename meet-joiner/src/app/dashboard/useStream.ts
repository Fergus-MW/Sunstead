"use client";

import { useEffect, useRef, useState } from "react";
import type { Envelope, TracePayload } from "./types";

export type ConnState = "connecting" | "open" | "closed";

/** Accumulated reasoning/output stream for one task, folded from `trace` deltas.
 *  `ts` is the last delta's clock — it counts as liveness so a long-streaming task
 *  isn't falsely flagged "stalled" while only trace (not activity) is flowing. */
export type TaskTrace = { thinking: string; text: string; lastSeq: number; ts: number };

type Options = {
  /** WS base, e.g. ws://localhost:8800/stream. Falls back to NEXT_PUBLIC_GATEWAY_WS_URL. */
  url?: string;
  /** Optional meeting_id filter passed as a query param. */
  meetingId?: string;
  /** Ring-buffer cap so the feed never grows unbounded. */
  max?: number;
};

const DEFAULT_WS =
  process.env.NEXT_PUBLIC_GATEWAY_WS_URL ?? "ws://localhost:8800/stream";

// Max per-task trace transcripts retained; oldest-by-update evicted past this (see the fold).
const MAX_TRACES = 200;

/**
 * Subscribe to the gateway's WS /stream. Auto-reconnects with backoff so the
 * dashboard is usable even before the gateway is online — it just sits in
 * "connecting" and starts filling once the gateway comes up. No throw paths.
 */
export function useStream({ url, meetingId, max = 500 }: Options = {}) {
  const [events, setEvents] = useState<Envelope[]>([]);
  // Reasoning/output deltas are folded into a per-task transcript, separate from `events`
  // — they're high-volume and would otherwise evict task/result events from the ring.
  const [traces, setTraces] = useState<Record<string, TaskTrace>>({});
  const [state, setState] = useState<ConnState>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const closedRef = useRef(false);

  const base = url ?? DEFAULT_WS;

  useEffect(() => {
    closedRef.current = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (closedRef.current) return;
      setState("connecting");
      const target = new URL(base);
      if (meetingId) target.searchParams.set("meeting_id", meetingId);

      let ws: WebSocket;
      try {
        ws = new WebSocket(target.toString());
      } catch {
        scheduleRetry();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        retryRef.current = 0;
        setState("open");
      };
      ws.onmessage = (ev) => {
        try {
          const env = JSON.parse(ev.data) as Envelope;
          if (env.type === "trace") {
            // Fold the delta into the task's transcript. seq is monotonic per task, so
            // skipping seq <= lastSeq drops replayed deltas (ring replay on reconnect)
            // without an unbounded seen-id set — Kafka keys by task_id, so order holds.
            const p = env.payload as unknown as TracePayload;
            setTraces((prev) => {
              const cur = prev[p.task_id] ?? { thinking: "", text: "", lastSeq: 0, ts: 0 };
              if (p.seq <= cur.lastSeq) return prev;
              const next: TaskTrace = {
                thinking: cur.thinking + (p.phase === "thinking" ? p.delta : ""),
                text: cur.text + (p.phase === "text" ? p.delta : ""),
                lastSeq: p.seq,
                ts: Date.parse(env.ts) || Date.now(),
              };
              const merged = { ...prev, [p.task_id]: next };
              // Bound the map: traces accrue automatically (unlike click-bounded sets), so a
              // dashboard left open all day would grow forever. Past MAX_TRACES, evict the
              // least-recently-updated transcript. Cap >> concurrent tasks, so live ones survive.
              const keys = Object.keys(merged);
              if (keys.length > MAX_TRACES) {
                let oldest = keys[0];
                for (const k of keys) if (merged[k].ts < merged[oldest].ts) oldest = k;
                delete merged[oldest];
              }
              return merged;
            });
            return;
          }
          setEvents((prev) => {
            // Dedupe by envelope id: the gateway replays its ring buffer on every
            // connect (so a late-joining browser sees recent events), and the
            // auto-reconnect loop / React dev double-mount reconnect repeatedly —
            // without this, each replay re-appends the same envelopes (and collides
            // React's key={env.id}).
            if (prev.some((e) => e.id === env.id)) return prev;
            const next = [env, ...prev];
            return next.length > max ? next.slice(0, max) : next;
          });
        } catch {
          /* ignore non-JSON frames */
        }
      };
      ws.onerror = () => {
        try {
          ws.close();
        } catch {}
      };
      ws.onclose = () => {
        setState("closed");
        scheduleRetry();
      };
    };

    const scheduleRetry = () => {
      if (closedRef.current) return;
      const delay = Math.min(8000, 500 * 2 ** retryRef.current);
      retryRef.current += 1;
      timer = setTimeout(connect, delay);
    };

    connect();

    return () => {
      closedRef.current = true;
      if (timer) clearTimeout(timer);
      try {
        wsRef.current?.close();
      } catch {}
    };
  }, [base, meetingId, max]);

  const clear = () => {
    setEvents([]);
    setTraces({});
  };

  return { events, traces, state, clear };
}
