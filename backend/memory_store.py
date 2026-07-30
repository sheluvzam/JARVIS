"""Real, durable memory store: SQLite for persistence, an in-memory numpy
cache (embeddings + row lookup) for fast cosine-similarity search — same
shape `MockMindStore` already uses, just backed by disk instead of
synthetic generation.

Constructed exactly once in app.py's lifespan, on `app.state.mind_store` —
the REST skeleton handler and the chat WS's `remember`/`recall` tool
handlers all reach this one instance, which is what guarantees a memory
written by real agent activity is visible on the next skeleton read (see
`mock_store.py`'s docstring for the failure mode this discipline avoids).

Agent/tool *identity* (id/label/role/category/owner) comes from
`agent_roster.py` — the same lists that build `ClaudeAgentOptions` — so the
visualized roster can never drift from the real one. Agent/tool *runtime
state* (status, activity_level, usage_count) lives only in memory here, not
in SQLite: a freshly started process has nothing mid-task by definition, so
persisting "last known status" across a restart would be misleading, not
accurate. Only memories are durable.

Similarity tuning note: `REAL_SIMILARITY_THRESHOLD`/`REAL_SIMILARITY_TOP_K`
(backend/config.py) are placeholders tuned against HashingVectorizer's
sparser, lower-baseline cosine similarities (see embeddings.py) — expect to
retune after real usage. Same diagnostic the mock's config already
establishes: a near-complete graph on /mind means the threshold is too low
(raise it); near-zero edges means it's too high (lower it).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from backend import config
from backend.agent_roster import AGENTS, TOOLS
from backend.similarity import cosine_similarity_matrix, edges_for_new_vector, l2_normalize, top_k_edges

RECENCY_WINDOW_DAYS = 21
CONTENT_PREVIEW_LENGTH = 140

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    content TEXT NOT NULL,
    importance REAL NOT NULL,
    created_at TEXT NOT NULL,
    tags TEXT NOT NULL,
    embedding BLOB NOT NULL,
    embedding_dim INTEGER NOT NULL
)
"""


def _recency_from_created_at(created_at: datetime) -> float:
    age_days = (datetime.now(timezone.utc) - created_at).total_seconds() / 86400
    return max(0.0, min(1.0, 1 - age_days / RECENCY_WINDOW_DAYS))


def _content_preview(content: str) -> str:
    content = content.strip()
    if len(content) <= CONTENT_PREVIEW_LENGTH:
        return content
    return content[: CONTENT_PREVIEW_LENGTH - 1].rstrip() + "…"


def _memory_dto(m: dict) -> dict:
    return {
        "id": m["id"],
        "label": m["label"],
        "content_preview": m["content_preview"],
        "recency": _recency_from_created_at(m["created_at"]),
        "importance": m["importance"],
        "created_at": m["created_at"],
        "tags": m["tags"],
        "embedding_dim": m["embedding_dim"],
    }


class RealMindStore:
    def __init__(self, db_path: Path, embedder):
        self._embedder = embedder
        self._lock = threading.Lock()

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

        self.core = {"id": "core", "label": "Core", "activity_level": 0.3}
        # Identity from agent_roster.py (single source of truth, also used
        # to build ClaudeAgentOptions); runtime state added here.
        self.agents = [{**a, "status": "idle", "activity_level": 0.0} for a in AGENTS]
        self.tools = [{**t, "usage_count": 0} for t in TOOLS]

        self.memories: list[dict] = []
        self.embeddings = np.zeros((0, config.REAL_EMBEDDING_DIM), dtype=np.float32)
        self._load_from_disk()

    # --- construction ---

    def _load_from_disk(self) -> None:
        rows = self._conn.execute(
            "SELECT id, label, content, importance, created_at, tags, embedding, embedding_dim "
            "FROM memories ORDER BY created_at"
        ).fetchall()
        embeddings = []
        for mem_id, label, content, importance, created_at, tags_json, embedding_blob, embedding_dim in rows:
            self.memories.append(
                {
                    "id": mem_id,
                    "label": label,
                    "content": content,
                    "content_preview": _content_preview(content),
                    "importance": importance,
                    "created_at": datetime.fromisoformat(created_at),
                    "tags": json.loads(tags_json),
                    "embedding_dim": embedding_dim,
                }
            )
            embeddings.append(np.frombuffer(embedding_blob, dtype=np.float32))
        if embeddings:
            self.embeddings = np.vstack(embeddings)

    # --- reads ---

    def get_skeleton(self) -> dict:
        with self._lock:
            ids = [m["id"] for m in self.memories]
            memory_nodes = [_memory_dto(m) for m in self.memories]
            unit = l2_normalize(self.embeddings)
            agents_snapshot = [dict(a) for a in self.agents]
            tools_snapshot = [dict(t) for t in self.tools]

        sim_matrix = cosine_similarity_matrix(unit)
        raw_edges = top_k_edges(sim_matrix, ids, config.REAL_SIMILARITY_TOP_K, config.REAL_SIMILARITY_THRESHOLD)
        memory_similarity_edges = [
            {"id": f"sim-{a}-{b}", "source_id": a, "target_id": b, "weight": w} for a, b, w in raw_edges
        ]

        trunk_edges = [
            {"id": "trunk-memories", "source_id": "core", "target_id": "hub-memories", "weight": 1.0},
            {"id": "trunk-tools", "source_id": "core", "target_id": "hub-tools", "weight": 1.0},
        ] + [
            {
                "id": f"trunk-{a['id']}",
                "source_id": "core",
                "target_id": a["id"],
                "weight": max(0.1, a["activity_level"]),
            }
            for a in agents_snapshot
        ]
        agent_tool_edges = [
            {"id": f"owns-{t['id']}", "source_id": t["owner_agent_id"], "target_id": t["id"], "weight": 1.0}
            for t in tools_snapshot
            if t["owner_agent_id"]
        ]

        regions = [
            {"id": "core", "label": "Core", "color_hex": "#eaffff", "node_count": 1},
            {"id": "memories", "label": "Memories", "color_hex": "#00d9ff", "node_count": len(memory_nodes)},
            {"id": "tools", "label": "Tools", "color_hex": "#4fd1ff", "node_count": len(tools_snapshot)},
            {"id": "agents", "label": "Agents", "color_hex": "#ff9d3d", "node_count": len(agents_snapshot)},
        ]

        node_count = 1 + len(memory_nodes) + len(tools_snapshot) + len(agents_snapshot)
        edge_count = len(memory_similarity_edges) + len(trunk_edges) + len(agent_tool_edges)

        return {
            "generated_at": datetime.now(timezone.utc),
            "config": {
                "similarity_threshold": config.REAL_SIMILARITY_THRESHOLD,
                "similarity_top_k": config.REAL_SIMILARITY_TOP_K,
            },
            "regions": regions,
            "nodes": {"core": self.core, "memories": memory_nodes, "tools": tools_snapshot, "agents": agents_snapshot},
            "edges": {
                "memory_similarity": memory_similarity_edges,
                "trunk": trunk_edges,
                "agent_tool": agent_tool_edges,
            },
            "stats": {
                "node_count": node_count,
                "edge_count": edge_count,
                # Real store serves everything it has — no truncation cap
                # (unlike the mock's deliberately-capped demo), so shown
                # always equals total.
                "memories_shown": len(memory_nodes),
                "memories_total": len(memory_nodes),
            },
        }

    # --- writes ---

    async def remember(
        self, content: str, tags: list[str] | None = None, importance: float = 0.5
    ) -> tuple[dict, list[dict]]:
        """Embeds + persists a new memory, computes its similarity edges
        against everything already stored, and returns both — so a
        `memory_created` broadcast is never missing edges."""
        return await asyncio.to_thread(self._remember_sync, content, tags or [], importance)

    def _remember_sync(self, content: str, tags: list[str], importance: float) -> tuple[dict, list[dict]]:
        with self._lock:
            embedding = l2_normalize(self._embedder.embed(content).reshape(1, -1))[0]
            existing_ids = [m["id"] for m in self.memories]
            existing_unit = l2_normalize(self.embeddings)

            mem_id = f"memory-{uuid.uuid4().hex}"
            created_at = datetime.now(timezone.utc)
            preview = _content_preview(content)
            memory = {
                "id": mem_id,
                "label": preview[:60],
                "content": content,
                "content_preview": preview,
                "importance": importance,
                "created_at": created_at,
                "tags": tags,
                "embedding_dim": config.REAL_EMBEDDING_DIM,
            }

            self._conn.execute(
                "INSERT INTO memories (id, label, content, importance, created_at, tags, embedding, embedding_dim) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mem_id,
                    memory["label"],
                    content,
                    importance,
                    created_at.isoformat(),
                    json.dumps(tags),
                    embedding.tobytes(),
                    config.REAL_EMBEDDING_DIM,
                ),
            )
            self._conn.commit()

            self.memories.append(memory)
            self.embeddings = (
                np.vstack([self.embeddings, embedding.reshape(1, -1)])
                if self.embeddings.size
                else embedding.reshape(1, -1)
            )

            raw_edges = edges_for_new_vector(
                embedding,
                existing_unit,
                existing_ids,
                mem_id,
                config.REAL_SIMILARITY_TOP_K,
                config.REAL_SIMILARITY_THRESHOLD,
            )
            edges = [
                {"id": f"sim-{mem_id}-{other}", "source_id": mem_id, "target_id": other, "weight": w}
                for _new, other, w in raw_edges
            ]

            return _memory_dto(memory), edges

    async def recall(self, query: str, top_k: int = 5) -> list[tuple[dict, float]]:
        """Semantic search over ALL stored memories (not capped) — recall is
        a search operation, unrelated to any UI-payload serving cap."""
        return await asyncio.to_thread(self._recall_sync, query, top_k)

    def _recall_sync(self, query: str, top_k: int) -> list[tuple[dict, float]]:
        with self._lock:
            if not self.memories:
                return []
            query_unit = l2_normalize(self._embedder.embed(query).reshape(1, -1))[0]
            existing_unit = l2_normalize(self.embeddings)
            sims = existing_unit @ query_unit
            top_indices = np.argsort(sims)[::-1][:top_k]
            return [(_memory_dto(self.memories[i]), float(sims[i])) for i in top_indices]

    def set_agent_status(self, agent_id: str, status: str, activity_level: float) -> dict | None:
        with self._lock:
            for agent in self.agents:
                if agent["id"] == agent_id:
                    agent["status"] = status
                    agent["activity_level"] = activity_level
                    return dict(agent)
            return None

    def record_tool_use(self, tool_id: str) -> None:
        with self._lock:
            for tool in self.tools:
                if tool["id"] == tool_id:
                    tool["usage_count"] += 1
                    return
