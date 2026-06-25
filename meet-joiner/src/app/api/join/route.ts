import { NextResponse } from "next/server";

const MEET_PATTERN = /^https:\/\/meet\.google\.com\/([a-z]{3}-[a-z]{4}-[a-z]{3})(\?.*)?$/i;

// The avatar-agent dispatch endpoint (the Lambda Function URL, or the single-EC2
// dispatch route). POSTs a Meet link → Recall sends the avatar bot in. Kept
// server-side so no Recall/LiveKit creds ever touch the browser. Unset = the UI
// still works (acknowledges the join) but no real bot is sent — useful for dev
// and for demoing the dashboard without the realtime layer online.
const DISPATCH_URL = process.env.AVATAR_DISPATCH_URL ?? "";

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

  // The canonical meeting_id is the Meet code — the same key the avatar tags its
  // delegated tasks with, so the dashboard can subscribe (WS /stream?meeting_id=).
  const meetingId = match[1].toLowerCase();

  if (!DISPATCH_URL) {
    return NextResponse.json({ ok: true, meetingId, dispatched: false });
  }

  try {
    const res = await fetch(DISPATCH_URL, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ meeting_url: link }),
    });
    const data = (await res.json().catch(() => ({}))) as {
      ok?: boolean;
      error?: string;
      meeting_id?: string;
      bot_id?: string;
    };
    if (!res.ok || data.ok === false) {
      return NextResponse.json(
        { error: data.error ?? "Avatar dispatch failed", meetingId },
        { status: 502 },
      );
    }
    // Prefer the dispatcher's meeting_id (authoritative); they should match.
    return NextResponse.json({
      ok: true,
      meetingId: data.meeting_id ?? meetingId,
      dispatched: true,
      botId: data.bot_id,
    });
  } catch (err) {
    return NextResponse.json(
      {
        error: "Could not reach the avatar dispatcher",
        detail: err instanceof Error ? err.message : String(err),
        meetingId,
      },
      { status: 502 },
    );
  }
}
