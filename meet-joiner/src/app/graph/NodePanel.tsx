"use client";

import { useMemo, useState } from "react";
import {
  colorFor,
  edgeLabel,
  formatValue,
  humanizeKey,
  typeLabel,
  type GraphNode,
  type Subgraph,
} from "./types";

type Rel = { edgeId: string; rel: string; dir: "out" | "in"; other: GraphNode };

type Props = {
  node: GraphNode;
  data: Subgraph;
  onSelectNode: (node: GraphNode) => void;
  onExpand: (node: GraphNode) => void;
  onClose: () => void;
};

// The detail view for a clicked node: identity, key properties, and — most
// usefully — its connections, which double as navigation into the graph.
export default function NodePanel({ node, data, onSelectNode, onExpand, onClose }: Props) {
  const [showRaw, setShowRaw] = useState(false);

  const nodeMap = useMemo(() => new Map(data.nodes.map((n) => [n.id, n])), [data.nodes]);

  const rels = useMemo<Rel[]>(() => {
    const out: Rel[] = [];
    for (const e of data.edges) {
      if (e.source_node_id === node.id) {
        const other = nodeMap.get(e.target_node_id);
        if (other) out.push({ edgeId: e.id, rel: edgeLabel(e.type), dir: "out", other });
      } else if (e.target_node_id === node.id) {
        const other = nodeMap.get(e.source_node_id);
        if (other) out.push({ edgeId: e.id, rel: edgeLabel(e.type), dir: "in", other });
      }
    }
    return out.sort((a, b) => a.rel.localeCompare(b.rel));
  }, [data.edges, node.id, nodeMap]);

  const props = Object.entries(node.properties ?? {}).filter(
    ([, v]) => v !== null && v !== "" && !(Array.isArray(v) && v.length === 0),
  );

  return (
    <div className="space-y-3 rounded-md border border-[#f3ead3]/15 bg-black/30 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className="inline-block h-3 w-3 shrink-0 rounded-full"
            style={{ background: colorFor(node.type) }}
          />
          <span className="text-[10px] uppercase tracking-[0.25em] text-[#f3ead3]/50">
            {typeLabel(node.type)}
          </span>
        </div>
        <button
          onClick={onClose}
          aria-label="Close detail"
          className="-mt-1 text-[#f3ead3]/40 hover:text-[#f3ead3]"
        >
          ✕
        </button>
      </div>

      <p className="break-words text-sm font-medium leading-snug">{node.name}</p>

      {/* Key properties as a clean list. */}
      {props.length > 0 && (
        <dl className="space-y-1 border-t border-[#f3ead3]/10 pt-2 text-xs">
          {props.map(([k, v]) => (
            <div key={k} className="flex gap-2">
              <dt className="w-20 shrink-0 text-[#f3ead3]/45">{humanizeKey(k)}</dt>
              <dd className="min-w-0 flex-1 break-words [overflow-wrap:anywhere] text-[#f3ead3]/85">
                {formatValue(v)}
              </dd>
            </div>
          ))}
          <button
            onClick={() => setShowRaw((s) => !s)}
            className="text-[10px] text-[#f3ead3]/40 underline-offset-2 hover:text-[#f3ead3]/70 hover:underline"
          >
            {showRaw ? "Hide raw JSON" : "Show raw JSON"}
          </button>
          {showRaw && (
            <pre className="max-h-40 overflow-auto rounded bg-black/40 p-2 text-[10px] leading-relaxed text-[#f3ead3]/70">
              {JSON.stringify(node.properties, null, 2)}
            </pre>
          )}
        </dl>
      )}

      {/* Connections — click to walk the graph. */}
      <div className="space-y-1.5 border-t border-[#f3ead3]/10 pt-2">
        <p className="text-[10px] uppercase tracking-[0.25em] text-[#f3ead3]/45">
          Connections · {rels.length}
        </p>
        {rels.length === 0 ? (
          <p className="text-xs text-[#f3ead3]/40">
            None in view — try “Expand neighbors”.
          </p>
        ) : (
          <ul className="max-h-56 space-y-0.5 overflow-y-auto">
            {rels.map((r) => (
              <li key={r.edgeId}>
                <button
                  onClick={() => onSelectNode(r.other)}
                  title={`${r.dir === "out" ? "→" : "←"} ${r.rel} · ${r.other.name}`}
                  className="group flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs hover:bg-[#f3ead3]/10"
                >
                  <span className="shrink-0 text-[#f3ead3]/35">{r.dir === "out" ? "→" : "←"}</span>
                  <span className="w-16 shrink-0 truncate text-[10px] italic text-[#f3ead3]/45">
                    {r.rel}
                  </span>
                  <span
                    className="inline-block h-2 w-2 shrink-0 rounded-full"
                    style={{ background: colorFor(r.other.type) }}
                  />
                  <span className="min-w-0 flex-1 truncate text-[#f3ead3]/85 group-hover:text-[#f3ead3]">
                    {r.other.name}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <button
        onClick={() => onExpand(node)}
        className="w-full rounded border border-[#f3ead3]/20 px-2 py-1.5 text-xs text-[#f3ead3]/80 hover:border-[#f3ead3]/50 hover:text-[#f3ead3]"
      >
        + Expand neighbors
      </button>
    </div>
  );
}
