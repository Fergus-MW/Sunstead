import { forwardParams, relayJson } from "../../_proxy";

// Server-side proxy to central-kg-api's GET /subgraph.
// Keeps the KG base URL server-side and sidesteps CORS / mixed-content.
const KG_API_URL = process.env.KG_API_URL ?? "http://localhost:8000";

export async function GET(req: Request) {
  const incoming = new URL(req.url);
  const target = new URL("/subgraph", KG_API_URL);

  // Forward only the params the KG endpoint understands.
  forwardParams(incoming, target, ["q", "hops", "node_limit"]);
  // `center` may repeat (one or more node ids).
  for (const c of incoming.searchParams.getAll("center")) {
    target.searchParams.append("center", c);
  }

  return relayJson(target, { headers: { accept: "application/json" } }, "Could not reach central-kg-api");
}
