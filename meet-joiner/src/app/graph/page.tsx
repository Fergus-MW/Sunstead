"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import ForceGraph from "./ForceGraph";
import { colorFor, type GraphNode, type Subgraph } from "./types";

type Load =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: Subgraph }
  | { kind: "error"; message: string };

export default function GraphExplorer() {
  const [query, setQuery] = useState("");
  const [hops, setHops] = useState(2);
  const [load, setLoad] = useState<Load>({ kind: "idle" });
  const [selected, setSelected] = useState<GraphNode | null>(null);

  const runSearch = useCallback(
    async (q: string, h: number) => {
      setLoad({ kind: "loading" });
      try {
        const params = new URLSearchParams({
          q,
          hops: String(h),
          node_limit: "120",
        });
        const res = await fetch(`/api/graph/subgraph?${params}`);
        const data = await res.json();
        if (!res.ok) {
          setLoad({ kind: "error", message: data.error ?? "Search failed" });
          return;
        }
        setLoad({ kind: "ok", data });
      } catch (err) {
        setLoad({
          kind: "error",
          message: err instanceof Error ? err.message : "Network error",
        });
      }
    },
    [],
  );

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (query.trim()) runSearch(query.trim(), hops);
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
          kind: "ok",
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

  // Legend: which node types are actually present.
  const presentTypes = useMemo(() => {
    const t = new Set(data.nodes.map((n) => n.type));
    return [...t].sort();
  }, [data]);

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
        <Link
          href="/"
          className="text-xs text-[#f3ead3]/60 underline-offset-4 hover:text-[#f3ead3] hover:underline"
        >
          ← Meeting hall
        </Link>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Left rail */}
        <aside className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto border-r border-[#f3ead3]/10 p-4">
          <form onSubmit={onSubmit} className="space-y-2">
            <label className="text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">
              Search the graph
            </label>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. authentication, standup, deploy…"
              className="w-full rounded-md border border-[#f3ead3]/15 bg-black/40 px-3 py-2 text-sm outline-none placeholder:text-[#f3ead3]/30 focus:border-[#f3ead3]/60"
            />
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
            <button
              type="submit"
              disabled={!query.trim() || load.kind === "loading"}
              className="w-full rounded-md bg-[#f3ead3] px-3 py-2 text-sm font-medium text-[#1d2e4a] transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {load.kind === "loading" ? "Traversing…" : "Explore"}
            </button>
          </form>

          {load.kind === "ok" && (
            <p className="text-xs text-[#f3ead3]/50">
              {data.nodes.length} nodes · {data.edges.length} edges
            </p>
          )}
          {load.kind === "error" && (
            <p className="rounded-md border border-rose-300/30 bg-rose-900/30 p-2 text-xs text-rose-100">
              {load.message}
            </p>
          )}

          {/* Selected node detail */}
          {selected && (
            <div className="space-y-2 rounded-md border border-[#f3ead3]/15 bg-black/30 p-3">
              <div className="flex items-center gap-2">
                <span
                  className="inline-block h-3 w-3 rounded-full"
                  style={{ background: colorFor(selected.type) }}
                />
                <span className="text-[10px] uppercase tracking-[0.25em] text-[#f3ead3]/50">
                  {selected.type}
                </span>
              </div>
              <p className="break-words text-sm font-medium">{selected.name}</p>
              {Object.keys(selected.properties ?? {}).length > 0 && (
                <pre className="max-h-48 overflow-auto rounded bg-black/40 p-2 text-[10px] leading-relaxed text-[#f3ead3]/70">
                  {JSON.stringify(selected.properties, null, 2)}
                </pre>
              )}
              <button
                onClick={() => expand(selected)}
                className="w-full rounded border border-[#f3ead3]/20 px-2 py-1.5 text-xs text-[#f3ead3]/80 hover:border-[#f3ead3]/50 hover:text-[#f3ead3]"
              >
                Expand neighbors
              </button>
            </div>
          )}

          {/* Legend */}
          {presentTypes.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-[10px] uppercase tracking-[0.3em] text-[#f3ead3]/50">Legend</p>
              <ul className="space-y-1">
                {presentTypes.map((t) => (
                  <li key={t} className="flex items-center gap-2 text-xs text-[#f3ead3]/70">
                    <span
                      className="inline-block h-2.5 w-2.5 rounded-full"
                      style={{ background: colorFor(t) }}
                    />
                    {t}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="mt-auto text-[10px] leading-relaxed text-[#f3ead3]/35">
            Drag nodes to reposition · scroll to zoom · drag canvas to pan · click a node for
            detail, then “Expand neighbors”.
          </p>
        </aside>

        {/* Canvas */}
        <section className="relative min-w-0 flex-1">
          {load.kind === "idle" && (
            <div className="absolute inset-0 flex items-center justify-center px-8 text-center text-sm text-[#f3ead3]/40">
              Search the knowledge graph to render its neighborhood.
            </div>
          )}
          {load.kind === "ok" && data.nodes.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center text-sm text-[#f3ead3]/40">
              No matches. Try a broader term.
            </div>
          )}
          <ForceGraph data={data} selectedId={selected?.id ?? null} onSelect={setSelected} />
        </section>
      </div>
    </main>
  );
}
