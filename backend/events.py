"""Background task simulating live agent activity — memory recalls, new
memories, and agent status changes — broadcast over the read-only WS.

Always goes through ConnectionManager.broadcast() (never a raw socket) and
always mutates the one shared MockMindStore instance the REST handler also
reads, so a `memory_created` event is never missing its similarity edges.
"""
from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timezone

from backend import config
from backend.mock_store import MockMindStore
from backend.ws_manager import ConnectionManager


def _jsonable_memory(memory: dict) -> dict:
    return {**memory, "created_at": memory["created_at"].isoformat()}


async def live_event_simulator(store: MockMindStore, manager: ConnectionManager):
    rng = random.Random()
    while True:
        await asyncio.sleep(rng.uniform(*config.EVENT_INTERVAL_SECONDS))
        action = rng.choices(["recall", "create", "agent_status"], weights=[0.55, 0.2, 0.25], k=1)[0]

        if action == "recall":
            memory = store.random_existing_memory()
            if memory is None:
                continue
            manager.broadcast(
                {
                    "type": "memory_recalled",
                    "event_id": uuid.uuid4().hex,
                    "memory_id": memory["id"],
                    "strength": round(rng.uniform(0.4, 1.0), 2),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        elif action == "create":
            memory, edges = store.add_memory()
            manager.broadcast(
                {"type": "memory_created", "memory": _jsonable_memory(memory), "similarity_edges": edges}
            )
        else:
            agent = store.random_agent()
            if agent is None:
                continue
            status = rng.choice(["idle", "active", "thinking"])
            activity = round(rng.uniform(0.2, 0.95), 2)
            store.set_agent_status(agent["id"], status, activity)
            manager.broadcast(
                {"type": "agent_status", "agent_id": agent["id"], "status": status, "activity_level": activity}
            )
