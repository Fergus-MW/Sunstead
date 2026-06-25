"""Message contracts for the Sunstead agent subsystem.

The single source of truth for what flows over Kafka. Every component imports these
models instead of hand-rolling dicts. Changing a payload here is a cross-team event
(see docs/AGENT_SYSTEM.md §6) — PR it and note the doc.

    Envelope[TaskCreatePayload](type="task.create", meeting_id=..., ts=..., payload=...)
"""

from __future__ import annotations

from typing import Annotated, Generic, Literal, TypeVar, Union
from uuid import uuid4

from pydantic import BaseModel, Field

SCHEMA = "sunstead.v1"

# --- payloads -------------------------------------------------------------

class Speaker(BaseModel):
    id: int | str | None = None
    name: str | None = None


class TranscriptPayload(BaseModel):
    speaker: Speaker = Field(default_factory=Speaker)
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    is_final: bool = False
    confidence: float | None = None
    language: str | None = None


MeetingEventKind = Literal[
    "bot.joined", "bot.left", "participant.joined", "participant.left",
    "participant.muted", "participant.unmuted",
]


class MeetingEventPayload(BaseModel):
    kind: MeetingEventKind
    participant: Speaker | None = None


# intents are the controlled vocab per agent (docs/AGENT_SYSTEM.md §9)
TaskIntent = Literal[
    "echo",                                                  # dev / smoke (no creds)
    "build_website", "update_website",                       # web-agent
    "analyze", "summarize_metrics", "query_data",            # data-agent
    "read_git", "blame", "who_changed", "recent_changes",    # git-agent (code questions)
    "ask",                                                   # general KG question (any node/edge type)
    "recap", "action_items", "decisions",                    # meeting-ops (transcript → outcomes → KG)
    "research",                                               # research agent (live web search)
]


class TaskCreatePayload(BaseModel):
    task_id: str
    intent: TaskIntent
    args: dict = Field(default_factory=dict)
    context_refs: list[str] = Field(default_factory=list)  # pointers into KG, not blobs
    # harness fields (docs/AGENT_SYSTEM.md §4)
    idempotency_key: str | None = None                     # dedupe redelivery; defaults to task_id
    workspace_id: str | None = None                        # web-agent persistent workspace
    parent_task_id: str | None = None                      # agent→agent subtask graph
    depth: int = 0                                          # depth-limited (e.g. <= 2)
    requested_by: str = "listener"
    reply_to: str = "agent.results"


class Artifact(BaseModel):
    kind: Literal["url", "image", "text", "json"]
    value: str


class Verdict(BaseModel):
    """The verifier's grounding judgement on an answer (docs/DESIGN.md §7).

    Annotate-only / fail-open: we surface confidence, we don't block. `grounded=False`
    means a factual assertion in the answer wasn't supported by the evidence the agent
    actually retrieved; `evidence_count` is how many tool results were checked against.
    """
    grounded: bool
    confidence: float                                      # 0..1, the verifier's certainty
    evidence_count: int = 0
    note: str | None = None


class TaskResultPayload(BaseModel):
    task_id: str
    status: Literal["completed", "failed"]
    result: dict | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    error: str | None = None
    verdict: Verdict | None = None                         # grounding check, when evidence was present


class ActivityPayload(BaseModel):
    """Visible status for the FE feed — NOT chain-of-thought (docs/AGENT_SYSTEM.md §4)."""
    task_id: str
    status: str                                            # "received" | "building site" | ...
    detail: str | None = None


class TracePayload(BaseModel):
    """A streamed reasoning/output delta for the FE (docs/AGENT_SYSTEM.md §3.5).

    Unlike ActivityPayload (coarse status), this carries the model's live tokens:
    `phase="thinking"` is summarized chain-of-thought (extended thinking), `phase="text"`
    is the answer as it generates. `seq` is monotonic per task so the FE can apply deltas
    in order and drop replays idempotently.
    """
    task_id: str
    seq: int
    phase: Literal["thinking", "text"]
    delta: str


class VerdictPayload(BaseModel):
    """An async grounding verdict for an already-emitted result (docs/DESIGN.md §7).

    The answer is emitted immediately; the verifier runs off the critical path and the
    verdict follows as this event — so the check never delays the answer (the §2 tempo
    rule: never put a slow verifier in front of the spoken/displayed result). The FE
    applies it to the task by `task_id`.
    """
    task_id: str
    verdict: Verdict


class ControlPayload(BaseModel):
    """Operator/conductor command targeting a running task (docs/DESIGN.md §7).

    The control channel turns oversight from observe-only into *act*: `cancel` stops a
    task mid-flight. Routed by `task_id`; the runner that owns that task honours it.
    """
    task_id: str
    action: Literal["cancel"]
    reason: str | None = None


class KgUpdatePayload(BaseModel):
    """Async 'write this fact to the graph' — consumed by central-kg-api."""
    node_key: str
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)


Payload = Union[
    TranscriptPayload, MeetingEventPayload, TaskCreatePayload,
    TaskResultPayload, ActivityPayload, TracePayload, VerdictPayload, ControlPayload, KgUpdatePayload,
]

# --- envelope -------------------------------------------------------------

MessageType = Literal[
    "transcript.partial", "transcript.final",
    "meeting.event",
    "task.create", "task.completed", "task.failed",
    "activity",
    "trace",
    "verdict",
    "control",
    "kg.update",
]

P = TypeVar("P", bound=BaseModel)


class Envelope(BaseModel, Generic[P]):
    schema_: Annotated[str, Field(alias="schema")] = SCHEMA
    id: str = Field(default_factory=lambda: str(uuid4()))
    type: MessageType
    meeting_id: str
    ts: str  # ISO-8601, producer clock — pass explicitly
    payload: P

    model_config = {"populate_by_name": True}

    def to_json(self) -> bytes:
        return self.model_dump_json(by_alias=True).encode()
