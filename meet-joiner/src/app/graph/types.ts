// Shapes mirror central-kg-api's pydantic models (app/models.py).

export type GraphNode = {
  id: string;
  type: string;
  name: string;
  properties: Record<string, unknown>;
  source_id?: string | null;
  score?: number | null;
};

export type GraphEdge = {
  id: string;
  source_node_id: string;
  target_node_id: string;
  type: string;
  weight?: number;
  properties: Record<string, unknown>;
};

export type Subgraph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

// Node-type vocab from app/models.py → a palette tuned to the Sunstead theme.
export const NODE_COLORS: Record<string, string> = {
  person: "#ffd57a",
  company: "#f78f3f",
  meeting: "#7ec8e3",
  task: "#9ad29a",
  workflow: "#c4a3e0",
  requirement: "#e08aa3",
  feature: "#6fcf97",
  user_story: "#a3c4e0",
  code_module: "#e3b04b",
  product: "#f3ead3",
  source_document: "#8a99a8",
  topic: "#d98ae0",
  decision: "#e06f6f",
};

export const FALLBACK_COLOR = "#bcae8a";

export function colorFor(type: string): string {
  return NODE_COLORS[type] ?? FALLBACK_COLOR;
}

// Human-readable label for a node type (e.g. "user_story" → "User story").
export function typeLabel(type: string): string {
  return type.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

// Relationship phrasing for edges, read source → target.
export const EDGE_LABELS: Record<string, string> = {
  depends_on: "depends on",
  discussed_in: "discussed in",
  implements: "implements",
  relates_to: "relates to",
  derived_from: "derived from",
  assigned_to: "assigned to",
  blocks: "blocks",
  mentions: "mentions",
  part_of: "part of",
  owns: "owns",
};

export function edgeLabel(type: string): string {
  return EDGE_LABELS[type] ?? type.replace(/_/g, " ");
}

// A compact label for crowded canvases: keep the last 1–2 path segments,
// strip pytest node-ids, and cap length. Full name lives in the detail panel.
export function shortLabel(name: string): string {
  let s = name.split("::")[0]; // drop pytest "::test_x" suffixes
  if (s.includes("/")) {
    const parts = s.split("/").filter(Boolean);
    s = parts.slice(-2).join("/");
  }
  return s.length > 22 ? s.slice(0, 21) + "…" : s;
}

// Turn a property key into a readable label ("author_email" → "Author email").
export function humanizeKey(key: string): string {
  return key.replace(/[_-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

// Render a property value compactly for the detail list.
export function formatValue(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

// ── Display / view-mode settings ────────────────────────────────
export type ColorMode = "type" | "degree" | "minimal";

export type Display = {
  colorMode: ColorMode; // how nodes are colored
  labels: "hubs" | "all" | "off"; // which node labels to draw
  edgeLabels: "focus" | "all" | "off"; // when to draw relationship labels
  hideIsolated: boolean; // drop nodes with no edges
  spacing: number; // layout spread multiplier (0.6–1.8)
};

export const DEFAULT_DISPLAY: Display = {
  colorMode: "type",
  labels: "hubs",
  edgeLabels: "focus",
  hideIsolated: false,
  spacing: 1,
};

export const MINIMAL_NODE_COLOR = "#cdbf99";

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

// Cool (few links) → warm (hub) ramp for the connectivity color mode.
const DEG_LOW = hexToRgb("#4a6f74");
const DEG_HIGH = hexToRgb("#f78f3f");

export function degreeColor(deg: number, maxDeg: number): string {
  const t = Math.sqrt(Math.min(1, deg / Math.max(1, maxDeg)));
  const r = Math.round(DEG_LOW[0] + (DEG_HIGH[0] - DEG_LOW[0]) * t);
  const g = Math.round(DEG_LOW[1] + (DEG_HIGH[1] - DEG_LOW[1]) * t);
  const b = Math.round(DEG_LOW[2] + (DEG_HIGH[2] - DEG_LOW[2]) * t);
  return `rgb(${r},${g},${b})`;
}
