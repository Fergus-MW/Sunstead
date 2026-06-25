import { gatewayPost } from "../_proxy";

// Server-side proxy to the agent-system gateway's POST /tasks (the "ask box").
// Browser → here → gateway → Kafka agent.tasks.*. Keeps the gateway URL
// server-side and avoids CORS (the gateway has no CORS middleware).
export async function POST(req: Request) {
  return gatewayPost(req, "/tasks");
}
