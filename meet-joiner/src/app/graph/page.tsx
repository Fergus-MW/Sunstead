"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import ForceGraph from "./ForceGraph";
import NodePanel from "./NodePanel";
import { colorFor, typeLabel, type GraphNode, type Subgraph } from "./types";

type Load =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: Subgraph; source: "overview" | "search" }
  | { kind: "error"; message: string };

const EXAMPLES = ["authentication", "deploy", "standup", "client", "bedrock"];

export default function GraphExplorer() {
  const [query, setQuery] = useState("");
  const [hops, setHops] = useState(2);
  const [load, setLoad] = useState<Load>({ kind: "idle" });
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());

  const loadOverview = useCallback(async () => {
    setLoad({ kind: "loading" });
    setSelected(null);
    try {
      const res = await fetch(`/api/graph/overview?seeds=14&hops=1&node_limit=140`);
      const data = await res.json();
      if (!res.ok) {
        setLoad({ kind: "error", message: data.error ?? "Could not load overview" });
        return;
      }
      setLoad({ kind: "ok", data, source: "overview" });
    } catch (err) {
      setLoad({ kind: "error", message: err instanceof Error ? err.message : "Network error" });
    }
  }, []);

  // Land on a useful view instead of a blank canvas.
  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  const runSearch = useCallback(async (q: string, h: number) => {
    setLoad({ kind: "loading" });
    setSelected(null);
    try {
      const params = new URLSearchParams({ q, hops: String(h), node_limit: "120" });
      const res = await fetch(`/api/graph/subgraph?${params}`);
      const data = await res.json();
      if (!res.ok) {
        setLoad({ kind: "error", message: data.error ?? "Search failed" });
        return;
      }
      setLoad({ kind: "ok", data, source: "search" });
    } catch (err) {
      setLoad({ kind: "error", message: err instanceof Error ? err.message : "Network error" });
    }
  }, []);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (query.trim()) runSearch(query.trim(), hops);
  }

  function runExample(q: string) {
    setQuery(q);
    runSearch(q, hops);
  }

  // Expand a node's neighborhood into the current view on demand.
  const expand = useCallback(async (node: GraphNode) => {
    try {
      const res = await fetch(`/api/graph/entity/${node.id}?hops=1&limit=60`);
      const data = await res.json();
      if (!res.ok) return;
      const incoming: Subgraph = data.subgraph;
      setLoad((cur) => {
        if (cur.kind !== "ok") return cur;
        const nodeMap = new Map(cur.data.nodes.map((n) => [n.id, n]));
        for (const n of incoming.nodes) if (!nodeMap.has(n.id)) nodeMap.set(n.id, n);
        const edgeMap = new Map(cur.data.edges.map((e) => [e.id, e]));
        for (const e of incoming.edges) if (!edgeMap.has(e.id)) edgeMap.set(e.id, e);
        return {
          ...cur,
          data: { nodes: [...nodeMap.values()], edges: [...edgeMap.values()] },
        };
      });
    } catch {
      /* non-fatal */
    }
  }, []);

  const data: Subgraph = useMemo(
    () => (load.kind === "ok" ? load.data : { nodes: [], edges: [] }),
    [load],
  );

  // Legend: node types present, with counts, sorted by frequency.
  const typeCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of data.nodes) m.set(n.type, (m.get(n.type) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [data]);

  function toggleType(t: string) {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  }

  const visibleNodeCount = useMemo(
    () => data.nodes.filter((n) => !hiddenTypes.has(n.type)).length,
    [data, hiddenTypes],
  );

  return (
    <main className="flex h-screen flex-col bg-[#0b1a17] text-[#f3ead3]">
      {/* Header */}
      <header className="flex items-center justify-between border-b border-[#f3ead3]/10 px-5 py-3">
        <div className="flex items-baseline gap-3">
          <h1 className="font-serif text-lg tracking-tight">Sunstead · Knowledge Graph</h1>
          <span className="text-[10px] uppercase tracking-[0.35em] text-[#f3ead3]/40">
            central-kg-api
          </span>
        </div>
        <nav className="flex gap-4 text-xs text-[#f3ead3]/60">
          <Link href="/dashboard" className="underline-offset-4 hover:text-[#f3ead3] hover:underline">
            Dashboard
          </Link>
          <Link href="/" className="underline-offset-4 hover:text-[#f3ead3] hover:underline">
            ← Meeting hall
          </Link>
        </nav>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Left rail */}
        <aside className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto border-r border-[#f3ead3]/10 p-4">
          <form onSubmit={onSubmit} className="space-y-2">
            <label className="text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
              Search the graph
            </label>
            <div className="flex gap-1.5">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="authentication, standup, deploy…"
                className="min-w-0 flex-1 rounded-md border border-[#f3ead3]/15 bg-black/40 px-3 py-2 text-sm outline-none placeholder:text-[#f3ead3]/30 focus:border-[#f3ead3]/60"
              />
              <button
                type="submit"
                disabled={!query.trim() || load.kind === "loading"}
                className="shrink-0 rounded-md bg-[#f3ead3] px-3 py-2 text-sm font-medium text-[#1d2e4a] transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
              >
                {load.kind === "loading" ? "…" : "Go"}
              </button>
            </div>
            <div className="flex items-center justify-between text-xs text-[#f3ead3]/60">
              <label htmlFor="hops">hops: {hops}</label>
              <input
                id="hops"
                type="range"
                min={1}
                max={4}
                value={hops}
                onChange={(e) => setHops(Number(e.target.value))}
                className="w-32 accent-[#f78f3f]"
              />
            </div>
            {/* Example chips */}
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  type="button"
                  onClick={() => runExample(ex)}
                  className="rounded-full border border-[#f3ead3]/15 px-2 py-0.5 text-[11px] text-[#f3ead3]/55 hover:border-[#f3ead3]/45 hover:text-[#f3ead3]"
                >
                  {ex}
                </button>
              ))}
            </div>
          </form>

          {/* Status line + reset to overview */}
          <div className="flex items-center justify-between text-xs">
            {load.kind === "ok" ? (
              <span className="text-[#f3ead3]/50">
                {hiddenTypes.size > 0 ? `${visibleNodeCount}/${data.nodes.length}` : data.nodes.length}{" "}
                nodes · {data.edges.length} edges
                {load.source === "overview" && (
                  <span className="text-[#f3ead3]/35"> · overview</span>
                )}
              </span>
            ) : (
              <span />
            )}
            <button
              onClick={loadOverview}
              className="text-[#f3ead3]/45 underline-offset-2 hover:text-[#f3ead3]/80 hover:underline"
            >
              ↻ Overview
            </button>
          </div>
          {load.kind === "error" && (
            <p className="rounded-md border border-rose-300/30 bg-rose-900/30 p-2 text-xs text-rose-100">
              {load.message}
            </p>
          )}

          {/* Selected node detail */}
          {selected && (
            <NodePanel
              node={selected}
              data={data}
              onSelectNode={setSelected}
              onExpand={expand}
              onClose={() => setSelected(null)}
            />
          )}

          {/* Interactive legend — click a type to show/hide it. */}
          {typeCounts.length > 0 && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <p className="text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
                  Types · click to filter
                </p>
                {hiddenTypes.size > 0 && (
                  <button
                    onClick={() => setHiddenTypes(new Set())}
                    className="text-[10px] text-[#f3ead3]/45 hover:text-[#f3ead3]/80"
                  >
                    show all
                  </button>
                )}
              </div>
              <ul className="space-y-0.5">
                {typeCounts.map(([t, count]) => {
                  const off = hiddenTypes.has(t);
                  return (
                    <li key={t}>
                      <button
                        onClick={() => toggleType(t)}
                        className={`flex w-full items-center gap-2 rounded px-1.5 py-1 text-xs transition-opacity hover:bg-[#f3ead3]/10 ${
                          off ? "opacity-35" : ""
                        }`}
                      >
                        <span
                          className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                          style={{ background: colorFor(t) }}
                        />
                        <span className={off ? "line-through" : ""}>{typeLabel(t)}</span>
                        <span className="ml-auto tabular-nums text-[#f3ead3]/40">{count}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          <p className="mt-auto text-[10px] leading-relaxed text-[#f3ead3]/35">
            Drag to reposition · scroll to zoom · drag canvas to pan · click a node for detail and
            its connections.
          </p>
        </aside>

        {/* Canvas */}
        <section
          className="relative min-w-0 flex-1"
          style={{
            background:
              "radial-gradient(ellipse 70% 70% at 50% 45%, rgba(247,143,63,0.06), rgba(11,26,23,0) 70%)",
          }}
        >
          {load.kind === "ok" && load.source === "overview" && data.nodes.length > 0 && (
            <div className="pointer-events-none absolute left-1/2 top-4 z-10 -translate-x-1/2 rounded-full border border-[#f3ead3]/10 bg-black/40 px-3 py-1 text-[11px] text-[#f3ead3]/55 backdrop-blur">
              Overview · the busiest hubs in your graph — search or click a node to dig in
            </div>
          )}
          {load.kind === "loading" && (
            <div className="absolute inset-0 z-10 flex items-center justify-center text-sm text-[#f3ead3]/40">
              Traversing the graph…
            </div>
          )}
          {load.kind === "ok" && data.nodes.length === 0 && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-8 text-center text-sm text-[#f3ead3]/40">
              <p>No matches. Try a broader term or load the overview.</p>
              <button
                onClick={loadOverview}
                className="rounded-md border border-[#f3ead3]/20 px-3 py-1.5 text-xs text-[#f3ead3]/80 hover:border-[#f3ead3]/50"
              >
                Load overview
              </button>
            </div>
          )}
          <ForceGraph
            data={data}
            selectedId={selected?.id ?? null}
            hiddenTypes={hiddenTypes}
            onSelect={setSelected}
          />
        </section>
      </div>
    </main>
  );
}
