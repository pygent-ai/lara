"""lara composition root over the SDK execution host.

The SDK host owns user-message routing (fork interrupt vs. turn), the fork
exchange hand-off into the running turn, and shutdown. lara only swaps in its
runtime service (Feishu speak channel, lara_api entries), its coordinator
(execution/case-run indexes, recovery) and its session manager (run queries).
"""

from __future__ import annotations

from functools import partial
from typing import Any, cast

from pygent.agent.pygent_v1.orchestration.execution_host import (
    LocalExecutionHost as _SdkLocalExecutionHost,
    UserDelivery,
)
from pygent.agent.pygent_v1.schema import RunConfig
from pygent.agent.pygent_v1.sessions import SessionManager as _SdkSessionManager

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
