"""Grounding verifier — the product guard (docs/DESIGN.md §7).

Before an answer is emitted (and, later, spoken in a live meeting), check that its factual
assertions are actually supported by the evidence the agent retrieved. The git/KG agent
promises "never invent people, files, decisions"; this enforces it.

Design:
  • **Annotate-only / fail-open.** We return a Verdict (grounded + confidence); we never block.
    On any verifier error we return a neutral verdict so a flaky check never fails a good task.
  • **Cheap.** Runs on the fast model with a constrained JSON schema, one short round-trip. The
    harness only calls it when an agent supplied evidence (see `harness.run_task`), so it adds
    zero cost to tasks with nothing to check.
"""

from __future__ import annotations

import json

from .contracts import Verdict

_VERIFY_SYSTEM = """You are a strict grounding checker. You are given a CLAIM (an agent's answer) \
and EVIDENCE (the raw tool results the agent retrieved to produce it).

Decide whether every *specific factual assertion* in the CLAIM — names of people, files, modules, \
commits, meetings, decisions, dates, counts, quotes — is supported by the EVIDENCE.

Rules:
- grounded = false if the CLAIM states any specific fact that does not appear in the EVIDENCE.
- Generic framing, hedging, and restating the question are fine; invented specifics are not.
- "I couldn't find anything" / "no results" is grounded when the EVIDENCE is indeed empty.
- confidence is your certainty in the verdict, 0.0–1.0.
- note: one short phrase naming the unsupported claim, or why it's grounded. Keep it under 12 words.

Return ONLY the JSON object."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "grounded": {"type": "boolean"},
        "confidence": {"type": "number"},
        "note": {"type": "string"},
    },
    "required": ["grounded", "confidence", "note"],
    "additionalProperties": False,
}

_MAX_EVIDENCE_ITEMS = 12
_MAX_CLAIM_CHARS = 4000


async def verify_grounding(client, *, model: str, claim: str, evidence: list[str]) -> Verdict:
    """Check `claim` against `evidence`; return a Verdict. Never raises."""
    claim = (claim or "").strip()
    items = [e for e in (evidence or []) if e and e.strip()]
    if not claim:
        return Verdict(grounded=True, confidence=0.0, evidence_count=len(items), note="empty answer")
    if not items:
        # No tool results to check against — nothing to ground, so don't claim it's verified.
        return Verdict(grounded=True, confidence=0.0, evidence_count=0, note="no evidence captured")

    ev = "\n\n---\n\n".join(items[:_MAX_EVIDENCE_ITEMS])
    user = f"CLAIM:\n{claim[:_MAX_CLAIM_CHARS]}\n\nEVIDENCE (tool results the agent retrieved):\n{ev}"
    try:
        msg = await client.messages.create(
            model=model,
            max_tokens=512,
            system=_VERIFY_SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
        text = next((b.text for b in msg.content if b.type == "text"), "{}")
        data = json.loads(text)
        conf = float(data.get("confidence", 0.0))
        return Verdict(
            grounded=bool(data.get("grounded", True)),
            confidence=max(0.0, min(1.0, conf)),
            evidence_count=len(items),
            note=(data.get("note") or None),
        )
    except Exception as e:  # fail open — a broken verifier must never fail a real answer
        return Verdict(grounded=True, confidence=0.0, evidence_count=len(items),
                       note=f"verifier unavailable: {type(e).__name__}")
