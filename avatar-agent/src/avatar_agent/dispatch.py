"""`dispatch-bot` — send the avatar into a Google Meet with just the link (JP-1).

Flow:
  1. Derive a LiveKit room name from the meeting id.
  2. Mint a scoped LiveKit token for the Recall viewer page (it must publish the
     meeting audio into the room and subscribe to the avatar's audio/video).
  3. Build the viewer URL (viewer/index.html) with the room URL + token.
  4. Ask Recall to join the meeting and stream that page as the bot's camera.

When Recall's browser loads the viewer and joins the room, LiveKit dispatches a
job to the running worker (agent.py) automatically — one session per meeting.

Usage:
    dispatch-bot https://meet.google.com/abc-defg-hij
    dispatch-bot --leave <bot_id>
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from urllib.parse import quote, urlencode

from dotenv import load_dotenv
from livekit import api

from .config import settings
from .recall import RecallClient

# Same shape the meet-joiner front-end validates.
MEET_PATTERN = re.compile(
    r"^https://meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})(\?.*)?$", re.IGNORECASE
)

# The canonical meeting key is the Meet code (e.g. "abc-defg-hij"). The LiveKit
# room is that key with this prefix; `meeting_id_for_room` reverses it. Keeping the
# mapping here (one place) means the worker (agent.py), the gateway, and the FE WS
# filter all agree on `meeting_id`, so a delegated result correlates to the call.
ROOM_PREFIX = "avatar-"


def meeting_id_for(meeting_url: str) -> str:
    """The canonical meeting_id (the Meet code) for a meeting URL."""
    match = MEET_PATTERN.match(meeting_url.strip())
    if not match:
        raise ValueError("not a valid Google Meet link (expected meet.google.com/xxx-xxxx-xxx)")
    return match.group(1).lower()


def meeting_id_for_room(room: str) -> str:
    """Reverse of `_room_for`: the meeting_id a LiveKit room name encodes."""
    return room[len(ROOM_PREFIX):] if room.startswith(ROOM_PREFIX) else room


def _room_for(meeting_url: str) -> str:
    return f"{ROOM_PREFIX}{meeting_id_for(meeting_url)}"


def _viewer_token(room: str) -> str:
    cfg = settings()
    return (
        api.AccessToken(cfg.livekit_api_key, cfg.livekit_api_secret)
        .with_identity("recall-viewer")
        .with_name("Recall Viewer")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,  # publishes the meeting audio into the room
                can_subscribe=True,  # renders the avatar's audio + video
            )
        )
        .to_jwt()
    )


def _viewer_url(room: str) -> str:
    cfg = settings()
    qs = urlencode({"lk": cfg.livekit_url, "token": _viewer_token(room)}, quote_via=quote)
    sep = "&" if "?" in cfg.viewer_url else "?"
    return f"{cfg.viewer_url}{sep}{qs}"


async def dispatch_bot(meeting_url: str) -> dict:
    """Send the avatar into `meeting_url`. Returns {bot_id, room, meeting_id, viewer_url}.

    `meeting_id` is the canonical key the FE subscribes to (`WS /stream?meeting_id=`)
    so the avatar's delegated results land against the right call.

    Reused by both the `dispatch-bot` CLI and the AWS Lambda Function URL handler
    (lambda_dispatch.py), so the join logic lives in exactly one place.
    """
    cfg = settings()
    meeting_id = meeting_id_for(meeting_url)  # raises ValueError on a bad link
    room = _room_for(meeting_url)
    viewer_url = _viewer_url(room)
    bot = await RecallClient(settings=cfg).create_bot(
        meeting_url=meeting_url, viewer_url=viewer_url
    )
    return {
        "bot_id": bot.get("id"),
        "room": room,
        "meeting_id": meeting_id,
        "viewer_url": viewer_url,
    }


async def leave_bot(bot_id: str) -> None:
    """Pull a running bot out of its call (JP-3)."""
    await RecallClient(settings=settings()).leave_bot(bot_id)


async def _join(meeting_url: str) -> None:
    result = await dispatch_bot(meeting_url)
    print(f"✅ bot dispatched to {meeting_url}")
    print(f"   bot id     : {result['bot_id']}")
    print(f"   room       : {result['room']}")
    print(f"   meeting_id : {result['meeting_id']}")
    print(f"   viewer     : {result['viewer_url'][:80]}…")


async def _leave(bot_id: str) -> None:
    await leave_bot(bot_id)
    print(f"👋 bot {bot_id} asked to leave the call")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Send the Sunstead avatar into a Google Meet.")
    parser.add_argument("meeting_url", nargs="?", help="Google Meet link")
    parser.add_argument("--leave", metavar="BOT_ID", help="make a running bot leave")
    args = parser.parse_args()

    try:
        if args.leave:
            asyncio.run(_leave(args.leave))
        elif args.meeting_url:
            asyncio.run(_join(args.meeting_url))
        else:
            parser.print_help()
            sys.exit(1)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
