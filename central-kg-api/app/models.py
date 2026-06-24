from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NodeType = Literal[
    "person",
    "company",
    "meeting",
    "task",
    "workflow",
    "requirement",
    "feature",
    "user_story",
    "code_module",
    "product",
    "source_document",
    "topic",
    "decision",
]

EdgeType = Literal[
    "depends_on",
    "discussed_in",
    "implements",
    "relates_to",
    "derived_from",
    "assigned_to",
    "blocks",
    "mentions",
    "part_of",
    "owns",
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
