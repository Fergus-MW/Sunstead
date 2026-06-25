import { NextResponse } from "next/server";

// Server-side proxy to the agent-system gateway's POST /control (the "stop" button).
// Browser → here → gateway → Kafka agent.control → the runner cancels the task.
// Keeps the gateway URL server-side and avoids CORS (the gateway has no CORS middleware).
const GATEWAY_URL = process.env.GATEWAY_URL ?? "http://localhost:8800";

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const target = new URL("/control", GATEWAY_URL);
  try {
    const res = await fetch(target, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch (err) {
    return NextResponse.json(
      {
        error: "Could not reach the agent gateway",
        detail: err instanceof Error ? err.message : String(err),
        target: target.toString(),
      },
      { status: 502 },
    );
  }
}
