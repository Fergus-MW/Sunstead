import { gatewayPost } from "../_proxy";

// Server-side proxy to the agent-system gateway's POST /transcript.
// Browser -> here -> gateway -> Kafka meeting.transcript -> planner.
export async function POST(req: Request) {
  return gatewayPost(req, "/transcript");
}
