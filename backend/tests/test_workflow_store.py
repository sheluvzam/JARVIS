"""Unit tests for backend/workflow_store.py — deterministic due-check logic
(fed explicit `now` values, never real sleeping) and CRUD round-trips.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.workflow_store import WorkflowStore, WorkflowValidationError


def _store(tmp_path) -> WorkflowStore:
    return WorkflowStore(tmp_path / "workflows.db")


def test_create_interval_round_trip(tmp_path):
    store = _store(tmp_path)
    wf = store.create("check tech news", "interval", interval_minutes=30)
    assert wf["schedule_kind"] == "interval"
    assert wf["interval_minutes"] == 30
    assert wf["daily_time_utc"] is None
    assert wf["enabled"] is True
    assert wf["last_run_at"] is None

    listed = store.list()
    assert len(listed) == 1
    assert listed[0]["id"] == wf["id"]


def test_create_daily_round_trip(tmp_path):
    store = _store(tmp_path)
    wf = store.create("morning briefing", "daily", daily_time_utc="08:00")
    assert wf["schedule_kind"] == "daily"
    assert wf["daily_time_utc"] == "08:00"
    assert wf["interval_minutes"] is None


def test_create_rejects_empty_instruction(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(WorkflowValidationError):
        store.create("   ", "interval", interval_minutes=5)


def test_create_rejects_bad_schedule_kind(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(WorkflowValidationError):
        store.create("do X", "hourly", interval_minutes=5)


def test_create_rejects_missing_interval_minutes(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(WorkflowValidationError):
        store.create("do X", "interval")


def test_create_rejects_zero_or_negative_interval(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(WorkflowValidationError):
        store.create("do X", "interval", interval_minutes=0)


def test_create_rejects_malformed_daily_time(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(WorkflowValidationError):
        store.create("do X", "daily", daily_time_utc="8am")
    with pytest.raises(WorkflowValidationError):
        store.create("do X", "daily", daily_time_utc="25:00")


def test_delete_removes_and_reports_success(tmp_path):
    store = _store(tmp_path)
    wf = store.create("do X", "interval", interval_minutes=5)
    assert store.delete(wf["id"]) is True
    assert store.list() == []


def test_delete_unknown_id_returns_false(tmp_path):
    store = _store(tmp_path)
    assert store.delete("no-such-workflow") is False


def test_interval_workflow_due_only_after_elapsed(tmp_path):
    store = _store(tmp_path)
    wf = store.create("do X", "interval", interval_minutes=10)
    now = datetime.now(timezone.utc)

    # Never run yet — due immediately regardless of "now".
    assert wf["id"] in [w["id"] for w in store.due_workflows(now)]

    store.mark_run(wf["id"], "ran", now)
    assert store.due_workflows(now + timedelta(minutes=5)) == []
    due_later = store.due_workflows(now + timedelta(minutes=10, seconds=1))
    assert wf["id"] in [w["id"] for w in due_later]


def test_daily_workflow_due_once_past_time_not_twice_same_day(tmp_path):
    store = _store(tmp_path)
    wf = store.create("morning briefing", "daily", daily_time_utc="08:00")

    before = datetime(2026, 7, 30, 7, 59, tzinfo=timezone.utc)
    assert store.due_workflows(before) == []

    at_time = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
    due = store.due_workflows(at_time)
    assert wf["id"] in [w["id"] for w in due]

    store.mark_run(wf["id"], "ran", at_time)
    later_same_day = datetime(2026, 7, 30, 20, 0, tzinfo=timezone.utc)
    assert store.due_workflows(later_same_day) == []

    next_day = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)
    due_next_day = store.due_workflows(next_day)
    assert wf["id"] in [w["id"] for w in due_next_day]


def test_disabled_workflow_never_due(tmp_path):
    store = _store(tmp_path)
    wf = store.create("do X", "interval", interval_minutes=1)
    with store._lock:
        store._conn.execute("UPDATE workflows SET enabled = 0 WHERE id = ?", (wf["id"],))
        store._conn.commit()
    assert store.due_workflows(datetime.now(timezone.utc) + timedelta(hours=1)) == []
