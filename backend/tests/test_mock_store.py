"""Unit tests for backend/mock_store.py — regression guard against the kind
of silent breakage a bad rename/refactor of the shape or seeding could
cause (e.g. this session's MOCK_EMBEDDING_DIM split).
"""
from __future__ import annotations

from backend import config
from backend.mock_store import MockMindStore


def test_construction_does_not_raise():
    MockMindStore(seed=42)


def test_skeleton_shape_matches_config():
    store = MockMindStore(seed=42)
    skeleton = store.get_skeleton()

    assert len(skeleton["nodes"]["agents"]) == config.AGENT_COUNT
    assert skeleton["stats"]["memories_total"] == config.MEMORY_COUNT
    assert skeleton["stats"]["memories_shown"] == min(config.MEMORY_COUNT, config.MAX_MEMORIES_SERVED)
    assert len(skeleton["nodes"]["memories"]) == skeleton["stats"]["memories_shown"]

    region_ids = {r["id"] for r in skeleton["regions"]}
    assert region_ids == {"core", "memories", "tools", "agents"}

    assert skeleton["config"]["similarity_threshold"] == config.MOCK_SIMILARITY_THRESHOLD
    assert skeleton["config"]["similarity_top_k"] == config.MOCK_SIMILARITY_TOP_K


def test_skeleton_edge_kinds_present():
    store = MockMindStore(seed=42)
    skeleton = store.get_skeleton()
    assert set(skeleton["edges"].keys()) == {"memory_similarity", "trunk", "agent_tool"}
    # Every agent gets a trunk edge from core, plus the two hub trunk edges.
    assert len(skeleton["edges"]["trunk"]) == config.AGENT_COUNT + 2


def test_same_seed_is_deterministic():
    store_a = MockMindStore(seed=7)
    store_b = MockMindStore(seed=7)

    memories_a = [(m["label"], m["content_preview"], m["tags"]) for m in store_a.memories]
    memories_b = [(m["label"], m["content_preview"], m["tags"]) for m in store_b.memories]
    assert memories_a == memories_b

    agents_a = [(a["label"], a["status"], a["activity_level"]) for a in store_a.agents]
    agents_b = [(a["label"], a["status"], a["activity_level"]) for a in store_b.agents]
    assert agents_a == agents_b


def test_different_seeds_diverge():
    store_a = MockMindStore(seed=1)
    store_b = MockMindStore(seed=2)
    labels_a = [m["content_preview"] for m in store_a.memories]
    labels_b = [m["content_preview"] for m in store_b.memories]
    assert labels_a != labels_b


def test_add_memory_returns_edges_against_existing():
    store = MockMindStore(seed=42)
    memory, edges = store.add_memory()
    assert memory["id"] not in [m["id"] for m in store.memories[:-1]]
    for edge in edges:
        assert edge["source_id"] == memory["id"]
        assert 0.0 <= edge["weight"] <= 1.0


def test_set_agent_status_updates_existing_agent():
    store = MockMindStore(seed=42)
    agent_id = store.agents[0]["id"]
    updated = store.set_agent_status(agent_id, "active", 0.42)
    assert updated is not None
    assert updated["status"] == "active"
    assert updated["activity_level"] == 0.42


def test_set_agent_status_unknown_id_returns_none():
    store = MockMindStore(seed=42)
    assert store.set_agent_status("no-such-agent", "active", 0.5) is None
