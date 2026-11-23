"""lara composition root over the SDK execution host.

The SDK host owns user-message routing (fork interrupt vs. turn), the fork
exchange hand-off into the running turn, and shutdown. lara adds one route in
front of it — a plain session with a running turn takes the message as an
immediate steering input — and swaps in its runtime service (Feishu speak
channel, lara_api entries), its coordinator (execution/case-run indexes,
steering, recovery) and its session manager (run queries).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, cast

from pygent.agent.pygent_v1.orchestration.execution_host import (
    LocalExecutionHost as _SdkLocalExecutionHost,
    UserDelivery as _SdkUserDelivery,
)
from pygent.agent.pygent_v1.schema import RunConfig
from pygent.agent.pygent_v1.sessions import SessionManager as _SdkSessionManager

from lara.config import load_run_config
from lara.runtime.service import LaraRuntimeService
from lara.schema import RunConfig as LaraRunConfig
from lara.sessions import SessionManager, realtime_enabled_for

from .runtime_pool import WorkspaceRuntimePool
from .session_execution import SessionExecutionCoordinator


@dataclass(frozen=True)
class UserDelivery(_SdkUserDelivery):
    """SDK delivery plus lara's ``steering`` mode.

    A ``steering`` delivery resolved into the running turn: ``turn`` is that
    turn (re-attach to its stream; the react layer drains the message at its
    next boundary) and ``input_id`` the id the boundary event will carry.
    """

    mode: Literal["turn", "interrupt", "steering"]  # pyright: ignore[reportIncompatibleVariableOverride]
    input_id: str | None = None


class LocalExecutionHost(_SdkLocalExecutionHost):
    runtime_pool: WorkspaceRuntimePool  # pyright: ignore[reportIncompatibleVariableOverride] — lara's pool subclasses the SDK's
    coordinator: SessionExecutionCoordinator  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        *,
        runtime_pool: WorkspaceRuntimePool | None = None,
        coordinator: SessionExecutionCoordinator | None = None,
        prompt_registry: Any | None = None,
    ) -> None:
        super().__init__(
            runtime_pool=runtime_pool
            or WorkspaceRuntimePool(
                runtime_factory=self._build_runtime,
                # The pool passes this through explicitly, so the runtime's
                # own setdefault cannot fill it in: without it the background
                # memory agents (extractor/builder) cannot load their aliases.
                run_config_loader=load_run_config,
                prompt_registry=prompt_registry,
            ),
            coordinator=coordinator or SessionExecutionCoordinator(),
        )

    def _build_runtime(self, config: RunConfig, **kwargs: Any) -> LaraRuntimeService:
        # The pool only ever hands back the lara RunConfig it was given.
        return LaraRuntimeService(
            cast(LaraRunConfig, config),
            on_exchange_ready=partial(self._deliver_exchange, config),
            **kwargs,
        )

    async def deliver_user_message(
        self,
        *,
        config: RunConfig,
        manager: _SdkSessionManager,
        message: str,
        session_id: str | None = None,
        input_id: str | None = None,
        case_id: str = "chat",
        interactive_approvals: bool = True,
        attachments: Sequence[str] = (),
        session_title: str | None = None,
        model_group_name: str | None = None,
        selected_model_key: str | None = None,
    ) -> UserDelivery:
        """SDK routing plus lara's plain-session steering.

        The SDK forks a reply only for the realtime (Lara说) session and
        queues everything else behind the running turn. A plain session
        with a running turn instead steers it right away; when no running
        turn can take the input, the SDK path queues a turn as before.
        """

        if session_id is not None and not realtime_enabled_for(
            manager.load(session_id)
        ):
            resolved_input_id = input_id or uuid.uuid4().hex
            steered = await self.coordinator.deliver_steering(
                manager,
                session_id,
                input_id=resolved_input_id,
                message=message,
                attachments=attachments,
            )
            if steered is not None:
                return UserDelivery(
                    mode="steering", turn=steered, input_id=resolved_input_id
                )
        delivery = await super().deliver_user_message(
            config=config,
            manager=manager,
            message=message,
            session_id=session_id,
            input_id=input_id,
            case_id=case_id,
            interactive_approvals=interactive_approvals,
            attachments=attachments,
            session_title=session_title,
            model_group_name=model_group_name,
            selected_model_key=selected_model_key,
        )
        return UserDelivery(
            mode=delivery.mode, turn=delivery.turn, outcome=delivery.outcome
        )

    async def acquire_runtime(
        self,
        *,
        config: RunConfig,
        manager: _SdkSessionManager | None = None,
    ) -> Any:
        # The pool pins the first manager to the runtime generation; a drain
        # acquiring first must not pin the SDK base manager, which lacks the
        # run queries lara_api reads through the lease.
        return await self.runtime_pool.acquire(
            config=config, manager=manager or SessionManager(cast(LaraRunConfig, config))
        )


__all__ = ["LocalExecutionHost", "UserDelivery"]
