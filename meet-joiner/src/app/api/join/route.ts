import { NextResponse } from "next/server";

const MEET_PATTERN = /^https:\/\/meet\.google\.com\/([a-z]{3}-[a-z]{4}-[a-z]{3})(\?.*)?$/i;

export async function POST(req: Request) {
  let body: { link?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const link = typeof body.link === "string" ? body.link.trim() : "";
  const match = link.match(MEET_PATTERN);
  if (!match) {
    return NextResponse.json(
      { error: "Not a valid Google Meet link" },
      { status: 400 },
    );
  }

  const meetingId = match[1];

  // TODO: dispatch the actual joining agent (Recall.ai, headless browser, etc.)
  // For now we just acknowledge the request so the UI can be wired end-to-end.

  return NextResponse.json({ ok: true, meetingId });
}
