import { NextResponse } from "next/server";

// Server-side proxy to central-kg-api's GET /entity/{node_id}.
// Returns { node, subgraph } — the clicked node plus its local neighborhood.
const KG_API_URL = process.env.KG_API_URL ?? "http://localhost:8000";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const incoming = new URL(req.url);
  const target = new URL(`/entity/${encodeURIComponent(id)}`, KG_API_URL);

  for (const key of ["hops", "limit"]) {
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
