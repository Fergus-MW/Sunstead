"""AWS Lambda handler (Function URL) that dispatches / removes the avatar bot.

Lets the meet-joiner front-end POST a Meet link to send the avatar into a call
without holding any Recall/LiveKit credentials in the browser. Packaged as a
container image (deploy/Dockerfile.lambda); config comes from env + SSM secrets
injected by Terraform.

Request (POST, JSON body):
    { "meeting_url": "https://meet.google.com/abc-defg-hij" }   → join
    { "leave": "<bot_id>" }                                       → leave

Response:
    200 { "ok": true, "bot_id": "...", "room": "...", "viewer_url": "..." }
    400 { "ok": false, "error": "..." }    (bad link / bad request)
    502 { "ok": false, "error": "..." }    (Recall/LiveKit call failed)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("avatar-agent.lambda")
logging.getLogger().setLevel(logging.INFO)


def _hydrate_secrets_from_ssm() -> None:
    """Resolve SSM SecureStrings into env vars before config is read.

    Terraform passes `<NAME>_SSM=<param-name>` env vars (e.g. RECALL_API_KEY_SSM);
    at cold start we fetch each parameter (WithDecryption) and set the canonical
    env var (RECALL_API_KEY) that pydantic-settings reads. boto3 ships in the
    Lambda base image. No-ops locally where no `_SSM` vars are set.
    """
    pending = {k[:-4]: v for k, v in os.environ.items() if k.endswith("_SSM") and v}
    if not pending:
        return
    try:
        import boto3

        ssm = boto3.client("ssm")
        for canonical, param_name in pending.items():
            if os.environ.get(canonical):
                continue
            resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
            os.environ[canonical] = resp["Parameter"]["Value"]
    except Exception:  # surface a clear error rather than a cryptic auth failure
        logger.exception("failed to hydrate secrets from SSM")
        raise


# Runs once per cold start, before dispatch imports trigger settings().
_hydrate_secrets_from_ssm()

from .dispatch import dispatch_bot, leave_bot  # noqa: E402  (after secret hydration)


def _resp(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    body = _parse_body(event)

    if bot_id := body.get("leave"):
        try:
            asyncio.run(leave_bot(str(bot_id)))
        except httpx.HTTPError as exc:
            logger.exception("leave failed")
            return _resp(502, {"ok": False, "error": f"recall error: {exc}"})
        return _resp(200, {"ok": True, "left": bot_id})

    meeting_url = body.get("meeting_url")
    if not isinstance(meeting_url, str) or not meeting_url:
        return _resp(400, {"ok": False, "error": "meeting_url is required"})

    try:
        result = asyncio.run(dispatch_bot(meeting_url))
    except ValueError as exc:  # invalid Meet link
        return _resp(400, {"ok": False, "error": str(exc)})
    except httpx.HTTPError as exc:  # Recall call failed
        logger.exception("dispatch failed")
        return _resp(502, {"ok": False, "error": f"recall error: {exc}"})

    return _resp(200, {"ok": True, **result})
