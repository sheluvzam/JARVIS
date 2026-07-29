"""Self-contained mock mind data store — stands in for a real JARVIS memory
store until one exists. Deterministic synthetic regions/agents/tools/
memories with embeddings, realistic enough to demonstrate real similarity
structure (not random noise), extensible later.

`MockMindStore` must be constructed exactly once (see app.py's lifespan,
which stores the instance on `app.state.mind_store`) and shared by every
read and write path — the REST handler, the WS live-event simulator, and
any future write. Two separate instances would silently reproduce the
"new memories have no similarity edges" failure mode (reads and writes
landing on different in-memory state).
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np

from backend import config
from backend.similarity import cosine_similarity_matrix, edges_for_new_vector, l2_normalize, top_k_edges

AGENT_ROLES = [
    "Research",
    "Coding",
    "Scheduling",
    "Voice",
    "Memory",
    "Ops",
]

TOOL_CATALOG = [
    ("web_search", "research"),
    ("code_exec", "execution"),
    ("calendar", "scheduling"),
    ("email", "communication"),
    ("file_read", "data"),
    ("file_write", "data"),
    ("shell", "execution"),
    ("browser", "research"),
    ("calculator", "utility"),
    ("translator", "utility"),
    ("image_gen", "creative"),
    ("summarizer", "utility"),
    ("git", "execution"),
    ("database", "data"),
    ("vector_search", "data"),
    ("http_request", "execution"),
    ("scheduler", "scheduling"),
    ("notifier", "communication"),
    ("pdf_reader", "data"),
    ("spreadsheet", "data"),
]

MEMORY_TOPICS = [
    "the login flow",
    "background job retries",
    "the notification queue",
    "API rate limiting",
    "the onboarding checklist",
    "dark mode styling",
    "the export pipeline",
    "session expiry handling",
    "the search index",
    "voice latency",
    "the deploy pipeline",
    "calendar sync",
]

MEMORY_ACTIONS = [
    "fixed a race condition in",
    "learned a durable preference about",
    "debugged an intermittent failure in",
    "documented the correct usage of",
    "discovered a workaround for",
    "clarified an ambiguity in",
    "optimized the latency of",
    "recorded a lesson from a mistake in",
]

TAG_VOCAB = ["bug", "preference", "workflow", "performance", "ux", "infra", "security", "docs"]

RECENCY_WINDOW_DAYS = 21


def _recency_from_created_at(created_at: datetime) -> float:
    age_days = (datetime.now(timezone.utc) - created_at).total_seconds() / 86400
    return max(0.0, min(1.0, 1 - age_days / RECENCY_WINDOW_DAYS))


class MockMindStore:
    def __init__(self, seed: int = config.MOCK_SEED):
        self._rng = random.Random(seed)
        self._np_rng = np.random.RandomState(seed)

        self.core = {"id": "core", "label": "Core", "activity_level": 0.7}
        self.agents = self._build_agents()
        self.tools = self._build_tools(self.agents)
        self._topic_centers = self._build_topic_centers()
        self.memories: list[dict] = []
        self.embeddings = np.zeros((0, config.MOCK_EMBEDDING_DIM), dtype=np.float32)
        for i in range(config.MEMORY_COUNT):
            self._append_memory(index=i)

    # --- construction ---

    def _build_agents(self) -> list[dict]:
        agents = []
        for i, role in enumerate(AGENT_ROLES[: config.AGENT_COUNT]):
            agents.append(
                {
                    "id": f"agent-{i}",
                    "label": f"{role} Agent",
                    "role": role,
                    "status": self._rng.choice(["idle", "active", "thinking"]),
                    "activity_level": round(self._rng.uniform(0.2, 0.95), 2),
                    "tool_ids": [],
                }
            )
        return agents

    def _build_tools(self, agents: list[dict]) -> list[dict]:
        tools = []
        catalog = list(TOOL_CATALOG)
        self._rng.shuffle(catalog)
        pool = iter(catalog)

        for i in range(config.CENTRAL_TOOL_COUNT):
            name, category = next(pool, (f"tool_{i}", "utility"))
            tools.append(
                {
                    "id": f"tool-central-{i}",
                    "label": name,
                    "category": category,
                    "usage_count": self._rng.randint(0, 400),
                    "owner_agent_id": None,
                }
            )

        for agent in agents:
            n_owned = self._rng.randint(*config.TOOLS_PER_AGENT)
            for j in range(n_owned):
                name, category = next(pool, (f"{agent['id']}_tool_{j}", "utility"))
                tool_id = f"tool-{agent['id']}-{j}"
                tools.append(
                    {
                        "id": tool_id,
                        "label": name,
                        "category": category,
                        "usage_count": self._rng.randint(0, 200),
                        "owner_agent_id": agent["id"],
                    }
                )
                agent["tool_ids"].append(tool_id)

        return tools

    def _build_topic_centers(self) -> np.ndarray:
        centers = self._np_rng.normal(size=(len(MEMORY_TOPICS), config.MOCK_EMBEDDING_DIM)).astype(np.float32)
        return l2_normalize(centers)

    def _make_memory_embedding(self, topic_index: int) -> np.ndarray:
        # Real cluster structure (topic center + small noise), not pure
        # noise — otherwise every threshold reads as "dust with no
        # structure" regardless of tuning.
        center = self._topic_centers[topic_index]
        noise = self._np_rng.normal(scale=0.35, size=config.MOCK_EMBEDDING_DIM).astype(np.float32)
        return l2_normalize((center + noise).reshape(1, -1))[0]

    def _append_memory(self, index: int | None = None) -> dict:
        topic_index = self._rng.randrange(len(MEMORY_TOPICS))
        topic = MEMORY_TOPICS[topic_index]
        action = self._rng.choice(MEMORY_ACTIONS)
        age_days = self._rng.uniform(0, RECENCY_WINDOW_DAYS * 1.6)
        created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
        mem_id = f"memory-{index if index is not None else len(self.memories)}-{uuid.uuid4().hex[:6]}"

        memory = {
            "id": mem_id,
            "label": topic.title(),
            "content_preview": f"{action.capitalize()} {topic}.",
            "importance": round(self._rng.uniform(0.1, 1.0), 2),
            "created_at": created_at,
            "tags": self._rng.sample(TAG_VOCAB, k=self._rng.randint(1, 3)),
            "embedding_dim": config.MOCK_EMBEDDING_DIM,
        }
        embedding = self._make_memory_embedding(topic_index)
        self.memories.append(memory)
        self.embeddings = (
            np.vstack([self.embeddings, embedding.reshape(1, -1)]) if self.embeddings.size else embedding.reshape(1, -1)
        )
        return memory

    # --- reads ---

    def _served_memories(self) -> tuple[list[dict], np.ndarray]:
        # Most-recently-created first, capped at MAX_MEMORIES_SERVED — a
        # real (not silently hidden) truncation, reflected in stats.
        order = sorted(range(len(self.memories)), key=lambda i: self.memories[i]["created_at"], reverse=True)
        served_idx = order[: config.MAX_MEMORIES_SERVED]
        served = [self.memories[i] for i in served_idx]
        served_embeddings = self.embeddings[served_idx]
        return served, served_embeddings

    def get_skeleton(self) -> dict:
        served, served_embeddings = self._served_memories()
        ids = [m["id"] for m in served]
        unit = l2_normalize(served_embeddings)
        sim_matrix = cosine_similarity_matrix(unit)
        raw_edges = top_k_edges(sim_matrix, ids, config.MOCK_SIMILARITY_TOP_K, config.MOCK_SIMILARITY_THRESHOLD)

        memory_similarity_edges = [
            {"id": f"sim-{a}-{b}", "source_id": a, "target_id": b, "weight": w} for a, b, w in raw_edges
        ]
        memory_nodes = [{**m, "recency": _recency_from_created_at(m["created_at"])} for m in served]

        trunk_edges = [
            {"id": "trunk-memories", "source_id": "core", "target_id": "hub-memories", "weight": 1.0},
            {"id": "trunk-tools", "source_id": "core", "target_id": "hub-tools", "weight": 1.0},
        ] + [
            {"id": f"trunk-{a['id']}", "source_id": "core", "target_id": a["id"], "weight": a["activity_level"]}
            for a in self.agents
        ]
        agent_tool_edges = [
            {"id": f"owns-{t['id']}", "source_id": t["owner_agent_id"], "target_id": t["id"], "weight": 1.0}
            for t in self.tools
            if t["owner_agent_id"]
        ]

        regions = [
            {"id": "core", "label": "Core", "color_hex": "#f4e9ff", "node_count": 1},
            {"id": "memories", "label": "Memories", "color_hex": "#8a5cff", "node_count": len(served)},
            {"id": "tools", "label": "Tools", "color_hex": "#4fd1c5", "node_count": len(self.tools)},
            {"id": "agents", "label": "Agents", "color_hex": "#ffb454", "node_count": len(self.agents)},
        ]

        node_count = 1 + len(served) + len(self.tools) + len(self.agents)
        edge_count = len(memory_similarity_edges) + len(trunk_edges) + len(agent_tool_edges)

        return {
            "generated_at": datetime.now(timezone.utc),
            "config": {
                "similarity_threshold": config.MOCK_SIMILARITY_THRESHOLD,
                "similarity_top_k": config.MOCK_SIMILARITY_TOP_K,
            },
            "regions": regions,
            "nodes": {"core": self.core, "memories": memory_nodes, "tools": self.tools, "agents": self.agents},
            "edges": {
                "memory_similarity": memory_similarity_edges,
                "trunk": trunk_edges,
                "agent_tool": agent_tool_edges,
            },
            "stats": {
                "node_count": node_count,
                "edge_count": edge_count,
                "memories_shown": len(served),
                "memories_total": len(self.memories),
            },
        }

    # --- writes (driven by the live-event simulator, Tier 6) ---

    def add_memory(self) -> tuple[dict, list[dict]]:
        """Adds a synthetic new memory and computes its similarity edges
        against currently-served embeddings immediately, so a
        `memory_created` broadcast is never missing edges."""
        served, served_embeddings = self._served_memories()
        served_ids = [m["id"] for m in served]

        memory = self._append_memory()
        new_embedding = self.embeddings[-1]
        served_unit = l2_normalize(served_embeddings)
        raw_edges = edges_for_new_vector(
            new_embedding, served_unit, served_ids, memory["id"], config.MOCK_SIMILARITY_TOP_K, config.MOCK_SIMILARITY_THRESHOLD
        )
        edges = [
            {"id": f"sim-{memory['id']}-{other}", "source_id": memory["id"], "target_id": other, "weight": w}
            for _new, other, w in raw_edges
        ]
        memory_dto = {**memory, "recency": _recency_from_created_at(memory["created_at"])}
        return memory_dto, edges

    def random_existing_memory(self) -> dict | None:
        served, _ = self._served_memories()
        if not served:
            return None
        weights = [max(0.05, _recency_from_created_at(m["created_at"])) for m in served]
        return self._rng.choices(served, weights=weights, k=1)[0]

    def random_agent(self) -> dict | None:
        return self._rng.choice(self.agents) if self.agents else None

    def set_agent_status(self, agent_id: str, status: str, activity_level: float) -> dict | None:
        for agent in self.agents:
            if agent["id"] == agent_id:
                agent["status"] = status
                agent["activity_level"] = activity_level
                return agent
        return None
