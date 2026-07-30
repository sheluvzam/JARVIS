"""Unit tests for backend/task_store.py — deterministic CRUD and
focus-session logic (fed explicit `now` values for elapsed-time checks,
never real sleeping, same pattern as test_workflow_store.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.task_store import TaskStore, TaskValidationError


def _store(tmp_path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.db")


# --- tasks ---


def test_create_task_round_trip(tmp_path):
    store = _store(tmp_path)
    task = store.create_task("write the report", notes="due before standup")
    assert task["status"] == "open"
    assert task["completed_at"] is None
    assert task["notes"] == "due before standup"

    open_tasks = store.list_tasks("open")
    assert len(open_tasks) == 1
    assert open_tasks[0]["id"] == task["id"]


def test_create_task_rejects_empty_title(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(TaskValidationError):
        store.create_task("   ")


def test_list_tasks_filters_by_status(tmp_path):
    store = _store(tmp_path)
    open_task = store.create_task("open one")
    done_task = store.create_task("done one")
    store.complete_task(done_task["id"])

    assert [t["id"] for t in store.list_tasks("open")] == [open_task["id"]]
    assert [t["id"] for t in store.list_tasks("done")] == [done_task["id"]]
    all_ids = {t["id"] for t in store.list_tasks("all")}
    assert all_ids == {open_task["id"], done_task["id"]}


def test_list_tasks_rejects_bad_filter(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(TaskValidationError):
        store.list_tasks("archived")


def test_complete_task_sets_status_and_timestamp(tmp_path):
    store = _store(tmp_path)
    task = store.create_task("do the thing")
    completed = store.complete_task(task["id"])
    assert completed["status"] == "done"
    assert completed["completed_at"] is not None


def test_complete_task_unknown_id_returns_none(tmp_path):
    store = _store(tmp_path)
    assert store.complete_task("no-such-task") is None


def test_complete_task_already_done_returns_none(tmp_path):
    store = _store(tmp_path)
    task = store.create_task("do the thing")
    store.complete_task(task["id"])
    assert store.complete_task(task["id"]) is None


def test_delete_task_removes_and_reports_success(tmp_path):
    store = _store(tmp_path)
    task = store.create_task("do the thing")
    assert store.delete_task(task["id"]) is True
    assert store.list_tasks("all") == []


def test_delete_task_unknown_id_returns_false(tmp_path):
    store = _store(tmp_path)
    assert store.delete_task("no-such-task") is False


# --- focus sessions ---


def test_start_focus_session_round_trip(tmp_path):
    store = _store(tmp_path)
    session = store.start_focus_session("deep work", planned_minutes=25)
    assert session["label"] == "deep work"
    assert session["planned_minutes"] == 25
    assert session["ended_at"] is None


def test_start_focus_session_rejects_empty_label(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(TaskValidationError):
        store.start_focus_session("  ")


def test_start_focus_session_rejects_nonpositive_planned_minutes(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(TaskValidationError):
        store.start_focus_session("deep work", planned_minutes=0)


def test_start_focus_session_rejects_concurrent_session(tmp_path):
    store = _store(tmp_path)
    store.start_focus_session("deep work")
    with pytest.raises(TaskValidationError, match="already active"):
        store.start_focus_session("another one")


def test_end_focus_session_clears_active_session(tmp_path):
    store = _store(tmp_path)
    store.start_focus_session("deep work")
    ended = store.end_focus_session()
    assert ended["ended_at"] is not None

    # A new session can now start.
    store.start_focus_session("another one")


def test_end_focus_session_with_none_active_returns_none(tmp_path):
    store = _store(tmp_path)
    assert store.end_focus_session() is None


def test_get_focus_status_when_inactive(tmp_path):
    store = _store(tmp_path)
    status = store.get_focus_status(datetime.now(timezone.utc))
    assert status == {"active": False}


def test_get_focus_status_computes_elapsed_and_over_planned(tmp_path):
    store = _store(tmp_path)
    session = store.start_focus_session("deep work", planned_minutes=25)
    started_at = datetime.fromisoformat(session["started_at"])

    mid_session = store.get_focus_status(started_at + timedelta(minutes=10))
    assert mid_session["active"] is True
    assert mid_session["elapsed_minutes"] == pytest.approx(10.0, abs=0.1)
    assert mid_session["over_planned"] is False

    past_planned = store.get_focus_status(started_at + timedelta(minutes=30))
    assert past_planned["over_planned"] is True


def test_get_focus_status_without_planned_minutes_never_over(tmp_path):
    store = _store(tmp_path)
    session = store.start_focus_session("open-ended")
    started_at = datetime.fromisoformat(session["started_at"])
    status = store.get_focus_status(started_at + timedelta(hours=5))
    assert status["over_planned"] is False
