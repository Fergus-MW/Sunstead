#!/usr/bin/env python3
"""Test script: make the bot join a Google Meet, and watch it connect.

Two modes:

  • Bare join (default) — sends a Recall bot into the meeting with NO avatar.
    Needs only RECALL_API_KEY. This is the fastest "does it actually join?"
    smoke test (the design doc's P0 milestone) — no worker, no hosted viewer.

  • Avatar join (--avatar) — the full path: streams the Anam avatar as the
    bot's camera. Requires the worker running (`avatar-agent start`) AND a
    publicly-hosted VIEWER_URL.

Usage:
    python scripts/join_meeting.py https://meet.google.com/abc-defg-hij
    python scripts/join_meeting.py https://meet.google.com/abc-defg-hij --avatar
    python scripts/join_meeting.py --status <bot_id>
    python scripts/join_meeting.py --leave  <bot_id>
    python scripts/join_meeting.py https://meet.google.com/abc-defg-hij --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Run from a checkout without needing an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from avatar_agent.config import settings  # noqa: E402
from avatar_agent.dispatch import _room_for, dispatch_bot  # noqa: E402
from avatar_agent.recall import RecallClient  # noqa: E402

# Recall status codes that mean "stop watching" — in the call, or finished.
TERMINAL = {"in_call_recording", "in_call_not_recording", "call_ended", "done", "fatal"}


def _latest_status(bot: dict) -> str:
    changes = bot.get("status_changes") or []
    if changes and isinstance(changes[-1], dict):
        code = changes[-1].get("code", "?")
        msg = changes[-1].get("message") or changes[-1].get("sub_code")
        return f"{code}{f' ({msg})' if msg else ''}"
    return bot.get("status", "unknown")


def _preflight(meeting_url: str, avatar: bool) -> list[str]:
    """Return a list of blocking problems (empty = good to go)."""
    cfg = settings()
    problems: list[str] = []

    try:
        _room_for(meeting_url)
    except ValueError as exc:
        problems.append(str(exc))

    if not cfg.recall_api_key:
        problems.append("RECALL_API_KEY is not set")

    if avatar:
        if not (cfg.livekit_url and cfg.livekit_api_key and cfg.livekit_api_secret):
            problems.append("LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET must be set")
        if not cfg.viewer_url or "example.com" in cfg.viewer_url:
            problems.append("VIEWER_URL must point at a real, publicly-hosted viewer page")
    return problems


async def _watch(recall: RecallClient, bot_id: str, timeout: float) -> None:
    print(f"\nwatching bot {bot_id} (Ctrl-C to stop)…")
    waited = 0.0
    last = None
    while waited < timeout:
        bot = await recall.get_bot(bot_id)
        status = _latest_status(bot)
        if status != last:
            print(f"  [{waited:5.0f}s] {status}")
            last = status
        code = status.split()[0]
        if code in TERMINAL:
            if code.startswith("in_call"):
                print("\n✅ the bot is in the meeting.")
            return
        await asyncio.sleep(3)
        waited += 3
    print("\n⏱  stopped watching (timeout) — check the Recall dashboard for live status.")


async def _run(args: argparse.Namespace) -> int:
    cfg = settings()
    recall = RecallClient(settings=cfg)

    if args.status:
        print(_latest_status(await recall.get_bot(args.status)))
        return 0

    if args.leave:
        await recall.leave_bot(args.leave)
        print(f"👋 asked bot {args.leave} to leave the call")
        return 0

    problems = _preflight(args.meeting_url, args.avatar)
    if problems:
        print("✋ can't dispatch — fix these first:", file=sys.stderr)
        for p in problems:
            print(f"   • {p}", file=sys.stderr)
        return 2

    mode = "avatar (Anam via output_media)" if args.avatar else "bare join (no avatar)"
    print(f"mode    : {mode}")
    print(f"meeting : {args.meeting_url}")

    if args.dry_run:
        if args.avatar:
            room = _room_for(args.meeting_url)
            print(f"room    : {room}")
            print(f"viewer  : {cfg.viewer_url} (token appended at dispatch)")
        print("\n(dry run — no Recall bot created)")
        return 0

    if args.avatar:
        if not args.assume_worker:
            print("⚠  make sure the worker is running:  avatar-agent start")
        result = await dispatch_bot(args.meeting_url)  # creates the bot with output_media
        bot_id = result["bot_id"]
        print(f"room    : {result['room']}")
    else:
        bot = await recall.create_bot(meeting_url=args.meeting_url)
        bot_id = bot.get("id")

    print(f"✅ dispatched — bot id: {bot_id}")
    print(f"   leave with:  python scripts/join_meeting.py --leave {bot_id}")

    if not args.no_watch:
        await _watch(recall, bot_id, args.timeout)
    return 0


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Send the avatar bot into a Google Meet (test).")
    p.add_argument("meeting_url", nargs="?", help="Google Meet link")
    p.add_argument("--avatar", action="store_true", help="full avatar path (needs worker + VIEWER_URL)")
    p.add_argument("--leave", metavar="BOT_ID", help="make a running bot leave")
    p.add_argument("--status", metavar="BOT_ID", help="print a bot's current status")
    p.add_argument("--dry-run", action="store_true", help="preflight only; create nothing")
    p.add_argument("--no-watch", action="store_true", help="don't poll status after dispatch")
    p.add_argument("--assume-worker", action="store_true", help="skip the 'is the worker up?' reminder")
    p.add_argument("--timeout", type=float, default=90.0, help="seconds to watch status (default 90)")
    args = p.parse_args()

    if not (args.meeting_url or args.leave or args.status):
        p.print_help()
        return 1

    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nstopped.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
