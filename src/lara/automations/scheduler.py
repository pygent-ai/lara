from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from lara.core.io import plain_data, plain_object
from lara.orchestration import SessionExecutionCoordinator, SessionTurnService
from lara.schema import RunConfig
from lara.sessions import SessionManager

from .messages import automation_trigger_message
from .models import AutomationDestination, AutomationRun, AutomationRunStatus
from .store import AutomationStore

ConfigFactory = Callable[[str], RunConfig]
RuntimeAcquirer = Callable[..., Awaitable[Any]]


class AutomationScheduler:
    """Materialize and execute durable automation runs while the app is alive."""

    def __init__(
        self,
        *,
        store: AutomationStore,
        coordinator: SessionExecutionCoordinator,
        acquire_runtime: RuntimeAcquirer,
        config_factory: ConfigFactory,
        poll_seconds: float = 1.0,
        max_parallel: int = 4,
    ) -> None:
        self.store = store
        self.coordinator = coordinator
        self.acquire_runtime = acquire_runtime
        self.config_factory = config_factory
        self.poll_seconds = poll_seconds
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
            raise RuntimeError("automation scheduler is closed")
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(
                self._run_loop(), name="lara-automation-scheduler"
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
        runs = await asyncio.to_thread(
            self.store.claim_ready_runs, owner=self.owner, limit=available
        )
        for run in runs:
            task = asyncio.create_task(
                self._execute_guarded(run), name=f"automation-run:{run.run_id}"
            )
            self._tasks[run.run_id] = task
            task.add_done_callback(
                lambda completed, run_id=run.run_id: self._tasks.pop(run_id, None)
            )
        return len(runs)

    async def cancel_run(self, run_id: str) -> bool:
        """Cancel one in-flight run; returns False when it is not active here.

        The live turn is cancelled first so the durable execution stops and the
        session lane is released, then the scheduler task unwinds through the
        same finalizer a scheduler shutdown uses.
        """

        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        self._cancel_requested.add(run_id)
        turn = self._turns.get(run_id)
        if turn is not None:
            await turn.cancel()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # A task cancelled before its coroutine received a first timeslice never
        # reaches the finalizer, so the run would stay "running" until its lease
        # expires.
        run = await asyncio.to_thread(self.store.get_run, run_id)
        if run.status == AutomationRunStatus.RUNNING.value:
            await asyncio.to_thread(
                self.store.finish_run,
                run_id,
                status=AutomationRunStatus.CANCELLED.value,
                error="cancelled before the run started",
            )
        return True

    async def _run_loop(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(self.poll_seconds)

    async def _execute_guarded(self, run: AutomationRun) -> None:
        async with self._slots:
            await self._execute(run)

    async def _execute(self, run: AutomationRun) -> None:
        automation = await asyncio.to_thread(self.store.get, run.automation_id)
        session_id: str | None = None
        case_run_id: str | None = None
        execution_id: str | None = None
        try:
            workspace = Path(automation.workspace_root)
            if not workspace.is_dir():
                raise FileNotFoundError(f"workspace does not exist: {workspace}")
            config = self.config_factory(str(workspace))
            manager = SessionManager(config)
            if automation.destination == AutomationDestination.HEARTBEAT.value:
                session_id = automation.target_session_id
                assert session_id is not None
                manager.load(session_id)
            else:
                session_id = manager.create("automation", mode="chat").session_id
            message = automation_trigger_message(automation, run)
            data = plain_data(message.data)
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
                submission_id=run.run_id,
                interactive_approvals=False,
                message_kind=message.kind or "lara.automation.trigger",
                message_data=dict(data) if isinstance(data, dict) else {},
                session_title=(
                    f"定时任务：{automation.name}"
                    if automation.destination == AutomationDestination.STANDALONE.value
                    else None
                ),
            )
            self._turns[run.run_id] = turn
            await turn.wait_ready()
            if turn.run_ref is not None:
                case_run_id = turn.run_ref.case_run_id
            if turn.execution_handle is not None:
                execution_id = turn.execution_handle.execution_id
            output, _ = await turn.result()
            if run.run_id in self._cancel_requested:
                # The turn was cancelled through cancel_run(); route through the
                # cancellation finalizer so the run is recorded exactly once.
                raise asyncio.CancelledError
            result = plain_object(plain_object(output.data).get("result"))
            status = str(result.get("status") or AutomationRunStatus.PASSED.value)
            if status not in {"passed", "failed", "error", "skipped"}:
                status = AutomationRunStatus.ERROR.value
            await asyncio.to_thread(
                self.store.finish_run,
                run.run_id,
                status=status,
                session_id=session_id,
                case_run_id=case_run_id,
                execution_id=execution_id,
                final_answer=str(result.get("final_answer") or ""),
                error=(str(result["error"]) if result.get("error") else None),
            )
        except asyncio.CancelledError:
            # A normal completion may already have finalized the run when the
            # cancel raced it; only a still-running run is stamped here.
            current = await asyncio.to_thread(self.store.get_run, run.run_id)
            if current.status == AutomationRunStatus.RUNNING.value:
                cancelled_by_user = run.run_id in self._cancel_requested
                await asyncio.to_thread(
                    self.store.finish_run,
                    run.run_id,
                    status=(
                        AutomationRunStatus.CANCELLED.value
                        if cancelled_by_user
                        else AutomationRunStatus.ERROR.value
                    ),
                    session_id=session_id,
                    case_run_id=case_run_id,
                    execution_id=execution_id,
                    error=(
                        "cancelled by user"
                        if cancelled_by_user
                        else "automation scheduler stopped before completion"
                    ),
                )
            raise
        except Exception as exc:  # noqa: BLE001 - run failures are persisted, not leaked
            missing_target = isinstance(exc, FileNotFoundError)
            await asyncio.to_thread(
                self.store.finish_run,
                run.run_id,
                status=(
                    AutomationRunStatus.FAILED.value
                    if missing_target
                    else AutomationRunStatus.ERROR.value
                ),
                session_id=session_id,
                case_run_id=case_run_id,
                execution_id=execution_id,
                error=str(exc),
            )
            if missing_target:
                await asyncio.to_thread(
                    self.store.pause_after_failure, automation.automation_id
                )
        finally:
            self._turns.pop(run.run_id, None)
            self._cancel_requested.discard(run.run_id)


__all__ = ["AutomationScheduler"]
