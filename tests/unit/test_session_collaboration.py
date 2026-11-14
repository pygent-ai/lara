from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lara.dispatch import DispatchOperationView, DispatchStore
from lara.orchestration import SessionCollaborationService
from lara.orchestration.session_collaboration import _fingerprint
from lara.schema import RunConfig
from lara.sessions import (
    AgentMessageState,
    SessionCollaborationStore,
    SessionDirectory,
    SessionManager,
)


def _setup(tmp_path):
    config = RunConfig(workspace_root=str(tmp_path), pygent_root=str(tmp_path / ".lara"))
    manager = SessionManager(config)
    source = manager.create("chat", mode="chat").session_id
    target = manager.create("chat", mode="chat").session_id
    return config, manager, source, target


def test_request_fingerprint_distinguishes_model_groups() -> None:
    base = _fingerprint(intent="start", message="task", model_group="coding")
    other = _fingerprint(intent="start", message="task", model_group="backup")

    assert base != other
    assert base == _fingerprint(intent="start", message="task", model_group="coding")


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
    store = SessionCollaborationStore(config.pygent_root)
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
    with pytest.raises(KeyError):
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
    finished = [item for item in items if item.done]
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

    operations = await service.list_operations(config=config, session_id=source)

    assert [op.operation_id for op in operations] == [item.item_id]
    assert isinstance(operations[0], DispatchOperationView)
    await service.aclose()


def _cross_project_setup(tmp_path):
    user_root = tmp_path / "home"
    config_a = RunConfig(
        workspace_root=str(tmp_path / "workspace-a"),
        pygent_root=str(user_root / "projects" / "a"),
    )
    config_b = RunConfig(
        workspace_root=str(tmp_path / "workspace-b"),
        pygent_root=str(user_root / "projects" / "b"),
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
    assert located.pygent_root == str((user_root / "projects" / "b").resolve())
    await service.aclose()


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
