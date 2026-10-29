from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from lara.dispatch import (
    AUTOMATION_MESSAGE_KIND,
    DispatchScheduler,
    DispatchStore,
)
from lara.schema import CaseRunRef, RunConfig
from lara.sessions import SessionManager


def _future(minutes: int = 10) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


async def _acquire_noop(**_kwargs: Any) -> Any:
    return SimpleNamespace(
        runtime=SimpleNamespace(
            reminders=SimpleNamespace(prewarm_session=lambda _session_id: None)
        ),
        release=_release_noop,
    )


async def _release_noop() -> None:
    return None


class _ConfigFactory:
    """Config factory stub that records the agent alias each call received."""

    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.captured: list[str | None] = []

    def __call__(self, workspace: str, agent_alias: str | None) -> RunConfig:
        self.captured.append(agent_alias)
        return self.config


class _Turn:
    """Turn stub for the automation turn path (run_ref + execution_handle)."""

    run_ref = SimpleNamespace(case_run_id="case-run-1")
    execution_handle = SimpleNamespace(execution_id="execution-1")

    def __init__(self, output: Any | None = None, *, hold: bool = False) -> None:
        self.output = output
        self.hold = hold
        self.finalized = asyncio.Event()
        self.cancelled = False

    async def wait_ready(self) -> None:
        return None

    async def cancel(self) -> bool:
        self.cancelled = True
        self.finalized.set()
        return True

    async def result(self):
        if self.hold:
            await self.finalized.wait()
        if self.output is None:
            return (None, None)
        return (self.output, SimpleNamespace())


class _CollabTurn:
    """Turn stub for the start/fork path (execution_id + run_ref attrs)."""

    def __init__(self, session_id: str) -> None:
        self.execution_id = "execution-1"
        self.run_ref = CaseRunRef(
            session_id=session_id,
            case_id="collaboration",
            case_run_id="run-1",
            run_dir="runs/1",
        )
        self.startup_error = None
        self.status = "passed"
        self.cancelled = False

    async def wait_ready(self) -> None:
        return None

    async def result(self):
        return (
            SimpleNamespace(
                content="answer",
                data={"result": {"status": "passed", "final_answer": "answer"}},
            ),
            None,
        )

    async def cancel(self) -> bool:
        self.cancelled = True
        return True


def _coordinator(turn_factory):
    class Coordinator:
        def __init__(self) -> None:
            self.turns: list[Any] = []
            self.commands: list[Any] = []

        async def submit_turn(self, *, lease, command):
            turn = turn_factory(command)
            self.turns.append(turn)
            self.commands.append(command)
            return turn

    return Coordinator


def _make_store(tmp_path: Path) -> DispatchStore:
    return DispatchStore(tmp_path / "dispatch.sqlite3")


@pytest.mark.asyncio
async def test_scheduler_submits_unattended_standalone_trigger(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = RunConfig(
        workspace_root=str(workspace), lara_root=str(workspace / ".lara")
    )
    store = _make_store(tmp_path)
    task = store.create(
        name="nightly",
        prompt="Inspect the build",
        workspace_root=str(workspace),
        destination="standalone",
        timezone_name="UTC",
        at_time=_future(),
    )
    requested = store.request_run(task.task_id)
    factory = _ConfigFactory(config)
    coordinator = _coordinator(
        lambda _command: _Turn(
            output=SimpleNamespace(
                data={"result": {"status": "passed", "final_answer": "done"}}
            )
        )
    )()

    scheduler = DispatchScheduler(
        store=store,
        coordinator=cast(Any, coordinator),
        acquire_runtime=cast(Any, _acquire_noop),
        config_factory=factory,
    )
    [item] = store.claim_items(owner_id=scheduler.owner, lease_seconds=scheduler.lease_seconds)
    await scheduler._execute_turn(item)

    command = coordinator.commands[0]
    # The '' alias sentinel on automation items resolves to the host default.
    assert factory.captured == [None]
    assert command.submission_id == requested.item_id
    assert command.message_kind == AUTOMATION_MESSAGE_KIND
    assert command.message_data["origin"] == "automation"
    assert command.interactive_approvals is False
    assert command.session_title == "定时任务：nightly"
    completed = store.get_item(item.item_id)
    assert completed.state == "passed"
    assert completed.final_answer == "done"
    assert completed.execution_id == "execution-1"
    assert completed.case_run_id == "case-run-1"
    assert completed.target_session_id == command.session_id
    assert SessionManager(config).show(command.session_id)["metadata"]["title"] == (
        "定时任务：nightly"
    )


@pytest.mark.asyncio
async def test_scheduler_cancel_stops_running_item(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = RunConfig(
        workspace_root=str(workspace), lara_root=str(workspace / ".lara")
    )
    store = _make_store(tmp_path)
    task = store.create(
        name="nightly",
        prompt="Inspect the build",
        workspace_root=str(workspace),
        destination="standalone",
        timezone_name="UTC",
        at_time=_future(),
    )
    requested = store.request_run(task.task_id)

    coordinator = _coordinator(lambda _command: _Turn(hold=True))()
    scheduler = DispatchScheduler(
        store=store,
        coordinator=cast(Any, coordinator),
        acquire_runtime=cast(Any, _acquire_noop),
        config_factory=_ConfigFactory(config),
        poll_seconds=0.01,
    )
    scheduler.start()
    try:
        for _ in range(200):
            if requested.item_id in scheduler._turns:
                break
            await asyncio.sleep(0.01)
        assert requested.item_id in scheduler._turns

        assert await scheduler.cancel_run(requested.item_id) is True

        completed = store.get_item(requested.item_id)
        assert completed.state == "cancelled"
        assert completed.error == "cancelled by user"
        assert completed.case_run_id == "case-run-1"
        assert coordinator.turns[0].cancelled is True
        assert requested.item_id not in scheduler._tasks
        # Cancelling a finished item again reports it is no longer active.
        assert await scheduler.cancel_run(requested.item_id) is False
    finally:
        await scheduler.close()


@pytest.mark.asyncio
async def test_scheduler_cancel_finalizes_never_started_item(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = RunConfig(
        workspace_root=str(workspace), lara_root=str(workspace / ".lara")
    )
    store = _make_store(tmp_path)
    task = store.create(
        name="nightly",
        prompt="Inspect the build",
        workspace_root=str(workspace),
        destination="standalone",
        timezone_name="UTC",
        at_time=_future(),
    )
    store.request_run(task.task_id)

    scheduler = DispatchScheduler(
        store=store,
        coordinator=cast(Any, SimpleNamespace()),
        acquire_runtime=cast(Any, _acquire_noop),
        config_factory=_ConfigFactory(config),
    )
    [item] = store.claim_items(owner_id=scheduler.owner, lease_seconds=scheduler.lease_seconds)
    started = asyncio.Event()

    async def slow_start() -> None:
        started.set()
        await asyncio.Event().wait()

    running = asyncio.create_task(slow_start())
    scheduler._tasks[item.item_id] = running

    # No await happens before cancel_run calls task.cancel(), so the coroutine
    # never receives a timeslice and its finalizer never runs.
    assert await scheduler.cancel_run(item.item_id) is True

    assert running.cancelled()
    cancelled = store.get_item(item.item_id)
    assert cancelled.state == "cancelled"
    assert cancelled.error == "cancelled before the run started"


def test_materializes_due_one_shot_task_once(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = RunConfig(
        workspace_root=str(workspace), lara_root=str(workspace / ".lara")
    )
    store = _make_store(tmp_path)
    task = store.create(
        name="once",
        prompt="Inspect",
        workspace_root=str(workspace),
        destination="standalone",
        timezone_name="UTC",
        at_time=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )
    scheduler = DispatchScheduler(
        store=store,
        coordinator=cast(Any, SimpleNamespace()),
        acquire_runtime=cast(Any, _acquire_noop),
        config_factory=_ConfigFactory(config),
    )

    scheduler._materialize_due_tasks()

    [item] = store.list_items(task.task_id)
    assert item.kind == "turn"
    assert item.state == "queued"
    assert item.submission_id == f"schedule:{task.task_id}:{task.at_time}"
    completed_task = store.get(task.task_id)
    assert completed_task.status == "completed"
    assert completed_task.last_run_id == item.item_id
    assert store.next_run_at(completed_task) is None

    scheduler._materialize_due_tasks()
    assert len(store.list_items(task.task_id)) == 1


def test_materializes_overdue_rrule_catch_up_once(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = RunConfig(
        workspace_root=str(workspace), lara_root=str(workspace / ".lara")
    )
    store = _make_store(tmp_path)
    task = store.create(
        name="hourly",
        prompt="Inspect",
        workspace_root=str(workspace),
        destination="standalone",
        timezone_name="UTC",
        rrule="FREQ=HOURLY",
    )
    scheduler = DispatchScheduler(
        store=store,
        coordinator=cast(Any, SimpleNamespace()),
        acquire_runtime=cast(Any, _acquire_noop),
        config_factory=_ConfigFactory(config),
    )

    # The task was created moments ago, so its first occurrence is in the future.
    scheduler._materialize_due_tasks()
    assert store.list_items(task.task_id) == []

    # A long scheduler downtime leaves one missed occurrence to catch up on.
    missed = (datetime.now(UTC) - timedelta(hours=12)).isoformat()
    with store._connect() as connection:
        connection.execute(
            "UPDATE dispatch_tasks SET created_at=? WHERE task_id=?",
            (missed, task.task_id),
        )
        connection.commit()
    scheduler._materialize_due_tasks()
    [item] = store.list_items(task.task_id)
    assert item.state == "queued"

    scheduler._materialize_due_tasks()
    assert len(store.list_items(task.task_id)) == 1
    active = store.get(task.task_id)
    assert active.status == "active"
    assert active.last_run_id == item.item_id
    assert store.next_run_at(active) is not None


@pytest.mark.asyncio
async def test_scheduler_executes_start_item_and_notifies_source(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = RunConfig(workspace_root=str(workspace), lara_root=str(tmp_path / ".lara"))
    store = _make_store(tmp_path)
    item = store.submit_item(
        kind="start",
        workspace_root=str(workspace),
        message="begin work",
        case_id="collaboration",
        source_session_id="sess-src",
        agent_alias="default",
        submission_id="submission-1",
    )
    factory = _ConfigFactory(config)
    coordinator = _coordinator(lambda command: _CollabTurn(str(command.session_id)))()

    scheduler = DispatchScheduler(
        store=store,
        coordinator=cast(Any, coordinator),
        acquire_runtime=cast(Any, _acquire_noop),
        config_factory=factory,
    )
    [claimed] = store.claim_items(owner_id=scheduler.owner, lease_seconds=scheduler.lease_seconds)
    await scheduler._execute_start(claimed)

    # The recorded agent alias is honored instead of the host default.
    assert factory.captured == ["default"]
    completed = store.get_item(item.item_id)
    assert completed.state == "passed"
    assert completed.final_answer == "answer"
    assert completed.execution_id == "execution-1"
    assert completed.case_run_id == "run-1"
    command = coordinator.commands[0]
    assert command.session_id == completed.target_session_id
    assert command.submission_id == item.item_id
    assert command.message == "begin work"
    assert command.case_id == "collaboration"
    assert command.interactive_approvals is False
    assert completed.target_session_id is not None
    SessionManager(config).load(completed.target_session_id)

    # Legacy worker parity: the source session receives a completion message.
    inbox = Path(config.lara_root) / "runtime" / "session-collaboration-v1.sqlite3"
    with sqlite3.connect(inbox) as connection:
        row = connection.execute(
            """SELECT content, source_session_id, target_session_id
               FROM agent_messages WHERE submission_id=?""",
            (f"operation-completion:{item.item_id}",),
        ).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    assert payload == {
        "type": "session.completed",
        "operation_id": item.item_id,
        "session_id": completed.target_session_id,
        "case_id": "collaboration",
        "execution_id": "execution-1",
        "case_run_id": "run-1",
        "status": "passed",
        "final_answer": "answer",
        "error": None,
    }
    assert row[1] == completed.target_session_id
    assert row[2] == "sess-src"


@pytest.mark.asyncio
async def test_scheduler_executes_fork_item(tmp_path: Path) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    source = manager.create("chat", mode="chat").session_id
    run = manager.start_case_run(source, "chat")
    manager.append_history_checkpoint(
        run,
        turn_id="turn-1",
        checkpoint_id="checkpoint-1",
        message={"role": "user", "content": "inherited question"},
    )
    manager.finish_case_run(run, "passed")
    store = _make_store(tmp_path)
    item = store.submit_item(
        kind="fork",
        workspace_root=str(tmp_path),
        message="follow-up",
        case_id="collaboration",
        source_session_id=source,
        agent_alias="default",
        submission_id="submission-fork",
    )
    coordinator = _coordinator(lambda command: _CollabTurn(str(command.session_id)))()

    scheduler = DispatchScheduler(
        store=store,
        coordinator=cast(Any, coordinator),
        acquire_runtime=cast(Any, _acquire_noop),
        config_factory=_ConfigFactory(config),
    )
    [claimed] = store.claim_items(owner_id=scheduler.owner, lease_seconds=scheduler.lease_seconds)
    await scheduler._execute_start(claimed)

    completed = store.get_item(item.item_id)
    assert completed.state == "passed"
    forked_id = completed.target_session_id
    assert forked_id is not None and forked_id != source
    forked = manager.load(forked_id)
    assert forked.metadata["forked_from"] == source
    assert forked.metadata["mode"] == "agent"
    assert forked.history[-1]["content"] == "inherited question"
