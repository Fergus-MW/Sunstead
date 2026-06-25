import { NextResponse } from "next/server";

// Server-side proxy to central-kg-api's GET /overview.
// Powers the no-query landing view (busiest hubs + neighborhoods).
const KG_API_URL = process.env.KG_API_URL ?? "http://localhost:8000";

export async function GET(req: Request) {
  const incoming = new URL(req.url);
  const target = new URL("/overview", KG_API_URL);

  for (const key of ["seeds", "hops", "node_limit"]) {
    const v = incoming.searchParams.get(key);
    if (v) target.searchParams.set(key, v);
  }

  try {
    const res = await fetch(target, { headers: { accept: "application/json" } });
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch (err) {
    return NextResponse.json(
      {
        error: "Could not reach central-kg-api",
        detail: err instanceof Error ? err.message : String(err),
        target: target.toString(),
      },
      { status: 502 },
    );
  }
}
