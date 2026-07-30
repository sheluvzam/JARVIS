"""Persistent, time-based workflow schedules — the storage layer behind
JARVIS's ability to run itself unattended (see backend/scheduler.py for
the background task that actually executes due ones).

Two schedule kinds:
- "interval": fires every `interval_minutes` minutes.
- "daily": fires once per UTC calendar day, at or after `daily_time_utc`
  ("HH:MM", 24h, always UTC — there's no per-user timezone setting yet).

`due_workflows` takes `now` as an explicit parameter rather than reading
the wall clock itself — the only way to make the due-check logic
deterministically unit-testable without real sleeping.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    instruction TEXT NOT NULL,
    schedule_kind TEXT NOT NULL,
    interval_minutes INTEGER,
    daily_time_utc TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    last_result_preview TEXT
)
"""

_DAILY_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class WorkflowValidationError(ValueError):
    """Bad input to create() — callers (the jarvis_workflow MCP tools) turn
    this into a plain-text tool result rather than a crash."""


def _row_to_dict(row) -> dict:
    (
        id_,
        instruction,
        schedule_kind,
        interval_minutes,
        daily_time_utc,
        enabled,
        created_at,
        last_run_at,
        last_result_preview,
    ) = row
    return {
        "id": id_,
        "instruction": instruction,
        "schedule_kind": schedule_kind,
        "interval_minutes": interval_minutes,
        "daily_time_utc": daily_time_utc,
        "enabled": bool(enabled),
        "created_at": created_at,
        "last_run_at": last_run_at,
        "last_result_preview": last_result_preview,
    }


class WorkflowStore:
    def __init__(self, db_path: Path):
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def create(
        self,
        instruction: str,
        schedule_kind: str,
        interval_minutes: int | None = None,
        daily_time_utc: str | None = None,
    ) -> dict:
        instruction = instruction.strip()
        if not instruction:
            raise WorkflowValidationError("instruction must not be empty")

        if schedule_kind == "interval":
            if not interval_minutes or interval_minutes < 1:
                raise WorkflowValidationError(
                    "interval_minutes must be a positive integer for an interval workflow"
                )
            daily_time_utc = None
        elif schedule_kind == "daily":
            if not daily_time_utc or not _DAILY_TIME_RE.match(daily_time_utc):
                raise WorkflowValidationError("daily_time_utc must be 'HH:MM' (24h, UTC) for a daily workflow")
            interval_minutes = None
        else:
            raise WorkflowValidationError("schedule_kind must be 'interval' or 'daily'")

        workflow_id = f"workflow-{uuid.uuid4().hex}"
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO workflows (id, instruction, schedule_kind, interval_minutes, daily_time_utc, "
                "enabled, created_at, last_run_at, last_result_preview) VALUES (?, ?, ?, ?, ?, 1, ?, NULL, NULL)",
                (workflow_id, instruction, schedule_kind, interval_minutes, daily_time_utc, created_at),
            )
            self._conn.commit()
        return {
            "id": workflow_id,
            "instruction": instruction,
            "schedule_kind": schedule_kind,
            "interval_minutes": interval_minutes,
            "daily_time_utc": daily_time_utc,
            "enabled": True,
            "created_at": created_at,
            "last_run_at": None,
            "last_result_preview": None,
        }

    def list(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, instruction, schedule_kind, interval_minutes, daily_time_utc, enabled, "
                "created_at, last_run_at, last_result_preview FROM workflows ORDER BY created_at"
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def delete(self, workflow_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def due_workflows(self, now: datetime) -> list[dict]:
        due = []
        for wf in self.list():
            if not wf["enabled"]:
                continue
            last_run_at = datetime.fromisoformat(wf["last_run_at"]) if wf["last_run_at"] else None

            if wf["schedule_kind"] == "interval":
                if last_run_at is None or now - last_run_at >= timedelta(minutes=wf["interval_minutes"]):
                    due.append(wf)
            else:  # "daily"
                hour, minute = (int(part) for part in wf["daily_time_utc"].split(":"))
                scheduled_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                already_ran_today = last_run_at is not None and last_run_at.date() == now.date()
                if now >= scheduled_today and not already_ran_today:
                    due.append(wf)
        return due

    def mark_run(self, workflow_id: str, result_preview: str, ran_at: datetime) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE workflows SET last_run_at = ?, last_result_preview = ? WHERE id = ?",
                (ran_at.isoformat(), result_preview, workflow_id),
            )
            self._conn.commit()
