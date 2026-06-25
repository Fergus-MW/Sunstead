"use client";

import { useEffect, useRef, useState } from "react";
import type { Envelope } from "./types";

export type ConnState = "connecting" | "open" | "closed";

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

/**
 * Subscribe to the gateway's WS /stream. Auto-reconnects with backoff so the
 * dashboard is usable even before the gateway is online — it just sits in
 * "connecting" and starts filling once the gateway comes up. No throw paths.
 */
export function useStream({ url, meetingId, max = 500 }: Options = {}) {
  const [events, setEvents] = useState<Envelope[]>([]);
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
          setEvents((prev) => {
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

  const clear = () => setEvents([]);

  return { events, state, clear };
}
