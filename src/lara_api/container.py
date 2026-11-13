from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

from lara.config import load_run_config
from lara.schema import RunConfig
from lara.sessions import SessionManager

from .project_state import GuiProjectState, SessionScope

if TYPE_CHECKING:
    from lara.dispatch import DispatchScheduler, DispatchService, DispatchStore
    from lara.orchestration import (
        RuntimeLease,
        SessionCollaborationService,
        SessionExecutionCoordinator,
        WorkspaceRuntimePool,
    )
    from lara.runtime.reminders import ReminderService


@dataclass(slots=True)
class ApiContext:
    """Application container shared by the HTTP composition root."""

    workspace_root: str | None = None
    agent_alias: str | None = None
    max_steps: int | None = None
    context_window: int | None = None
    state_path: str | None = None
    _config: RunConfig | None = None
    _manager: SessionManager | None = None
    _project_state: GuiProjectState | None = None
    _reminders: ReminderService | None = None
    _chat_registry: Any | None = None
    _session_coordinator: SessionExecutionCoordinator | None = None
    _session_collaboration: SessionCollaborationService | None = None
    _runtime_pool: WorkspaceRuntimePool | None = None
    _terminal_service: Any | None = None
    _dispatch_store: DispatchStore | None = None
    _dispatch_service: DispatchService | None = None
    _dispatch_scheduler: DispatchScheduler | None = None
    _feishu_bridge: Any | None = None
    _stale_runs_reconciled: bool = False
    _lock: RLock = field(default_factory=RLock)

    @property
    def config(self) -> RunConfig:
        with self._lock:
            if self._config is None:
                self._config = self._load_config()
            return self._config

    @property
    def manager(self) -> SessionManager:
        with self._lock:
            if self._manager is None:
                self._manager = SessionManager(self.config)
            return self._manager

    @property
    def project_state(self) -> GuiProjectState:
        with self._lock:
            if self._project_state is None:
                self._project_state = GuiProjectState.load(self.state_path)
            return self._project_state

    @property
    def reminders(self) -> ReminderService:
        with self._lock:
            if self._reminders is None:
                from lara.runtime.reminders import ReminderService

                self._reminders = ReminderService(self.config)
            return self._reminders

    @property
    def chat_registry(self) -> Any:
        with self._lock:
            if self._chat_registry is None:
                raise RuntimeError("chat registry has not been configured")
            return self._chat_registry

    @property
    def session_coordinator(self) -> SessionExecutionCoordinator:
        with self._lock:
            if self._session_coordinator is None:
                from lara.orchestration import SessionExecutionCoordinator

                self._session_coordinator = SessionExecutionCoordinator()
            return self._session_coordinator

    @property
    def runtime_pool(self) -> WorkspaceRuntimePool:
        with self._lock:
            if self._runtime_pool is None:
                from functools import partial

                from lara.orchestration import WorkspaceRuntimePool
                from lara.runtime.service import LaraRuntimeService

                # API composition root: register the stock Lara prompt
                # profile so the kernel's empty registry serves full prompts.
                from lara.runtime.prompt_profile import build_prompt_registry

                # The SDK pool defaults to PygentRuntimeService, which lacks
                # the steering/cancel/compress entries lara_api drives, and it
                # no longer carries a collaboration slot; bind both here.
                self._runtime_pool = WorkspaceRuntimePool(
                    runtime_factory=partial(
                        LaraRuntimeService,
                        collaboration=self.session_collaboration,
                    ),
                    prompt_registry=build_prompt_registry(),
                )
            return self._runtime_pool

    @property
    def session_collaboration(self) -> SessionCollaborationService:
        with self._lock:
            if self._session_collaboration is None:
                from lara.orchestration import SessionCollaborationService

                self._session_collaboration = SessionCollaborationService()
            return self._session_collaboration

    @property
    def stale_runs_reconciled(self) -> bool:
        return self._stale_runs_reconciled

    def mark_stale_runs_reconciled(self) -> None:
        self._stale_runs_reconciled = True

    async def acquire_runtime(
        self,
        *,
        config: RunConfig | None = None,
        manager: SessionManager | None = None,
    ) -> RuntimeLease:
        _ = self.session_collaboration
        effective_config = config or self.config
        # The SDK pool falls back to its base SessionManager and pins it to
        # the runtime generation; later callers passing lara's subclass then
        # get the base one back, so always hand it a lara manager.
        if manager is None:
            manager = (
                self.manager
                if effective_config is self.config
                else SessionManager(effective_config)
            )
        return await self.runtime_pool.acquire(
            config=effective_config,
            manager=manager,
        )

    @property
    def terminal_service(self) -> Any:
        with self._lock:
            if self._terminal_service is None:
                from lara_api.services.terminal_service import TerminalService

                self._terminal_service = TerminalService()
            return self._terminal_service

    @property
    def dispatch_store(self) -> DispatchStore:
        with self._lock:
            if self._dispatch_store is None:
                from lara.dispatch import DispatchStore

                self._dispatch_store = DispatchStore(
                    Path(self.config.user_pygent_root) / "dispatch-v1.sqlite3"
                )
            return self._dispatch_store

    @property
    def dispatch_service(self) -> DispatchService:
        with self._lock:
            if self._dispatch_service is None:
                from lara.dispatch import DispatchService

                self._dispatch_service = DispatchService(self.dispatch_store)
            return self._dispatch_service

    @property
    def dispatch_scheduler(self) -> DispatchScheduler:
        with self._lock:
            if self._dispatch_scheduler is None:
                from lara.dispatch import DispatchScheduler

                self._dispatch_scheduler = DispatchScheduler(
                    store=self.dispatch_store,
                    coordinator=self.session_coordinator,
                    acquire_runtime=self.acquire_runtime,
                    config_factory=self._dispatch_config,
                )
            return self._dispatch_scheduler

    def start_dispatch_scheduler(self) -> None:
        self.dispatch_scheduler.start()

    def start_feishu_bridge(self) -> bool:
        """Start the inbound Feishu relay when the bridge config enables it."""

        from lara_api.services.feishu_bridge import FeishuBridge

        self._feishu_bridge = FeishuBridge(self)
        return self._feishu_bridge.start()

    def _dispatch_config(self, workspace_root: str, agent_alias: str | None) -> RunConfig:
        return load_run_config(
            workspace_root=workspace_root,
            agent_alias=agent_alias or self.agent_alias,
            max_steps=self.max_steps,
            context_window=self.context_window,
        )

    def attach_chat_registry(self, registry: Any) -> None:
        with self._lock:
            if self._chat_registry is not None:
                raise RuntimeError("chat registry is already configured")
            self._chat_registry = registry

    async def aclose(self) -> None:
        with self._lock:
            dispatch_scheduler = self._dispatch_scheduler
            self._dispatch_scheduler = None
            reminders = self._reminders
            self._reminders = None
            registry = self._chat_registry
            self._chat_registry = None
            coordinator = self._session_coordinator
            self._session_coordinator = None
            collaboration = self._session_collaboration
            self._session_collaboration = None
            runtime_pool = self._runtime_pool
            self._runtime_pool = None
            terminal_service = self._terminal_service
            self._terminal_service = None
        if dispatch_scheduler is not None:
            await dispatch_scheduler.close()
        if terminal_service is not None:
            terminal_service.close()
        if runtime_pool is not None:
            await runtime_pool.stop_accepting()
        if collaboration is not None:
            await collaboration.aclose()
        if registry is not None:
            await registry.close()
            coordinator = None
        if coordinator is not None:
            await coordinator.close()
        if runtime_pool is not None:
            await runtime_pool.close(cancel=True)
        if reminders is not None:
            await reminders.close()

    async def areload(self, overrides: dict[str, Any] | None = None) -> RunConfig:
        with self._lock:
            old_config = self.config
            reminders = self._reminders
            self._reminders = None
            runtime_pool = self._runtime_pool
        if reminders is not None:
            await reminders.close()
        config = self.reload(overrides)
        if runtime_pool is not None:
            from lara.orchestration import RuntimeScopeKey

            await runtime_pool.retire_scope(RuntimeScopeKey.from_config(old_config))
        return config

    def reload(self, overrides: dict[str, Any] | None = None) -> RunConfig:
        if overrides:
            for key in ("workspace_root", "agent_alias", "max_steps", "context_window"):
                if key in overrides:
                    setattr(self, key, overrides[key])
        with self._lock:
            self._config = self._load_config()
            self._manager = SessionManager(self._config)
            return self._config

    def remember_project(self, project_path: str | Path) -> None:
        self.project_state.remember_project(project_path)

    def config_for_scope(self, scope: SessionScope) -> RunConfig:
        if scope.workspace_root is None:
            # Workspace-less scopes own their storage under the user root;
            # make sure the agent's working directory exists before tools run.
            Path(scope.runtime_workspace_root).mkdir(parents=True, exist_ok=True)
            return RunConfig(
                workspace_root=scope.runtime_workspace_root,
                pygent_root=scope.pygent_root,
                agent_alias=self.agent_alias or self.config.agent_alias,
                system_prompt=self.config.system_prompt,
                resolved_agent=self.config.resolved_agent,
                model_retry=self.config.model_retry,
                model_config_mapping=deepcopy(self.config.model_config_mapping),
                model_configuration_status=self.config.model_configuration_status,
                model_configuration_error=self.config.model_configuration_error,
                user_pygent_root=self.config.user_pygent_root,
                runtime_approvals=self.config.runtime_approvals,
                max_steps=self.max_steps if self.max_steps is not None else -1,
                context_window=self.context_window,
            )
        return load_run_config(
            workspace_root=scope.workspace_root,
            agent_alias=self.agent_alias,
            max_steps=self.max_steps,
            context_window=self.context_window,
        )

    def _load_config(self) -> RunConfig:
        return load_run_config(
            workspace_root=self.workspace_root,
            agent_alias=self.agent_alias,
            max_steps=self.max_steps,
            context_window=self.context_window,
        )
