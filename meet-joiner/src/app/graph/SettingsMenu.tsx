"use client";

import { useEffect, useRef, useState } from "react";
import type { Display } from "./types";

type Props = {
  display: Display;
  onChange: (d: Display) => void;
};

// A compact popover of display settings: color mode, label density, spacing.
export default function SettingsMenu({ display, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const set = (patch: Partial<Display>) => onChange({ ...display, ...patch });

  return (
    <div ref={ref} className="absolute right-4 top-4 z-20">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label="Display settings"
        title="Display settings"
        className={`flex h-9 items-center gap-1.5 rounded-lg border px-3 text-xs backdrop-blur transition-colors ${
          open
            ? "border-[#f3ead3]/40 bg-black/60 text-[#f3ead3]"
            : "border-[#f3ead3]/12 bg-black/45 text-[#f3ead3]/75 hover:border-[#f3ead3]/35 hover:text-[#f3ead3]"
        }`}
      >
        <span className="text-sm">⚙</span> Display
      </button>

      {open && (
        <div className="mt-2 w-60 space-y-3.5 rounded-xl border border-[#f3ead3]/12 bg-[#0d201c]/95 p-3.5 shadow-2xl backdrop-blur">
          <Segmented
            label="Color by"
            value={display.colorMode}
            options={[
              ["type", "Type"],
              ["degree", "Links"],
              ["minimal", "Minimal"],
            ]}
            onChange={(v) => set({ colorMode: v as Display["colorMode"] })}
          />
          <Segmented
            label="Labels"
            value={display.labels}
            options={[
              ["hubs", "Hubs"],
              ["all", "All"],
              ["off", "Off"],
            ]}
            onChange={(v) => set({ labels: v as Display["labels"] })}
          />
          <Segmented
            label="Edge labels"
            value={display.edgeLabels}
            options={[
              ["focus", "Focus"],
              ["all", "All"],
              ["off", "Off"],
            ]}
            onChange={(v) => set({ edgeLabels: v as Display["edgeLabels"] })}
          />

          <label className="flex cursor-pointer items-center justify-between text-xs text-[#f3ead3]/70">
            <span>Hide unconnected</span>
            <input
              type="checkbox"
              checked={display.hideIsolated}
              onChange={(e) => set({ hideIsolated: e.target.checked })}
              className="h-3.5 w-3.5 accent-[#f78f3f]"
            />
          </label>

          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs text-[#f3ead3]/70">
              <span>Spacing</span>
              <span className="tabular-nums text-[#f3ead3]/40">{display.spacing.toFixed(1)}×</span>
            </div>
            <input
              type="range"
              min={0.6}
              max={1.8}
              step={0.1}
              value={display.spacing}
              onChange={(e) => set({ spacing: Number(e.target.value) })}
              className="w-full accent-[#f78f3f]"
            />
          </div>
        </div>
      )}
    </div>
  );
}

function Segmented({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: [string, string][];
  onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-1">
      <p className="text-[10px] uppercase tracking-[0.2em] text-[#f3ead3]/40">{label}</p>
      <div className="flex overflow-hidden rounded-md border border-[#f3ead3]/12">
        {options.map(([val, lbl], i) => (
          <button
            key={val}
            type="button"
            onClick={() => onChange(val)}
            className={`flex-1 px-2 py-1 text-xs transition-colors ${i > 0 ? "border-l border-[#f3ead3]/12" : ""} ${
              value === val
                ? "bg-[#f3ead3]/90 font-medium text-[#0b1a17]"
                : "text-[#f3ead3]/65 hover:bg-[#f3ead3]/10"
            }`}
          >
            {lbl}
          </button>
        ))}
      </div>
    </div>
  );
}
