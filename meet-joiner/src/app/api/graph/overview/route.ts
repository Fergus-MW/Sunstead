import { forwardParams, relayJson } from "../../_proxy";

// Server-side proxy to central-kg-api's GET /overview.
// Powers the no-query landing view (busiest hubs + neighborhoods).
const KG_API_URL = process.env.KG_API_URL ?? "http://localhost:8000";

export async function GET(req: Request) {
  const target = new URL("/overview", KG_API_URL);
  forwardParams(new URL(req.url), target, ["seeds", "hops", "node_limit"]);
  return relayJson(target, { headers: { accept: "application/json" } }, "Could not reach central-kg-api");
}
