// Time formatting shared across the admin surfaces.

export function fmtTime(ts: string): string {
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleTimeString();
}

// Compact "time ago" for live feeds: 12s, 3m, 2h, 4d. Falls back to fmtTime.
export function relativeTime(ts: string, now: number = Date.now()): string {
  const t = new Date(ts).getTime();
  if (isNaN(t)) return ts;
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}
