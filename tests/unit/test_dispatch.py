from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from lara.core.io import plain_object
from lara.dispatch import (
    AUTOMATION_MESSAGE_KIND,
    DispatchService,
    DispatchStore,
    automation_trigger_message,
)


def _future(minutes: int = 10) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def _past(minutes: int = 1) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def _make_store(tmp_path: Path) -> DispatchStore:
    return DispatchStore(tmp_path / "dispatch" / "dispatch-v1.sqlite3")


def _create_task(store: DispatchStore, tmp_path: Path, **overrides: Any):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    values: dict[str, Any] = {
        "name": "daily check",
        "prompt": "Inspect the project",
        "workspace_root": str(workspace),
        "destination": "standalone",
        "timezone_name": "UTC",
        "at_time": _future(),
    }
    values.update(overrides)
    return store.create(**values)


def test_store_crud(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _create_task(store, tmp_path)

    assert task.task_id.startswith("auto-")
    assert task.status == "active"
    assert task.destination == "standalone"
    assert task.at_time is not None and task.at_time.endswith("+00:00")
    assert store.list()[0].task_id == task.task_id

    updated = store.update(task.task_id, name="renamed", prompt="New prompt")
    assert updated.name == "renamed"
    # A task update reactivates it, matching the legacy automation behavior.
    assert updated.status == "active"

    assert store.set_status(task.task_id, "paused").status == "paused"
    assert store.set_status(task.task_id, "active").status == "active"

    with pytest.raises(ValueError):
        store.update(task.task_id, workspace_root=str(tmp_path), bogus="x")
    with pytest.raises(KeyError):
        store.get("auto-missing")

    assert store.delete(task.task_id) is True
    assert store.delete(task.task_id) is False


def test_create_rejects_invalid_schedules(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    with pytest.raises(ValueError):
        _create_task(store, tmp_path, at_time=_future(), rrule="FREQ=DAILY")
    with pytest.raises(ValueError):
        _create_task(store, tmp_path, at_time=None, rrule=None)
    with pytest.raises(ValueError):
        _create_task(store, tmp_path, timezone_name="Mars/Olympus")
    with pytest.raises(ValueError):
        _create_task(store, tmp_path, destination="heartbeat", target_session_id=None)
    with pytest.raises(ValueError, match="no future occurrence"):
        _create_task(
            store,
            tmp_path,
            at_time=None,
            rrule="FREQ=DAILY;UNTIL=20200101T000000Z",
        )
    # Standalone tasks must not keep a target session.
    task = _create_task(store, tmp_path, target_session_id="sess-1")
    assert task.target_session_id is None
    heartbeat = _create_task(
        store, tmp_path, destination="heartbeat", target_session_id="sess-1"
    )
    assert heartbeat.target_session_id == "sess-1"


def test_resume_of_completed_one_time_task_is_rejected(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _create_task(store, tmp_path, at_time=_past())

    assert store.set_status(task.task_id, "paused").status == "paused"
    with pytest.raises(ValueError, match="cannot be resumed"):
        store.set_status(task.task_id, "active")


def test_request_run_materializes_turn_item(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _create_task(store, tmp_path)

    item = store.request_run(task.task_id)

    assert item.item_id.startswith("op-")
    assert item.kind == "turn"
    assert item.task_id == task.task_id
    assert item.state == "queued"
    assert item.workspace_root == task.workspace_root
    assert item.message == task.prompt
    assert item.case_id == "automation"
    assert item.agent_alias == ""
    with pytest.raises(KeyError):
        store.request_run("auto-missing")


def test_claim_lease_heartbeat_and_reclaim(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _create_task(store, tmp_path)
    item = store.request_run(task.task_id)

    [claimed] = store.claim_items(owner_id="owner-1", lease_seconds=30)
    assert claimed.item_id == item.item_id
    assert claimed.state == "claimed"
    assert claimed.owner_id == "owner-1"
    assert claimed.lease_until_epoch is not None

    # A claimed item is not handed out twice while its lease lives.
    assert store.claim_items(owner_id="owner-2", lease_seconds=30) == []

    assert store.heartbeat(item.item_id, owner_id="owner-2", lease_seconds=30) is False
    assert store.heartbeat(item.item_id, owner_id="owner-1", lease_seconds=30) is True

    # An expired lease returns the item to the queue for another owner.
    with store._connect() as connection:
        connection.execute(
            "UPDATE dispatch_items SET lease_until_epoch=? WHERE item_id=?",
            (0.0, item.item_id),
        )
        connection.commit()
    [reclaimed] = store.claim_items(owner_id="owner-2", lease_seconds=30)
    assert reclaimed.item_id == item.item_id
    assert reclaimed.owner_id == "owner-2"


def test_heartbeat_renewal_prevents_reclaim(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _create_task(store, tmp_path)
    item = store.request_run(task.task_id)
    store.claim_items(owner_id="owner-1", lease_seconds=0.05)
    assert store.heartbeat(item.item_id, owner_id="owner-1", lease_seconds=30) is True

    time.sleep(0.06)

    assert store.claim_items(owner_id="owner-2", lease_seconds=30) == []
    assert store.get_item(item.item_id).owner_id == "owner-1"


def test_mark_running_and_finish_require_ownership(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _create_task(store, tmp_path)
    item = store.request_run(task.task_id)
    store.claim_items(owner_id="owner-1", lease_seconds=30)

    with pytest.raises(RuntimeError, match="lease"):
        store.mark_running(item.item_id, owner_id="owner-2", lease_seconds=30)
    running = store.mark_running(
        item.item_id,
        owner_id="owner-1",
        execution_id="exec-1",
        case_run_id="case-run-1",
        lease_seconds=30,
    )
    assert running.state == "running"
    assert running.execution_id == "exec-1"

    with pytest.raises(ValueError, match="terminal"):
        store.finish_item(item.item_id, owner_id="owner-1", state="queued")
    with pytest.raises(RuntimeError, match="lease"):
        store.finish_item(item.item_id, owner_id="owner-2", state="passed")

    finished = store.finish_item(
        item.item_id, owner_id="owner-1", state="passed", final_answer="done"
    )
    assert finished.state == "passed"
    assert finished.final_answer == "done"
    assert finished.owner_id is None
    assert finished.lease_until_epoch is None
    assert finished.done is True
    with pytest.raises(RuntimeError, match="lease"):
        store.finish_item(item.item_id, owner_id="owner-1", state="passed")


def test_attach_target_records_created_session(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.submit_item(
        kind="start",
        workspace_root=str(tmp_path / "workspace"),
        message="begin work",
        case_id="collaboration",
    )
    [claimed] = store.claim_items(owner_id="owner-1", lease_seconds=30)

    with pytest.raises(RuntimeError, match="target session"):
        store.attach_target(claimed.item_id, "sess-9", owner_id="owner-2")
    attached = store.attach_target(claimed.item_id, "sess-9", owner_id="owner-1")
    assert attached.target_session_id == "sess-9"
    assert attached.state == "claimed"


def test_claim_skips_second_item_of_same_task(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _create_task(store, tmp_path)
    first = store.request_run(task.task_id)
    second = store.request_run(task.task_id)

    [claimed] = store.claim_items(owner_id="owner-1", lease_seconds=30)

    assert claimed.item_id == first.item_id
    skipped = store.get_item(second.item_id)
    assert skipped.state == "skipped"
    assert skipped.error == "previous automation run is still active"


def test_claim_serializes_items_for_same_target(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    first = store.submit_item(
        kind="start",
        workspace_root=str(tmp_path / "workspace"),
        message="first",
        case_id="collaboration",
        target_session_id="sess-1",
    )
    second = store.submit_item(
        kind="start",
        workspace_root=str(tmp_path / "workspace"),
        message="second",
        case_id="collaboration",
        target_session_id="sess-1",
    )

    [claimed] = store.claim_items(owner_id="owner-1", lease_seconds=30)
    assert claimed.item_id == first.item_id
    assert store.claim_items(owner_id="owner-1", lease_seconds=30) == []
    assert store.get_item(second.item_id).state == "queued"

    store.finish_item(first.item_id, owner_id="owner-1", state="passed")
    [next_claimed] = store.claim_items(owner_id="owner-1", lease_seconds=30)
    assert next_claimed.item_id == second.item_id


def test_submit_item_is_idempotent_per_submission_id(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    values = {
        "kind": "start",
        "workspace_root": str(tmp_path / "workspace"),
        "message": "begin work",
        "case_id": "collaboration",
        "source_session_id": "sess-src",
    }

    first = store.submit_item(**values, submission_id="submission-1")
    retry = store.submit_item(**values, submission_id="submission-1")
    assert retry.item_id == first.item_id
    assert retry.submission_id == "submission-1"
    assert retry.fingerprint

    with pytest.raises(ValueError, match="different request"):
        store.submit_item(
            **{**values, "message": "changed"}, submission_id="submission-1"
        )

    assert len(_all_items(store)) == 1


def test_task_delete_cascades_items(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _create_task(store, tmp_path)
    item = store.request_run(task.task_id)

    assert store.delete(task.task_id) is True
    with pytest.raises(KeyError):
        store.get_item(item.item_id)
    assert store.claim_items(owner_id="owner-1", lease_seconds=30) == []


def test_automation_trigger_message_shape(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _create_task(store, tmp_path, name="检查 <main>", prompt="Inspect <unsafe>")
    item = store.request_run(task.task_id)

    message = automation_trigger_message(task, item)

    assert message.role == "user"
    assert message.kind == AUTOMATION_MESSAGE_KIND
    data = plain_object(message.data)
    assert data["origin"] == "automation"
    assert data["automation_id"] == task.task_id
    assert data["automation_run_id"] == item.item_id
    assert "<automation-trigger" in message.content
    assert f'run-id="{item.item_id}"' in message.content
    assert "Inspect &lt;unsafe&gt;" in message.content


def test_service_validates_workspace_and_heartbeat_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _make_store(tmp_path)
    service = DispatchService(store)

    with pytest.raises(FileNotFoundError, match="workspace does not exist"):
        service.create(
            name="daily",
            prompt="Inspect",
            workspace_root=str(tmp_path / "missing"),
            destination="standalone",
            timezone_name="UTC",
            at_time=_future(),
        )
    with pytest.raises(FileNotFoundError):
        service.create(
            name="follow up",
            prompt="Continue",
            workspace_root=str(workspace),
            destination="heartbeat",
            target_session_id="missing",
            timezone_name="UTC",
            at_time=_future(),
        )
    task = service.create(
        name="daily",
        prompt="Inspect",
        workspace_root=str(workspace),
        destination="standalone",
        timezone_name="UTC",
        at_time=_future(),
    )
    assert service.pause(task.task_id).status == "paused"
    assert service.resume(task.task_id).status == "active"


_LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS automations(
    automation_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    destination TEXT NOT NULL,
    target_session_id TEXT,
    timezone TEXT NOT NULL,
    at_time TEXT,
    rrule TEXT,
    dtstart TEXT NOT NULL,
    status TEXT NOT NULL,
    next_run_at TEXT,
    last_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS automation_runs(
    run_id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL REFERENCES automations(automation_id) ON DELETE CASCADE,
    scheduled_for TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    session_id TEXT,
    case_run_id TEXT,
    execution_id TEXT,
    final_answer TEXT NOT NULL DEFAULT '',
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    claim_owner TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL
);
"""


def _build_legacy_store(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Create a legacy automations-v1 store with the exact migration inputs:
    an active rrule task, a completed one-shot task, a heartbeat task, and
    runs in passed/skipped/error/running states (insertion order matters:
    the migration maps each task to its last run)."""
    legacy_path = tmp_path / "legacy" / "automations-v1.sqlite3"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    workspace = str(tmp_path / "workspace")
    connection = sqlite3.connect(legacy_path)
    try:
        connection.executescript(_LEGACY_SCHEMA)
        connection.executemany(
            "INSERT INTO automations(automation_id, name, prompt, workspace_root,"
            " destination, target_session_id, timezone, at_time, rrule, dtstart,"
            " status, next_run_at, last_run_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
            [
                (
                    "auto-rrule-0001",
                    "hourly",
                    "Inspect",
                    workspace,
                    "standalone",
                    None,
                    "UTC",
                    None,
                    "FREQ=HOURLY",
                    now,
                    "active",
                    now,
                    now,
                ),
                (
                    "auto-once-0001",
                    "once",
                    "Finish",
                    workspace,
                    "standalone",
                    None,
                    "UTC",
                    _past(),
                    None,
                    now,
                    "completed",
                    now,
                    now,
                ),
                (
                    "auto-pulse-0001",
                    "pulse",
                    "Ping",
                    workspace,
                    "heartbeat",
                    "sess-1",
                    "America/New_York",
                    None,
                    "FREQ=DAILY",
                    now,
                    "active",
                    now,
                    now,
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO automation_runs(run_id, automation_id, scheduled_for,"
            " trigger, status, execution_id, case_run_id, final_answer, error,"
            " created_at, finished_at) VALUES (?, ?, ?, 'manual', ?, NULL, NULL,"
            " ?, ?, ?, ?)",
            [
                ("run-passed-0001", "auto-rrule-0001", now, "passed", "ok", None, now, now),
                ("run-skipped-0001", "auto-rrule-0001", now, "skipped", "", None, now, now),
                ("run-error-0001", "auto-once-0001", _past(), "error", "", "boom", now, now),
                ("run-running-0001", "auto-rrule-0001", now, "running", "", None, now, None),
            ],
        )
        connection.commit()
    finally:
        connection.close()
    return legacy_path, {
        "rrule": "auto-rrule-0001",
        "completed": "auto-once-0001",
        "heartbeat": "auto-pulse-0001",
        "run_passed": "run-passed-0001",
        "run_skipped": "run-skipped-0001",
        "run_running": "run-running-0001",
        "run_error": "run-error-0001",
    }


def test_migrate_legacy_automations_is_idempotent(tmp_path: Path) -> None:
    legacy_path, ids = _build_legacy_store(tmp_path)
    dispatch_path = tmp_path / "dispatch" / "dispatch-v1.sqlite3"
    store = DispatchStore(dispatch_path, legacy_path=legacy_path)

    tasks = {task.task_id: task for task in store.list()}
    assert set(tasks) == {ids["rrule"], ids["completed"], ids["heartbeat"]}
    rrule_task = tasks[ids["rrule"]]
    assert rrule_task.rrule == "FREQ=HOURLY"
    assert rrule_task.status == "active"
    assert rrule_task.last_run_id == ids["run_running"]
    assert tasks[ids["completed"]].status == "completed"
    assert tasks[ids["completed"]].last_run_id == ids["run_error"]
    heartbeat_task = tasks[ids["heartbeat"]]
    assert heartbeat_task.timezone_name == "America/New_York"
    assert heartbeat_task.target_session_id == "sess-1"
    assert heartbeat_task.last_run_id is None

    items = {item.item_id: item for item in _all_items(store)}
    assert set(items) == {
        ids["run_passed"],
        ids["run_skipped"],
        ids["run_running"],
        ids["run_error"],
    }
    passed = items[ids["run_passed"]]
    assert passed.kind == "turn"
    assert passed.state == "passed"
    assert passed.final_answer == "ok"
    assert passed.case_id == "automation"
    assert passed.agent_alias == ""
    assert passed.message == "Inspect"
    assert passed.workspace_root == str(tmp_path / "workspace")
    assert passed.task_id == ids["rrule"]
    assert items[ids["run_skipped"]].state == "skipped"
    assert items[ids["run_error"]].state == "error"
    assert items[ids["run_error"]].error == "boom"
    # A legacy running run is requeued: it has no owner in the new store.
    requeued = items[ids["run_running"]]
    assert requeued.state == "queued"
    assert requeued.owner_id is None
    assert requeued.lease_until_epoch is None

    # Re-running the migration (new instance or explicit call) changes nothing.
    second = DispatchStore(dispatch_path, legacy_path=legacy_path)
    assert second.migrate_legacy_automations() is False
    assert store.migrate_legacy_automations() is False
    assert len(store.list()) == 3
    assert len(_all_items(store)) == 4

    with store._connect() as connection:
        marker = connection.execute(
            "SELECT value FROM dispatch_meta WHERE key='legacy_automations_migrated_at'"
        ).fetchone()
    assert marker is not None


def test_migrate_is_skipped_without_legacy_store(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    assert store.migrate_legacy_automations() is False
    assert store.list() == []
    with store._connect() as connection:
        marker = connection.execute(
            "SELECT value FROM dispatch_meta WHERE key='legacy_automations_migrated_at'"
        ).fetchone()
    assert marker is None


def _all_items(store: DispatchStore):
    rows: list = []
    with store._connect() as connection:
        for row in connection.execute("SELECT item_id FROM dispatch_items"):
            rows.append(store.get_item(str(row["item_id"])))
    return rows
