import { forwardParams, relayJson } from "../../../_proxy";

// Server-side proxy to central-kg-api's GET /entity/{node_id}.
// Returns { node, subgraph } — the clicked node plus its local neighborhood.
const KG_API_URL = process.env.KG_API_URL ?? "http://localhost:8000";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const target = new URL(`/entity/${encodeURIComponent(id)}`, KG_API_URL);
  forwardParams(new URL(req.url), target, ["hops", "limit"]);
  return relayJson(target, { headers: { accept: "application/json" } }, "Could not reach central-kg-api");
}
