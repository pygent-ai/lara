"""lara composition root over the SDK execution host.

The SDK host owns persisted user-message routing, fork exchanges and shutdown.
Lara supplies the plain-session steering hook and its product-specific runtime,
coordinator and session manager.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, cast

from pygent.agent.pygent_v1.orchestration.execution_host import (
    LocalExecutionHost as _SdkLocalExecutionHost,
    UserDelivery,
)
from pygent.agent.pygent_v1.orchestration.managed_turn import ManagedSessionTurn
from pygent.agent.pygent_v1.schema import RunConfig
from pygent.agent.pygent_v1.sessions import SessionManager as _SdkSessionManager

from lara.config import load_run_config
from lara.runtime.service import LaraRuntimeService
from lara.schema import RunConfig as LaraRunConfig
from lara.sessions import SessionManager

from .runtime_pool import WorkspaceRuntimePool
from .session_execution import SessionExecutionCoordinator


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

    async def _try_steering(
        self,
        manager: _SdkSessionManager,
        session_id: str,
        input_id: str,
        payload: dict[str, Any],
        before_send: Callable[[str], Awaitable[None]],
    ) -> ManagedSessionTurn | None:
        return await self.coordinator.deliver_steering(
            manager,
            session_id,
            input_id=input_id,
            message=payload["message"],
            attachments=payload["attachments"],
            before_send=before_send,
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
