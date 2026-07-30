"""Persistent task list + focus-session state — the storage layer behind
the Tasks sub-agent. Two related, lightweight personal-productivity
concerns in one store, same reasoning that already bundles remember/recall
under one Memory agent rather than splitting them.

`get_focus_status` takes `now` as an explicit parameter rather than
reading the wall clock itself — the same deterministic-testing pattern
used by WorkflowStore.due_workflows.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS focus_sessions (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    planned_minutes INTEGER,
    started_at TEXT NOT NULL,
    ended_at TEXT
)
"""


class TaskValidationError(ValueError):
    """Bad input — callers (the jarvis_tasks MCP tools) turn this into a
    plain-text tool result rather than a crash."""


def _task_row_to_dict(row) -> dict:
    id_, title, notes, status, created_at, completed_at = row
    return {
        "id": id_,
        "title": title,
        "notes": notes,
        "status": status,
        "created_at": created_at,
        "completed_at": completed_at,
    }


def _focus_row_to_dict(row) -> dict:
    id_, label, planned_minutes, started_at, ended_at = row
    return {
        "id": id_,
        "label": label,
        "planned_minutes": planned_minutes,
        "started_at": started_at,
        "ended_at": ended_at,
    }


class TaskStore:
    def __init__(self, db_path: Path):
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # --- tasks ---

    def create_task(self, title: str, notes: str | None = None) -> dict:
        title = title.strip()
        if not title:
            raise TaskValidationError("title must not be empty")

        task_id = f"task-{uuid.uuid4().hex}"
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (id, title, notes, status, created_at, completed_at) "
                "VALUES (?, ?, ?, 'open', ?, NULL)",
                (task_id, title, notes, created_at),
            )
            self._conn.commit()
        return {
            "id": task_id,
            "title": title,
            "notes": notes,
            "status": "open",
            "created_at": created_at,
            "completed_at": None,
        }

    def list_tasks(self, status_filter: str = "open") -> list[dict]:
        if status_filter not in ("open", "done", "all"):
            raise TaskValidationError("status_filter must be 'open', 'done', or 'all'")
        with self._lock:
            if status_filter == "all":
                rows = self._conn.execute(
                    "SELECT id, title, notes, status, created_at, completed_at FROM tasks ORDER BY created_at"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, title, notes, status, created_at, completed_at FROM tasks "
                    "WHERE status = ? ORDER BY created_at",
                    (status_filter,),
                ).fetchall()
        return [_task_row_to_dict(row) for row in rows]

    def complete_task(self, task_id: str) -> dict | None:
        completed_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ? AND status = 'open'",
                (completed_at, task_id),
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                return None
            row = self._conn.execute(
                "SELECT id, title, notes, status, created_at, completed_at FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return _task_row_to_dict(row)

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    # --- focus sessions ---

    def _active_focus_session(self) -> dict | None:
        row = self._conn.execute(
            "SELECT id, label, planned_minutes, started_at, ended_at FROM focus_sessions "
            "WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return _focus_row_to_dict(row) if row else None

    def start_focus_session(self, label: str, planned_minutes: int | None = None) -> dict:
        label = label.strip()
        if not label:
            raise TaskValidationError("label must not be empty")
        if planned_minutes is not None and planned_minutes < 1:
            raise TaskValidationError("planned_minutes must be a positive integer if given")

        with self._lock:
            if self._active_focus_session() is not None:
                raise TaskValidationError(
                    "a focus session is already active — end it before starting another"
                )
            session_id = f"focus-{uuid.uuid4().hex}"
            started_at = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT INTO focus_sessions (id, label, planned_minutes, started_at, ended_at) "
                "VALUES (?, ?, ?, ?, NULL)",
                (session_id, label, planned_minutes, started_at),
            )
            self._conn.commit()
        return {
            "id": session_id,
            "label": label,
            "planned_minutes": planned_minutes,
            "started_at": started_at,
            "ended_at": None,
        }

    def end_focus_session(self) -> dict | None:
        with self._lock:
            active = self._active_focus_session()
            if active is None:
                return None
            ended_at = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "UPDATE focus_sessions SET ended_at = ? WHERE id = ?", (ended_at, active["id"])
            )
            self._conn.commit()
        active["ended_at"] = ended_at
        return active

    def get_focus_status(self, now: datetime) -> dict:
        with self._lock:
            active = self._active_focus_session()
        if active is None:
            return {"active": False}

        started_at = datetime.fromisoformat(active["started_at"])
        elapsed_minutes = (now - started_at).total_seconds() / 60
        over_planned = (
            active["planned_minutes"] is not None and elapsed_minutes > active["planned_minutes"]
        )
        return {
            "active": True,
            "id": active["id"],
            "label": active["label"],
            "planned_minutes": active["planned_minutes"],
            "elapsed_minutes": round(elapsed_minutes, 1),
            "over_planned": over_planned,
        }
