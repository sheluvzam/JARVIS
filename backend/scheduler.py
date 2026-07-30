"""Background task that makes JARVIS self-scheduling: wakes up on
`config.WORKFLOW_POLL_INTERVAL_SECONDS`, finds due workflows (see
WorkflowStore.due_workflows), and runs each one as a fresh, unattended
orchestrator turn via agent_core.run_agent_turn — the same roster and
sub-agents a live chat message would reach, just with no human present.

Same shape as events.py's live_event_simulator: an infinite loop, always
broadcasting through the shared ws_manager, never touching a raw socket.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from backend import config
from backend.agent_core import run_agent_turn
from backend.workflow_store import WorkflowStore

logger = logging.getLogger("jarvis.scheduler")


async def _run_due_workflow(workflow_store: WorkflowStore, agent_options, ws_manager, workflow: dict) -> None:
    ran_at = datetime.now(timezone.utc)
    try:
        result = await run_agent_turn(agent_options, workflow["instruction"])
    except Exception as exc:
        logger.exception("workflow %s failed", workflow["id"])
        result = f"(workflow run failed: {exc})"

    preview = result[: config.WORKFLOW_RESULT_PREVIEW_CHARS]
    workflow_store.mark_run(workflow["id"], preview, ran_at)
    ws_manager.broadcast(
        {
            "type": "workflow_run",
            "workflow_id": workflow["id"],
            "instruction": workflow["instruction"],
            "result": result,
            "timestamp": ran_at.isoformat(),
        }
    )


async def workflow_scheduler(workflow_store: WorkflowStore, agent_options, ws_manager) -> None:
    while True:
        await asyncio.sleep(config.WORKFLOW_POLL_INTERVAL_SECONDS)
        now = datetime.now(timezone.utc)
        for workflow in workflow_store.due_workflows(now):
            await _run_due_workflow(workflow_store, agent_options, ws_manager, workflow)
