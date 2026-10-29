from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lara.core.io import plain_data, plain_object
from lara.schema import RunConfig
from lara.sessions import SessionCollaborationStore, SessionManager

from .messages import automation_trigger_message
from .models import (
    DispatchDestination,
    DispatchItem,
    DispatchItemKind,
    DispatchItemState,
    DispatchTask,
    next_occurrence,
    utc_now,
)
from .store import DispatchStore

if TYPE_CHECKING:
    from lara.orchestration import SessionExecutionCoordinator

ConfigFactory = Callable[[str, str | None], RunConfig]
RuntimeAcquirer = Callable[..., Awaitable[Any]]

_RESULT_TERMINAL_STATES = {"passed", "failed", "error", "skipped"}


def _terminal_state(status: str) -> DispatchItemState:
    try:
        state = DispatchItemState(status)
    except ValueError:
        return DispatchItemState.ERROR
    return state if state.terminal else DispatchItemState.ERROR


class DispatchScheduler:
    """Materialize and execute durable dispatch items while the app is alive.

    Scheduled turns, background starts, and forks share one polling loop and
    one lease protocol: claim items, heartbeat the lease while a turn runs,
    and write the terminal state back before releasing the slot.
    """

    def __init__(
        self,
        *,
        store: DispatchStore,
        coordinator: SessionExecutionCoordinator,
        acquire_runtime: RuntimeAcquirer,
        config_factory: ConfigFactory,
        poll_seconds: float = 1.0,
        lease_seconds: float = 30.0,
        max_parallel: int = 4,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.store = store
        self.coordinator = coordinator
        self.acquire_runtime = acquire_runtime
        self.config_factory = config_factory
        self.poll_seconds = poll_seconds
        self.lease_seconds = float(lease_seconds)
        self.owner = f"scheduler-{uuid.uuid4().hex}"
        self._max_parallel = max_parallel
        self._slots = asyncio.Semaphore(max_parallel)
        self._loop_task: asyncio.Task[None] | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._turns: dict[str, Any] = {}
        self._cancel_requested: set[str] = set()
        self._closed = False

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("dispatch scheduler is closed")
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(
                self._run_loop(), name="lara-dispatch-scheduler"
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None
        for task in tuple(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def poll_once(self) -> int:
        available = max(0, self._max_parallel - len(self._tasks))
        if available == 0:
            return 0
        await asyncio.to_thread(self._materialize_due_tasks)
        items = await asyncio.to_thread(
            self.store.claim_items,
            owner_id=self.owner,
            limit=available,
            lease_seconds=self.lease_seconds,
        )
        for item in items:
            task = asyncio.create_task(
                self._execute_guarded(item), name=f"dispatch-item:{item.item_id}"
            )
            self._tasks[item.item_id] = task
            task.add_done_callback(
                lambda completed, item_id=item.item_id: self._tasks.pop(item_id, None)
            )
        return len(items)

    async def cancel_run(self, item_id: str) -> bool:
        """Cancel one in-flight item; returns False when it is not active here.

        The live turn is cancelled first so the durable execution stops and the
        session lane is released, then the scheduler task unwinds through the
        same finalizer a scheduler shutdown uses.
        """

        task = self._tasks.get(item_id)
        if task is None or task.done():
            return False
        self._cancel_requested.add(item_id)
        turn = self._turns.get(item_id)
        if turn is not None:
            await turn.cancel()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # A task cancelled before its coroutine received a first timeslice never
        # reaches the finalizer, so the item would stay active until its lease
        # expires.
        item = await asyncio.to_thread(self.store.get_item, item_id)
        if item.state in (DispatchItemState.CLAIMED, DispatchItemState.RUNNING):
            await asyncio.to_thread(
                self.store.finish_item,
                item_id,
                owner_id=self.owner,
                state=DispatchItemState.CANCELLED.value,
                error="cancelled before the run started",
            )
        return True

    async def _run_loop(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(self.poll_seconds)

    def _materialize_due_tasks(self) -> None:
        now = utc_now()
        for task in self.store.list(status="active"):
            occurrence = self._due_occurrence(task, now)
            if occurrence is None:
                continue
            item = self.store.submit_item(
                kind=DispatchItemKind.TURN.value,
                workspace_root=task.workspace_root,
                message=task.prompt,
                task_id=task.task_id,
                target_session_id=task.target_session_id,
                # The unique submission key makes racing schedulers materialize
                # one item per occurrence.
                submission_id=f"schedule:{task.task_id}:{occurrence}",
            )
            completed = task.rrule is None or (
                next_occurrence(
                    rrule=task.rrule,
                    timezone_name=task.timezone_name,
                    dtstart=task.created_at,
                    after=now,
                )
                is None
            )
            self.store.record_task_run(task.task_id, item.item_id, completed=completed)

    def _due_occurrence(self, task: DispatchTask, now: str) -> str | None:
        if task.at_time:
            if task.at_time > now or task.last_run_id is not None:
                return None
            return task.at_time
        if task.rrule is None:
            return None
        after = task.created_at
        if task.last_run_id:
            try:
                after = self.store.get_item(task.last_run_id).created_at
            except KeyError:
                pass
        occurrence = next_occurrence(
            rrule=task.rrule,
            timezone_name=task.timezone_name,
            dtstart=task.created_at,
            after=after,
        )
        if occurrence is None or occurrence > now:
            return None
        return occurrence

    async def _execute_guarded(self, item: DispatchItem) -> None:
        async with self._slots:
            if item.kind == DispatchItemKind.TURN.value:
                await self._execute_turn(item)
            else:
                await self._execute_start(item)

    async def _heartbeat_loop(self, item_id: str) -> None:
        interval = min(self.lease_seconds / 3, 10.0)
        while True:
            await asyncio.sleep(interval)
            renewed = await asyncio.to_thread(
                self.store.heartbeat,
                item_id,
                owner_id=self.owner,
                lease_seconds=self.lease_seconds,
            )
            if not renewed:
                raise RuntimeError("dispatch item lease was lost")

    async def _execute_turn(self, item: DispatchItem) -> None:
        assert item.task_id is not None
        task = await asyncio.to_thread(self.store.get, item.task_id)
        case_run_id: str | None = None
        execution_id: str | None = None
        heartbeat: asyncio.Task[None] | None = None
        try:
            workspace = Path(item.workspace_root)
            if not workspace.is_dir():
                raise FileNotFoundError(f"workspace does not exist: {workspace}")
            config = self.config_factory(item.workspace_root, item.agent_alias or None)
            manager = SessionManager(config)
            if task.destination == DispatchDestination.HEARTBEAT.value:
                session_id = item.target_session_id
                assert session_id is not None
                manager.load(session_id)
            else:
                session_id = manager.create("automation", mode="chat").session_id
                await asyncio.to_thread(
                    self.store.attach_target,
                    item.item_id,
                    session_id,
                    owner_id=self.owner,
                )
            message = automation_trigger_message(task, item)
            data = plain_data(message.data)
            from lara.orchestration import SessionTurnService

            turn_service = SessionTurnService(
                coordinator=self.coordinator,
                acquire_runtime=self.acquire_runtime,
            )
            turn = await turn_service.submit(
                config=config,
                manager=manager,
                session_id=session_id,
                message=message.content,
                case_id="automation",
                submission_id=item.item_id,
                interactive_approvals=False,
                message_kind=message.kind or "lara.automation.trigger",
                message_data=dict(data) if isinstance(data, dict) else {},
                session_title=(
                    f"定时任务：{task.name}"
                    if task.destination == DispatchDestination.STANDALONE.value
                    else None
                ),
            )
            self._turns[item.item_id] = turn
            heartbeat = asyncio.create_task(
                self._heartbeat_loop(item.item_id),
                name=f"dispatch-heartbeat:{item.item_id}",
            )
            await turn.wait_ready()
            if turn.run_ref is not None:
                case_run_id = turn.run_ref.case_run_id
            if turn.execution_handle is not None:
                execution_id = turn.execution_handle.execution_id
            await asyncio.to_thread(
                self.store.mark_running,
                item.item_id,
                owner_id=self.owner,
                execution_id=execution_id,
                case_run_id=case_run_id,
                lease_seconds=self.lease_seconds,
            )
            result_task = asyncio.create_task(
                turn.result(), name=f"dispatch-result:{item.item_id}"
            )
            done, _pending = await asyncio.wait(
                (result_task, heartbeat), return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                heartbeat.result()
                raise RuntimeError("dispatch item lease was lost")
            output, _ = result_task.result()
            if item.item_id in self._cancel_requested:
                # The item was cancelled through cancel_run(); route through the
                # cancellation finalizer so it is recorded exactly once.
                raise asyncio.CancelledError
            result = plain_object(plain_object(output.data).get("result"))
            status = str(result.get("status") or DispatchItemState.PASSED.value)
            if status not in _RESULT_TERMINAL_STATES:
                status = DispatchItemState.ERROR.value
            await asyncio.to_thread(
                self.store.finish_item,
                item.item_id,
                owner_id=self.owner,
                state=status,
                final_answer=str(result.get("final_answer") or ""),
                error=(str(result["error"]) if result.get("error") else None),
                execution_id=execution_id,
                case_run_id=case_run_id,
            )
        except asyncio.CancelledError:
            # A normal completion may already have finalized the item when the
            # cancel raced it; only an item still owned here is stamped.
            current = await asyncio.to_thread(self.store.get_item, item.item_id)
            if (
                current.state in (DispatchItemState.CLAIMED, DispatchItemState.RUNNING)
                and current.owner_id == self.owner
            ):
                cancelled_by_user = item.item_id in self._cancel_requested
                await asyncio.to_thread(
                    self.store.finish_item,
                    item.item_id,
                    owner_id=self.owner,
                    state=(
                        DispatchItemState.CANCELLED.value
                        if cancelled_by_user
                        else DispatchItemState.ERROR.value
                    ),
                    execution_id=execution_id,
                    case_run_id=case_run_id,
                    error=(
                        "cancelled by user"
                        if cancelled_by_user
                        else "automation scheduler stopped before completion"
                    ),
                )
            raise
        except Exception as exc:  # noqa: BLE001 - run failures are persisted, not leaked
            missing_target = isinstance(exc, FileNotFoundError)
            await self._finish_safely(
                item.item_id,
                state=(
                    DispatchItemState.FAILED
                    if missing_target
                    else DispatchItemState.ERROR
                ),
                error=str(exc),
                execution_id=execution_id,
                case_run_id=case_run_id,
            )
            if missing_target:
                assert item.task_id is not None
                await asyncio.to_thread(self.store.pause_after_failure, item.task_id)
        finally:
            self._turns.pop(item.item_id, None)
            self._cancel_requested.discard(item.item_id)
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def _execute_start(self, item: DispatchItem) -> None:
        config = self.config_factory(item.workspace_root, item.agent_alias or None)
        manager = SessionManager(config)
        turn: Any | None = None
        heartbeat: asyncio.Task[None] | None = None
        execution_id: str | None = None
        case_run_id: str | None = None
        try:
            # kind=fork records the fork source in source_session_id.
            if item.kind == DispatchItemKind.FORK.value:
                assert item.source_session_id is not None
                target = manager.fork(item.source_session_id, mode="agent")
            else:
                target = manager.create(
                    item.case_id, mode="agent", model_group_name=item.model_group
                )
            await asyncio.to_thread(
                self.store.attach_target,
                item.item_id,
                target.session_id,
                owner_id=self.owner,
            )
            from lara.orchestration import SessionTurnService

            turn_service = SessionTurnService(
                coordinator=self.coordinator,
                acquire_runtime=self.acquire_runtime,
            )
            turn = await turn_service.submit(
                config=config,
                manager=manager,
                session_id=target.session_id,
                message=item.message,
                case_id=item.case_id,
                submission_id=item.item_id,
                interactive_approvals=False,
            )
            self._turns[item.item_id] = turn
            heartbeat = asyncio.create_task(
                self._heartbeat_loop(item.item_id),
                name=f"dispatch-heartbeat:{item.item_id}",
            )
            await turn.wait_ready()
            if turn.startup_error is not None:
                raise turn.startup_error
            if turn.execution_id is None or turn.run_ref is None:
                raise RuntimeError(
                    "dispatch item did not produce an execution identity"
                )
            execution_id = turn.execution_id
            case_run_id = turn.run_ref.case_run_id
            await asyncio.to_thread(
                self.store.mark_running,
                item.item_id,
                owner_id=self.owner,
                execution_id=execution_id,
                case_run_id=case_run_id,
                lease_seconds=self.lease_seconds,
            )
            result_task = asyncio.create_task(
                turn.result(), name=f"dispatch-result:{item.item_id}"
            )
            done, _pending = await asyncio.wait(
                (result_task, heartbeat), return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                heartbeat.result()
                raise RuntimeError("dispatch item lease was lost")
            output, _ = result_task.result()
            if item.item_id in self._cancel_requested:
                raise asyncio.CancelledError
            result = plain_object(plain_object(output.data).get("result"))
            status = _terminal_state(str(result.get("status") or turn.status))
            final_answer = str(result.get("final_answer") or output.content or "")
            error = None if not result.get("error") else str(result["error"])
            await asyncio.to_thread(
                self.store.finish_item,
                item.item_id,
                owner_id=self.owner,
                state=status,
                final_answer=final_answer,
                error=error,
                execution_id=execution_id,
                case_run_id=case_run_id,
            )
            await self._notify_completion(
                item,
                config,
                target_session_id=target.session_id,
                status=status,
                final_answer=final_answer,
                error=error,
                execution_id=execution_id,
                case_run_id=case_run_id,
            )
        except asyncio.CancelledError:
            if turn is not None:
                await turn.cancel()
            cancelled_by_user = item.item_id in self._cancel_requested
            await self._finish_safely(
                item.item_id,
                state=(
                    DispatchItemState.CANCELLED
                    if cancelled_by_user
                    else DispatchItemState.SKIPPED
                ),
                error="cancelled by user"
                if cancelled_by_user
                else "dispatch scheduler stopped",
                execution_id=execution_id,
                case_run_id=case_run_id,
            )
            raise
        except BaseException as exc:
            if turn is not None:
                await turn.cancel()
            await self._finish_safely(
                item.item_id,
                state=DispatchItemState.ERROR,
                error=str(exc),
                execution_id=execution_id,
                case_run_id=case_run_id,
            )
        finally:
            self._turns.pop(item.item_id, None)
            self._cancel_requested.discard(item.item_id)
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def _notify_completion(
        self,
        item: DispatchItem,
        config: RunConfig,
        *,
        target_session_id: str,
        status: str,
        final_answer: str,
        error: str | None,
        execution_id: str | None,
        case_run_id: str | None,
    ) -> None:
        """Deliver the legacy worker's session.completed inbox notification."""
        if item.source_session_id is None:
            return
        content = json.dumps(
            {
                "type": "session.completed",
                "operation_id": item.item_id,
                "session_id": target_session_id,
                "case_id": item.case_id,
                "execution_id": execution_id,
                "case_run_id": case_run_id,
                "status": status,
                "final_answer": final_answer,
                "error": error,
            },
            ensure_ascii=False,
            indent=2,
        )
        try:
            await asyncio.to_thread(
                SessionCollaborationStore(config.lara_root).enqueue_message,
                submission_id=f"operation-completion:{item.item_id}",
                fingerprint=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                source_session_id=target_session_id,
                source_agent_alias=item.agent_alias,
                target_session_id=item.source_session_id,
                content=content,
            )
        except Exception:  # noqa: BLE001 - the item is terminal; notification is best effort
            pass

    async def _finish_safely(
        self,
        item_id: str,
        *,
        state: DispatchItemState,
        error: str,
        execution_id: str | None = None,
        case_run_id: str | None = None,
    ) -> None:
        try:
            await asyncio.to_thread(
                self.store.finish_item,
                item_id,
                owner_id=self.owner,
                state=state.value,
                error=error,
                execution_id=execution_id,
                case_run_id=case_run_id,
            )
        except (FileNotFoundError, RuntimeError):
            pass


__all__ = ["DispatchScheduler"]
