#!/usr/bin/env python3
"""Test script: make the bot join a Google Meet, and watch it connect.

Two modes:

  • Bare join (default) — sends a Recall bot into the meeting with NO avatar.
    Needs only RECALL_API_KEY. This is the fastest "does it actually join?"
    smoke test (the design doc's P0 milestone) — no worker, no hosted viewer.

  • Avatar join (--avatar) — the full path: streams the Anam avatar as the
    bot's camera. Requires the worker running (`avatar-agent start`) AND a
    publicly-hosted VIEWER_URL.

  • Avatar join with an auto-tunnel (--avatar --tunnel) — for local dev: serves
    viewer/ and opens a Cloudflare quick tunnel (*.trycloudflare.com), then uses
    that public URL as VIEWER_URL for this run. No manual hosting, and unlike
    ngrok's free tier there's no browser-warning interstitial to break the page.
    Still needs the worker running (`avatar-agent start`). The tunnel + static
    server are torn down when the script exits. Requires `cloudflared` on PATH.

Usage:
    python scripts/join_meeting.py https://meet.google.com/abc-defg-hij
    python scripts/join_meeting.py https://meet.google.com/abc-defg-hij --avatar
    python scripts/join_meeting.py https://meet.google.com/abc-defg-hij --avatar --tunnel
    python scripts/join_meeting.py --status <bot_id>
    python scripts/join_meeting.py --leave  <bot_id>
    python scripts/join_meeting.py https://meet.google.com/abc-defg-hij --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

# Run from a checkout without needing an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from avatar_agent.config import settings  # noqa: E402
from avatar_agent.dispatch import _room_for, dispatch_bot  # noqa: E402
from avatar_agent.recall import RecallClient  # noqa: E402

# Recall status codes that mean "stop watching" — in the call, or finished.
TERMINAL = {"in_call_recording", "in_call_not_recording", "call_ended", "done", "fatal"}

VIEWER_DIR = Path(__file__).resolve().parent.parent / "viewer"
TRYCLOUDFLARE_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _await_line_matching(proc: subprocess.Popen, pattern: re.Pattern, timeout: float) -> str | None:
    """Read proc's merged stdout in a thread until a line matches `pattern`.

    A thread + queue keeps the timeout honest even if cloudflared stalls before
    printing anything (a bare readline() would block forever).
    """
    q: queue.Queue[str | None] = queue.Queue()

    def _pump() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            q.put(line)
        q.put(None)  # EOF sentinel

    threading.Thread(target=_pump, daemon=True).start()
    while True:
        try:
            line = q.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:  # process exited without a match
            return None
        m = pattern.search(line)
        if m:
            return m.group(0)


@contextmanager
def viewer_tunnel(port: int):
    """Serve viewer/ locally and expose it via a Cloudflare quick tunnel.

    Yields the public https URL. Both the static server and the tunnel are
    started as child processes and terminated on exit.
    """
    if shutil.which("cloudflared") is None:
        raise RuntimeError(
            "cloudflared not found on PATH — install it (`brew install cloudflared`) "
            "or host the viewer yourself and set VIEWER_URL."
        )
    if not (VIEWER_DIR / "index.html").exists():
        raise RuntimeError(f"viewer page not found at {VIEWER_DIR / 'index.html'}")

    procs: list[subprocess.Popen] = []
    try:
        print(f"serving viewer/ on http://localhost:{port} …")
        procs.append(
            subprocess.Popen(
                [sys.executable, "-m", "http.server", str(port), "--directory", str(VIEWER_DIR)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )

        print("opening Cloudflare quick tunnel …")
        tunnel = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        procs.append(tunnel)

        url = _await_line_matching(tunnel, TRYCLOUDFLARE_RE, timeout=30.0)
        if not url:
            raise RuntimeError("timed out waiting for a trycloudflare.com URL from cloudflared")
        print(f"tunnel   : {url}")
        yield url
    finally:
        for p in reversed(procs):
            p.terminate()
        for p in reversed(procs):
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


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


async def _dispatch_and_watch(args: argparse.Namespace) -> int:
    """Preflight, create the bot, and (optionally) watch it join.

    Reads settings() fresh so an auto-tunnel's VIEWER_URL override is picked up.
    """
    cfg = settings()
    recall = RecallClient(settings=cfg)

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


async def _run(args: argparse.Namespace) -> int:
    recall = RecallClient(settings=settings())

    if args.status:
        print(_latest_status(await recall.get_bot(args.status)))
        return 0

    if args.leave:
        await recall.leave_bot(args.leave)
        print(f"👋 asked bot {args.leave} to leave the call")
        return 0

    if args.tunnel:
        args.avatar = True  # the tunnel only makes sense for the avatar path

    # With --tunnel, stand up viewer/ behind a Cloudflare quick tunnel and use
    # its public URL as VIEWER_URL for this run. A dry run validates without
    # opening the tunnel (it creates nothing by definition).
    if args.tunnel and not args.dry_run:
        with viewer_tunnel(args.viewer_port) as url:
            os.environ["VIEWER_URL"] = url
            settings.cache_clear()  # re-read so dispatch sees the tunnel URL
            return await _dispatch_and_watch(args)

    return await _dispatch_and_watch(args)


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Send the avatar bot into a Google Meet (test).")
    p.add_argument("meeting_url", nargs="?", help="Google Meet link")
    p.add_argument("--avatar", action="store_true", help="full avatar path (needs worker + VIEWER_URL)")
    p.add_argument("--tunnel", action="store_true", help="auto-serve viewer/ via a Cloudflare quick tunnel (implies --avatar)")
    p.add_argument("--viewer-port", type=int, default=8080, help="local port to serve viewer/ on for --tunnel (default 8080)")
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
