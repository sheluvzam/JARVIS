"""Pydantic models for the mind-viz REST skeleton response and WebSocket
live-event contract (Tier 1 data contract).

Field names and shapes here are load-bearing: `static/mind/scene.js` and
`static/mind/layout.js` decode these directly. Keep the two in sync.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

RegionId = Literal["core", "memories", "tools", "agents"]
AgentStatus = Literal["idle", "active", "thinking"]
EdgeKind = Literal["memory_similarity", "trunk", "agent_tool"]


class RegionDTO(BaseModel):
    id: RegionId
    label: str
    color_hex: str
    node_count: int


class CoreNodeDTO(BaseModel):
    id: Literal["core"] = "core"
    label: str
    activity_level: float


class MemoryNodeDTO(BaseModel):
    id: str
    label: str
    content_preview: str
    recency: float
    importance: float
    created_at: datetime
    tags: list[str]
    embedding_dim: int


class ToolNodeDTO(BaseModel):
    id: str
    label: str
    category: str
    usage_count: int
    owner_agent_id: str | None


class AgentNodeDTO(BaseModel):
    id: str
    label: str
    role: str
    status: AgentStatus
    activity_level: float
    tool_ids: list[str]


class NodesDTO(BaseModel):
    core: CoreNodeDTO
    memories: list[MemoryNodeDTO]
    tools: list[ToolNodeDTO]
    agents: list[AgentNodeDTO]


class EdgeDTO(BaseModel):
    id: str
    source_id: str
    target_id: str
    weight: float


class EdgesDTO(BaseModel):
    memory_similarity: list[EdgeDTO]
    trunk: list[EdgeDTO]
    agent_tool: list[EdgeDTO]


class StatsDTO(BaseModel):
    node_count: int
    edge_count: int
    memories_shown: int
    memories_total: int


class SkeletonConfigDTO(BaseModel):
    similarity_threshold: float
    similarity_top_k: int


class SkeletonResponse(BaseModel):
    generated_at: datetime
    config: SkeletonConfigDTO
    regions: list[RegionDTO]
    nodes: NodesDTO
    edges: EdgesDTO
    stats: StatsDTO


# --- WebSocket live-event contract (server -> client push only) ---


class WsHello(BaseModel):
    type: Literal["hello"] = "hello"
    server_time: datetime


class WsHeartbeat(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"
    server_time: datetime


class WsMemoryRecalled(BaseModel):
    type: Literal["memory_recalled"] = "memory_recalled"
    event_id: str
    memory_id: str
    strength: float
    timestamp: datetime


class WsMemoryCreated(BaseModel):
    type: Literal["memory_created"] = "memory_created"
    memory: MemoryNodeDTO
    similarity_edges: list[EdgeDTO]


class WsAgentStatus(BaseModel):
    type: Literal["agent_status"] = "agent_status"
    agent_id: str
    status: AgentStatus
    activity_level: float
