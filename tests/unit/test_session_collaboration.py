from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from lara.dispatch import DispatchOperationView, DispatchStore
from lara.orchestration import SessionCollaborationService
from lara.orchestration.session_collaboration import _fingerprint
from lara.schema import RunConfig
from lara.sessions import (
    AgentMessageState,
    CollaborationOperation,
    CollaborationState,
    SessionCollaborationStore,
    SessionDirectory,
    SessionManager,
)


def _setup(tmp_path):
    config = RunConfig(workspace_root=str(tmp_path), lara_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    source = manager.create("chat", mode="chat").session_id
    target = manager.create("chat", mode="chat").session_id
    return config, manager, source, target


def test_store_binds_idempotency_key_to_request_fingerprint(tmp_path) -> None:
    store = SessionCollaborationStore(tmp_path / ".lara")
    values = {
        "submission_id": "submission-1",
        "fingerprint": "same",
        "source_session_id": None,
        "target_session_id": "target-1",
        "agent_alias": "default",
        "case_id": "collaboration",
        "message": "hello",
    }

    first = store.reserve(**values)
    second = store.reserve(**values)

    assert second.operation_id == first.operation_id
    with pytest.raises(ValueError, match="different request"):
        store.reserve(**{**values, "fingerprint": "different"})


def test_store_persists_model_group_and_binds_it_to_the_fingerprint(tmp_path) -> None:
    store = SessionCollaborationStore(tmp_path / ".lara")
    values = {
        "submission_id": "submission-1",
        "fingerprint": "same",
        "source_session_id": None,
        "target_session_id": "target-1",
        "agent_alias": "default",
        "case_id": "collaboration",
        "message": "hello",
        "model_group": "coding",
    }

    first = store.reserve(**values)
    second = store.reserve(**values)

    assert first.model_group == "coding"
    assert second.model_group == "coding"
    assert second.operation_id == first.operation_id
    with pytest.raises(ValueError, match="different request"):
        store.reserve(**{**values, "fingerprint": "different"})


def test_request_fingerprint_distinguishes_model_groups() -> None:
    base = _fingerprint(intent="start", message="task", model_group="coding")
    other = _fingerprint(intent="start", message="task", model_group="backup")

    assert base != other
    assert base == _fingerprint(intent="start", message="task", model_group="coding")


def test_store_adds_model_group_to_databases_created_before_it(tmp_path) -> None:
    database_path = tmp_path / ".lara" / "runtime" / "session-collaboration-v1.sqlite3"
    database_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE collaboration_operations (
            operation_id TEXT PRIMARY KEY,
            submission_id TEXT NOT NULL UNIQUE,
            fingerprint TEXT NOT NULL,
            source_session_id TEXT,
            target_session_id TEXT,
            agent_alias TEXT NOT NULL,
            case_id TEXT NOT NULL,
            message TEXT NOT NULL,
            state TEXT NOT NULL,
            execution_id TEXT,
            case_run_id TEXT,
            final_answer TEXT NOT NULL DEFAULT '',
            error TEXT,
            owner_id TEXT,
            lease_until_epoch REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()

    store = SessionCollaborationStore(tmp_path / ".lara")
    operation = store.reserve(
        submission_id="legacy",
        fingerprint="legacy",
        source_session_id=None,
        target_session_id="target-legacy",
        agent_alias="default",
        case_id="collaboration",
        message="hello",
        model_group="coding",
    )

    assert operation.model_group == "coding"


def test_store_lists_only_operations_related_to_the_session(tmp_path) -> None:
    store = SessionCollaborationStore(tmp_path / ".lara")
    first = store.reserve(
        submission_id="first",
        fingerprint="first",
        source_session_id="parent",
        target_session_id="first-child",
        agent_alias="default",
        case_id="collaboration",
        message="first",
    )
    second = store.reserve(
        submission_id="second",
        fingerprint="second",
        source_session_id="other-parent",
        target_session_id="parent",
        agent_alias="default",
        case_id="collaboration",
        message="second",
    )
    store.reserve(
        submission_id="unrelated",
        fingerprint="unrelated",
        source_session_id="other-parent",
        target_session_id="other-child",
        agent_alias="default",
        case_id="collaboration",
        message="unrelated",
    )

    assert [item.operation_id for item in store.list_operations("parent")] == [
        second.operation_id,
        first.operation_id,
    ]


@pytest.mark.asyncio
async def test_send_queues_persistent_message_without_starting_a_turn(tmp_path) -> None:
    config, manager, source, target = _setup(tmp_path)
    service = SessionCollaborationService(poll_interval=0.01)

    first = await service.send(
        config=config,
        manager=manager,
        source_session_id=source,
        target_session_id=target,
        message="please inspect this",
        submission_id="submission-1",
    )
    second = await service.send(
        config=config,
        manager=manager,
        source_session_id=source,
        target_session_id=target,
        message="please inspect this",
        submission_id="submission-1",
    )
    assert second.message_id == first.message_id
    assert first.state is AgentMessageState.QUEUED
    persisted = await service.message_status(config=config, message_id=first.message_id)
    assert persisted.content == "please inspect this"
    assert persisted.source_session_id == source
    await service.aclose()


@pytest.mark.asyncio
async def test_agent_messages_are_claimed_fifo_and_acknowledged(tmp_path) -> None:
    config, manager, source, target = _setup(tmp_path)
    service = SessionCollaborationService(poll_interval=0.01)

    first = await service.send(
        config=config,
        manager=manager,
        source_session_id=source,
        target_session_id=target,
        message="first",
        submission_id="submission-first",
    )
    second = await service.send(
        config=config,
        manager=manager,
        source_session_id=source,
        target_session_id=target,
        message="second",
        submission_id="submission-second",
    )
    store = SessionCollaborationStore(config.lara_root)
    claimed = store.claim_messages(target, claim_id="delivery-1", lease_seconds=30)
    assert [item.message_id for item in claimed] == [
        first.message_id,
        second.message_id,
    ]

    store.acknowledge_message(
        first.message_id,
        claim_id="delivery-1",
        execution_id="execution-1",
    )
    store.release_message(second.message_id, claim_id="delivery-1")

    assert store.get_message(first.message_id).state is AgentMessageState.DELIVERED
    assert store.get_message(second.message_id).state is AgentMessageState.QUEUED
    await service.aclose()


def _dispatch_store(tmp_path: Path) -> DispatchStore:
    return DispatchStore(tmp_path / "dispatch-v1.sqlite3")


@pytest.mark.asyncio
async def test_status_prefers_the_dispatch_store(tmp_path) -> None:
    config, _manager, source, _target = _setup(tmp_path)
    service = SessionCollaborationService(poll_interval=0.01)
    item = _dispatch_store(tmp_path).submit_item(
        kind="start",
        workspace_root=str(tmp_path),
        message="dispatched work",
        case_id="collaboration",
        source_session_id=source,
        agent_alias="default",
        submission_id="submission-dispatch",
    )

    view = await service.status(config=config, operation_id=item.item_id)

    assert isinstance(view, DispatchOperationView)
    assert view.operation_id == item.item_id
    assert view.state is item.state
    assert view.done is False
    # The legacy project table is still consulted as a fallback.
    legacy_store = SessionCollaborationStore(config.lara_root)
    legacy = legacy_store.reserve(
        submission_id="submission-legacy",
        fingerprint="submission-legacy",
        source_session_id=source,
        target_session_id="target-legacy",
        agent_alias="default",
        case_id="collaboration",
        message="legacy work",
    )
    routed = await service.status(config=config, operation_id=legacy.operation_id)
    assert isinstance(routed, CollaborationOperation)
    with pytest.raises(FileNotFoundError):
        await service.status(config=config, operation_id=f"op-{'a' * 32}")
    await service.aclose()


@pytest.mark.asyncio
async def test_wait_any_returns_when_the_first_dispatch_item_finishes(tmp_path) -> None:
    config, _manager, source, _target = _setup(tmp_path)
    service = SessionCollaborationService(poll_interval=0.01)
    store = _dispatch_store(tmp_path)
    first = store.submit_item(
        kind="start",
        workspace_root=str(tmp_path),
        message="first",
        case_id="collaboration",
        source_session_id=source,
        submission_id="submission-first",
    )
    second = store.submit_item(
        kind="start",
        workspace_root=str(tmp_path),
        message="second",
        case_id="collaboration",
        source_session_id=source,
        submission_id="submission-second",
    )
    [claimed_first, _claimed_second] = store.claim_items(
        owner_id="scheduler", lease_seconds=30
    )
    store.finish_item(claimed_first.item_id, owner_id="scheduler", state="passed")

    items, timed_out = await service.wait_any(
        config=config,
        collaboration_ids=(first.item_id, second.item_id),
        timeout=2,
    )

    assert timed_out is False
    operations = cast(
        "list[CollaborationOperation | DispatchOperationView]", list(items)
    )
    finished = [item for item in operations if item.done]
    assert len(finished) == 1
    assert finished[0].operation_id == claimed_first.item_id
    await service.aclose()


@pytest.mark.asyncio
async def test_wait_any_timeout_returns_current_snapshots(tmp_path) -> None:
    config, _manager, source, _target = _setup(tmp_path)
    service = SessionCollaborationService(poll_interval=0.01)
    item = _dispatch_store(tmp_path).submit_item(
        kind="start",
        workspace_root=str(tmp_path),
        message="long task",
        case_id="collaboration",
        source_session_id=source,
        submission_id="submission-long",
    )

    items, timed_out = await service.wait_any(
        config=config,
        collaboration_ids=(item.item_id,),
        timeout=0.02,
    )

    assert timed_out is True
    assert len(items) == 1
    assert not items[0].done
    await service.aclose()


@pytest.mark.asyncio
async def test_list_operations_includes_dispatched_items(tmp_path) -> None:
    config, _manager, source, _target = _setup(tmp_path)
    service = SessionCollaborationService(poll_interval=0.01)
    item = _dispatch_store(tmp_path).submit_item(
        kind="start",
        workspace_root=str(tmp_path),
        message="dispatched",
        case_id="collaboration",
        source_session_id=source,
        submission_id="submission-dispatch",
    )
    SessionCollaborationStore(config.lara_root).reserve(
        submission_id="submission-legacy",
        fingerprint="submission-legacy",
        source_session_id=source,
        target_session_id="target-legacy",
        agent_alias="default",
        case_id="collaboration",
        message="legacy",
    )

    operations = cast(
        "list[CollaborationOperation | DispatchOperationView]",
        await service.list_operations(config=config, session_id=source),
    )

    assert [op.operation_id for op in operations] == [
        item.item_id,
        operations[1].operation_id,
    ]
    assert isinstance(operations[0], DispatchOperationView)
    assert isinstance(operations[1], CollaborationOperation)
    await service.aclose()


def _cross_project_setup(tmp_path):
    user_root = tmp_path / "home"
    config_a = RunConfig(
        workspace_root=str(tmp_path / "workspace-a"),
        lara_root=str(user_root / "projects" / "a"),
    )
    config_b = RunConfig(
        workspace_root=str(tmp_path / "workspace-b"),
        lara_root=str(user_root / "projects" / "b"),
    )
    manager_a = SessionManager(config_a)
    manager_b = SessionManager(config_b)
    source = manager_a.create("chat", mode="chat").session_id
    target = manager_b.create("chat", mode="chat").session_id
    return user_root, config_a, config_b, manager_a, manager_b, source, target


@pytest.mark.asyncio
async def test_send_delivers_cross_project_messages_into_the_target_store(
    tmp_path,
) -> None:
    (
        user_root,
        config_a,
        _config_b,
        manager_a,
        _manager_b,
        source,
        target,
    ) = _cross_project_setup(tmp_path)
    service = SessionCollaborationService(poll_interval=0.01)

    queued = await service.send(
        config=config_a,
        manager=manager_a,
        source_session_id=source,
        target_session_id=target,
        message="cross project hello",
        submission_id="submission-cross",
    )

    store_b = SessionCollaborationStore(user_root / "projects" / "b")
    claimed = store_b.claim_messages(target, claim_id="claim-b", lease_seconds=30.0)
    assert [message.message_id for message in claimed] == [queued.message_id]
    located = SessionDirectory(user_root).locate_session(target)
    assert located is not None
    assert located.lara_root == str((user_root / "projects" / "b").resolve())
    await service.aclose()


@pytest.mark.asyncio
async def test_status_routes_cross_project_items_through_the_directory(tmp_path) -> None:
    (
        user_root,
        config_a,
        config_b,
        manager_a,
        _manager_b,
        source,
        _target,
    ) = _cross_project_setup(tmp_path)
    service_b = SessionCollaborationService(poll_interval=0.01)

    # A legacy operation registered in the user-level directory stays reachable
    # from any project through the fallback route.
    store_a = SessionCollaborationStore(config_a.lara_root)
    operation = store_a.reserve(
        submission_id="submission-op",
        fingerprint="submission-op",
        source_session_id=source,
        target_session_id=None,
        agent_alias=config_a.agent_alias,
        case_id="collaboration",
        message="start work",
    )
    SessionDirectory(user_root).register_item(
        item_id=operation.operation_id,
        kind="operation",
        lara_root=str(config_a.lara_root),
        source_session_id=source,
        target_session_id=None,
    )

    routed = await service_b.status(config=config_b, operation_id=operation.operation_id)
    assert routed.operation_id == operation.operation_id
    with pytest.raises(FileNotFoundError):
        await service_b.status(config=config_b, operation_id=f"op-{'a' * 32}")
    await service_b.aclose()


@pytest.mark.asyncio
async def test_send_fails_closed_for_unregistered_sessions(tmp_path) -> None:
    config, manager, source, _target = _setup(tmp_path)
    service = SessionCollaborationService(poll_interval=0.01)

    with pytest.raises(FileNotFoundError):
        await service.send(
            config=config,
            manager=manager,
            source_session_id=source,
            target_session_id="chat-chat-20260101-000000-aaaaaaaa",
            message="hello",
        )

    await service.aclose()


async def _eventually(predicate, *, timeout: float = 1.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout)
