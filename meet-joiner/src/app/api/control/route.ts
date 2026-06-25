import { gatewayPost } from "../_proxy";

// Server-side proxy to the agent-system gateway's POST /control (the "stop" button).
// Browser → here → gateway → Kafka agent.control → the runner cancels the task.
// Keeps the gateway URL server-side and avoids CORS (the gateway has no CORS middleware).
export async function POST(req: Request) {
  return gatewayPost(req, "/control");
}
