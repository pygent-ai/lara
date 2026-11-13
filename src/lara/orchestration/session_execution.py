"""lara_api-facing lookups and recovery over the SDK session coordinator.

The SDK coordinator owns admission, lanes, and shutdown but indexes turns only
by identity. The HTTP adapters resolve turns by execution id and case run id
(stream re-attach, steering, approvals, stale-run reconciliation) and recover
durable executions after a restart, so this subclass keeps those indexes and
reuses the SDK drive path for everything else.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pygent.agent.pygent_v1.orchestration.managed_turn import ManagedSessionTurn
from pygent.agent.pygent_v1.orchestration.models import TurnCommand
from pygent.agent.pygent_v1.orchestration.session_execution import (
    SessionExecutionCoordinator as _SdkSessionExecutionCoordinator,
    SessionExecutionKey,
)

from lara.schema import CaseRunRef


class SessionExecutionCoordinator(_SdkSessionExecutionCoordinator):
    """SDK coordinator plus execution/case-run indexes and recovery."""

    def __init__(self) -> None:
        super().__init__()
        self._runs: dict[str, ManagedSessionTurn] = {}
        self._case_runs: dict[str, ManagedSessionTurn] = {}

    async def recover_turn(
        self,
        *,
        lease: Any,
        run_ref: CaseRunRef,
        execution_id: str,
    ) -> ManagedSessionTurn:
        """Recover a durable execution exactly once across concurrent callers."""

        async with self._lock:
            existing = self._runs.get(execution_id)
            if existing is None and self._closing:
                raise RuntimeError("session execution coordinator is closing")
            if existing is None:
                turn = ManagedSessionTurn(
                    lease=lease,
                    command=TurnCommand(
                        session_id=run_ref.session_id,
                        message="recovered execution",
                        case_id=run_ref.case_id,
                    ),
                    run_ref=run_ref,
                    recovery_execution_id=execution_id,
                )
                self._managed_runs[id(turn)] = turn
                self._runs[execution_id] = turn
                self._case_runs[run_ref.case_run_id] = turn
                turn.task = asyncio.create_task(
                    self._drive_turn(turn),
                    name=f"session-turn-recovery:{run_ref.case_run_id}",
                )
                return turn
        await lease.release()
        return existing

    async def find_execution(self, execution_id: str) -> ManagedSessionTurn | None:
        async with self._lock:
            return self._runs.get(execution_id)

    async def find_case_run(self, case_run_id: str) -> ManagedSessionTurn | None:
        async with self._lock:
            return self._case_runs.get(case_run_id)

    async def session_busy(self, manager: Any, session_id: str) -> bool:
        key = SessionExecutionKey.from_manager(manager, session_id)
        async with self._lock:
            return any(
                not turn.state.terminal
                and SessionExecutionKey.from_manager(
                    turn.manager, turn.command.session_id
                )
                == key
                for turn in self._managed_runs.values()
            )

    async def _drive_in_session_lane(self, turn: ManagedSessionTurn) -> None:
        # Create the case run before the SDK drive (which skips creation when
        # run_ref is already set) so a turn is findable by case run across the
        # whole start window; the execution id is indexed once it exists.
        if turn.run_ref is None:
            turn.run_ref = turn.manager.start_case_run(
                turn.command.session_id,
                turn.command.case_id,
                run_config=turn.manager.config,
            )
        await self._register_case_run(turn)
        indexer = asyncio.ensure_future(self._index_execution_when_ready(turn))
        try:
            await super()._drive_in_session_lane(turn)
        finally:
            if not indexer.done():
                indexer.cancel()
            await asyncio.gather(indexer, return_exceptions=True)

    async def _register_case_run(self, turn: ManagedSessionTurn) -> None:
        if turn.run_ref is None:
            raise RuntimeError("cannot register a turn before its case run exists")
        async with self._lock:
            self._case_runs[turn.run_ref.case_run_id] = turn

    async def _index_execution_when_ready(self, turn: ManagedSessionTurn) -> None:
        await turn.ready.wait()
        if turn.execution_id is not None:
            async with self._lock:
                self._runs[turn.execution_id] = turn

    async def _remove(self, turn: ManagedSessionTurn) -> None:
        async with self._lock:
            if (
                turn.execution_id is not None
                and self._runs.get(turn.execution_id) is turn
            ):
                del self._runs[turn.execution_id]
            if (
                turn.run_ref is not None
                and self._case_runs.get(turn.run_ref.case_run_id) is turn
            ):
                del self._case_runs[turn.run_ref.case_run_id]
        await super()._remove(turn)


__all__ = ["SessionExecutionCoordinator", "SessionExecutionKey"]
