from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Canonical vocabulary. The single source of truth is the agent suite's
# `agent-system/shared/src/shared/schema.py` (entity/episode split + helpers); this Literal
# MIRRORS it so the HTTP extractor and FastAPI surface validate against the same set the
# workers write. Keep in sync — change = PR + a LOG entry (DESIGN §6).
NodeType = Literal[
    # entities (stable, name-identified — merge-on-conflict by (type, lower(name)))
    "person",
    "company",
    "product",
    "feature",
    "requirement",
    "user_story",
    "workflow",
    "task",
    "topic",
    "code_module",
    "commit",
    "meeting",
    "website",
    "policy",
    "source_document",
    # episodes (occurrence-identified — keyed {scope}::{slug}, never name-merged)
    "utterance",
    "action_item",
    "decision",
    "research_finding",
]

EdgeType = Literal[
    # structural / code
    "depends_on",
    "part_of",
    "derived_from",
    "relates_to",
    "implements",
    # work / ownership
    "owns",
    "assigned_to",
    "blocks",
    "authored",
    "touches",
    # meeting / knowledge
    "in_meeting",
    "said",
    "attended",
    "mentions",
    "discussed_in",
    "rationale_for",
]


class Source(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    uri: str | None = None
    title: str | None = None
    content: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class SourceCreate(BaseModel):
    kind: str
    uri: str | None = None
    title: str | None = None
    content: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    extract: bool = True


class Node(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    type: str
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)
    source_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    score: float | None = None  # populated by search endpoints


class NodeUpsert(BaseModel):
    type: str
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)
    source_id: UUID | None = None
    embed: bool = True  # compute & store embedding


class Edge(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_node_id: UUID
    target_node_id: UUID
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    weight: float = 1.0
    source_id: UUID | None = None
    created_at: datetime


class EdgeUpsert(BaseModel):
    source_node_id: UUID | None = None
    target_node_id: UUID | None = None
    # Or refer by (type,name); resolved to ids server-side
    source_ref: tuple[str, str] | None = None
    target_ref: tuple[str, str] | None = None
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    weight: float = 1.0
    source_id: UUID | None = None


class Event(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    node_id: UUID | None = None
    source_id: UUID | None = None
    kind: str
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class EventCreate(BaseModel):
    node_id: UUID | None = None
    source_id: UUID | None = None
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None


class Subgraph(BaseModel):
    nodes: list[Node]
    edges: list[Edge]


class IngestResponse(BaseModel):
    source: Source
    extracted_nodes: list[Node] = Field(default_factory=list)
    extracted_edges: list[Edge] = Field(default_factory=list)


class QueryResult(BaseModel):
    query: str
    nodes: list[Node]
    subgraph: Subgraph


class ExtractRequest(BaseModel):
    source_id: UUID | None = None
    text: str | None = None
    hints: list[str] = Field(default_factory=list)


class ExtractResponse(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
