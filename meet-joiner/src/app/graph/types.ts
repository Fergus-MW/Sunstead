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
