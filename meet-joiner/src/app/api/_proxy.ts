import { NextResponse } from "next/server";

// Shared upstream-proxy plumbing for the API routes. Each route keeps the KG/gateway
// base URL server-side (avoiding CORS / mixed-content) and forwards to it. The only
// truly duplicated part — fetch the upstream, relay its JSON body verbatim, and turn
// an unreachable upstream into a clean 502 instead of an opaque 500 — lives here.
// Each route still builds its own target URL and forwards its own params.

// Relay a request to `target` and stream its JSON response back unchanged.
export async function relayJson(
  target: URL,
  init: RequestInit,
  unreachable: string,
): Promise<NextResponse> {
  try {
    const res = await fetch(target, init);
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch (err) {
    return NextResponse.json(
      {
        error: unreachable,
        detail: err instanceof Error ? err.message : String(err),
        target: target.toString(),
      },
      { status: 502 },
    );
  }
}

// Copy the listed single-valued query params from `from` onto `to`, when present.
export function forwardParams(from: URL, to: URL, keys: string[]): void {
  for (const k of keys) {
    const v = from.searchParams.get(k);
    if (v) to.searchParams.set(k, v);
  }
}

const GATEWAY_URL = process.env.GATEWAY_URL ?? "http://localhost:8800";

// Relay a JSON POST to the agent-system gateway (the "ask box" / "stop" button paths):
// parse+re-encode the body (400 on bad JSON), forward to `path`, relay the response.
export async function gatewayPost(req: Request, path: string): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }
  return relayJson(
    new URL(path, GATEWAY_URL),
    {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
    },
    "Could not reach the agent gateway",
  );
}
