"""lara_api-facing lookups and recovery over the SDK session coordinator.

The SDK coordinator owns admission, execution lanes, recovery and shutdown.
Lara adds HTTP-facing lookups and sends plain-session steering to the running
turn's ReAct inbox.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pygent import UserMessage
from pygent.agent import (
    REACT_PROJECTION_OPERATION_KIND,
    StandaloneUserMessage,
    SteeringMode,
    encode_react_projection_operation,
)
from pygent.agent.pygent_v1.orchestration.managed_turn import ManagedSessionTurn
from pygent.agent.pygent_v1.orchestration.models import TurnState
from pygent.agent.pygent_v1.orchestration.session_execution import (
    SessionExecutionCoordinator as _SdkSessionExecutionCoordinator,
    SessionExecutionKey,
)
from pygent.agent.pygent_v1.runtime.attachments import (
    build_media_blocks,
    resolve_attachments,
)

# The SDK pipeline checkpoints and announces this kind at the ReAct boundary
# that drains it, the same way it treats fork exchanges.
STEERING_MESSAGE_KIND = "pygent.v1.user.steering"


class SessionExecutionCoordinator(_SdkSessionExecutionCoordinator):
    async def find_execution(self, execution_id: str) -> ManagedSessionTurn | None:
        return await self.find_delivery_turn(execution_id)

    async def find_case_run(self, case_run_id: str) -> ManagedSessionTurn | None:
        async with self._lock:
            return next(
                (
                    turn
                    for turn in self._managed_runs.values()
                    if turn.run_ref is not None
                    and turn.run_ref.case_run_id == case_run_id
                ),
                None,
            )

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
        attachments: Sequence[str],
        before_send: Callable[[str], Awaitable[None]],
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
        # Journal the target before handing the input to the runtime inbox.
        await before_send(active.execution_handle.execution_id)
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


__all__ = ["STEERING_MESSAGE_KIND", "SessionExecutionCoordinator", "SessionExecutionKey"]
