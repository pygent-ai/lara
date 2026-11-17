"""lara_api-facing lookups and recovery over the SDK session coordinator.

The SDK coordinator owns admission, lanes, and shutdown but indexes turns only
by identity. The HTTP adapters resolve turns by execution id and case run id
(stream re-attach, steering, approvals, stale-run reconciliation) and recover
durable executions after a restart, so this subclass keeps those indexes and
reuses the SDK drive path for everything else. It also steers plain sessions:
the SDK only hands fork exchanges (wait mode) to a running turn, while lara's
plain sessions take a mid-turn user message as an immediate steering input.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from pygent import UserMessage
from pygent.agent import (
    REACT_PROJECTION_OPERATION_KIND,
    StandaloneUserMessage,
    SteeringMode,
    encode_react_projection_operation,
)
from pygent.agent.pygent_v1.orchestration.managed_turn import ManagedSessionTurn
from pygent.agent.pygent_v1.orchestration.models import TurnCommand, TurnState
from pygent.agent.pygent_v1.orchestration.session_execution import (
    SessionExecutionCoordinator as _SdkSessionExecutionCoordinator,
    SessionExecutionKey,
)
from pygent.agent.pygent_v1.runtime.attachments import (
    build_media_blocks,
    resolve_attachments,
)

from lara.schema import CaseRunRef

# The SDK pipeline checkpoints and announces this kind at the ReAct boundary
# that drains it, the same way it treats fork exchanges.
STEERING_MESSAGE_KIND = "pygent.v1.user.steering"


class SessionExecutionCoordinator(_SdkSessionExecutionCoordinator):
    """SDK coordinator plus execution/case-run indexes, steering and recovery."""

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

    async def deliver_steering(
        self,
        manager: Any,
        session_id: str,
        *,
        input_id: str,
        message: str,
        attachments: Sequence[str] = (),
    ) -> ManagedSessionTurn | None:
        """Steer the turn running on the session lane with one user message.

        The counterpart of the SDK's ``deliver_exchange`` for plain sessions:
        the message goes into the running execution's react inbox in the
        configured steering mode (immediate by default), so the react layer
        cancels its in-flight step and takes it as the next model input.
        Returns the steered turn, or ``None`` when no running turn could
        accept the input (none on the lane, still starting, or its inbox
        already sealed) — the caller then queues a turn instead.
        """

        key = SessionExecutionKey.from_manager(manager, session_id)
        async with self._lock:
            lane = self._session_lanes.get(key)
            active = lane.active if lane is not None else None
        if (
            active is None
            or active.state is not TurnState.RUNNING
            or active.execution_handle is None
            or active.run_ref is None
        ):
            return None
        config = active.runtime_service.config
        resolved = (
            resolve_attachments(attachments, config.workspace_root)
            if attachments
            else ()
        )
        data: dict[str, Any] = {
            "input_id": input_id,
            "case_run_id": active.run_ref.case_run_id,
        }
        if resolved:
            data["attachments"] = [item.relative for item in resolved]
        delivery = await active.execution_handle.send_input(
            input_id=input_id,
            kind=REACT_PROJECTION_OPERATION_KIND,
            value=encode_react_projection_operation(
                StandaloneUserMessage(
                    UserMessage(
                        content=message,
                        kind=STEERING_MESSAGE_KIND,
                        data=data,
                        media=build_media_blocks(resolved),
                    ),
                    mode=(
                        SteeringMode.IMMEDIATE
                        if config.runtime_steering.mode == "immediate"
                        else SteeringMode.WAIT
                    ),
                )
            ),
        )
        if delivery.status in {"accepted", "duplicate"}:
            return active
        return None

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


__all__ = ["STEERING_MESSAGE_KIND", "SessionExecutionCoordinator", "SessionExecutionKey"]
