from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock, Thread
from typing import TYPE_CHECKING, Any

from pygent.agent.pygent_v1.runtime.delivery_store import session_delivery_path

from lara.config import load_run_config
from lara.schema import RunConfig
from lara.sessions import SessionManager

from .project_state import (
    GuiProjectState,
    SessionScope,
    active_project_scope_id,
    build_session_scopes,
)

if TYPE_CHECKING:
    from pygent.agent.pygent_v1.scheduler.daemon import SchedulerDaemon
    from pygent.agent.pygent_v1.scheduler.store import SchedulerStore

    from lara.orchestration import (
        LocalExecutionHost,
        RuntimeLease,
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
    _execution_host: LocalExecutionHost | None = None
    _terminal_service: Any | None = None
    _scheduler_store: SchedulerStore | None = None
    _scheduler_daemon: SchedulerDaemon | None = None
    _scheduler_thread: Thread | None = None
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
    def execution_host(self) -> LocalExecutionHost:
        """The user-message entry and runtime pool shared by every adapter."""

        with self._lock:
            if self._execution_host is None:
                from lara.orchestration import LocalExecutionHost

                # API composition root: register the stock Lara prompt
                # profile so the kernel's empty registry serves full prompts.
                from lara.runtime.prompt_profile import build_prompt_registry

                self._execution_host = LocalExecutionHost(
                    coordinator=self.session_coordinator,
                    prompt_registry=build_prompt_registry(),
                )
            return self._execution_host

    @property
    def runtime_pool(self) -> WorkspaceRuntimePool:
        return self.execution_host.runtime_pool

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
        effective_config = config or self.config
        if manager is None and effective_config is self.config:
            manager = self.manager
        return await self.execution_host.acquire_runtime(
            config=effective_config, manager=manager
        )

    async def recover_message_deliveries(self) -> None:
        host = self.execution_host
        active_manager = self.manager
        await host.recover_pending_messages(
            config=active_manager.config, manager=active_manager
        )
        active_scope = active_project_scope_id(active_manager.config.workspace_root)
        for scope in build_session_scopes(
            self.project_state, active_workspace_root=active_manager.config.workspace_root
        ):
            if scope.scope_id == active_scope:
                continue
            if not session_delivery_path(Path(scope.pygent_root) / "sessions").is_file():
                continue
            config = self.config_for_scope(scope)
            await host.recover_pending_messages(
                config=config, manager=SessionManager(config)
            )

    @property
    def terminal_service(self) -> Any:
        with self._lock:
            if self._terminal_service is None:
                from lara_api.services.terminal_service import TerminalService

                self._terminal_service = TerminalService()
            return self._terminal_service

    @property
    def scheduler_store(self) -> SchedulerStore:
        with self._lock:
            if self._scheduler_store is None:
                from pygent.agent.pygent_v1.scheduler.store import (
                    SchedulerStore,
                    scheduler_db_path,
                )

                self._scheduler_store = SchedulerStore(
                    scheduler_db_path(self.config.pygent_root)
                )
            return self._scheduler_store

    def start_scheduler_daemon(self) -> None:
        """Run the SDK scheduler daemon for this workspace while the API is up.

        The daemon is a blocking poll loop that spawns `lara session run`
        children, so it lives on its own thread; `lara scheduler run` offers
        the same daemon as a standalone process.
        """

        from lara.cli.scheduler import build_daemon

        with self._lock:
            if self._scheduler_daemon is not None:
                return
            daemon = build_daemon(self.config, store=self.scheduler_store)
            thread = Thread(
                target=daemon.run_forever, name="lara-scheduler", daemon=True
            )
            self._scheduler_daemon = daemon
            self._scheduler_thread = thread
        thread.start()

    def start_feishu_bridge(self) -> bool:
        """Start the inbound Feishu relay when the bridge config enables it."""

        from lara_api.services.feishu_bridge import FeishuBridge

        self._feishu_bridge = FeishuBridge(self)
        return self._feishu_bridge.start()

    def attach_chat_registry(self, registry: Any) -> None:
        with self._lock:
            if self._chat_registry is not None:
                raise RuntimeError("chat registry is already configured")
            self._chat_registry = registry

    async def aclose(self) -> None:
        with self._lock:
            scheduler_daemon = self._scheduler_daemon
            scheduler_thread = self._scheduler_thread
            self._scheduler_daemon = None
            self._scheduler_thread = None
            reminders = self._reminders
            self._reminders = None
            registry = self._chat_registry
            self._chat_registry = None
            self._session_coordinator = None
            host = self._execution_host
            self._execution_host = None
            terminal_service = self._terminal_service
            self._terminal_service = None
        if scheduler_daemon is not None:
            scheduler_daemon.stop()
        if terminal_service is not None:
            terminal_service.close()
        if host is not None:
            await host.aclose()
        elif registry is not None:
            await registry.close()
        if reminders is not None:
            await reminders.close()
        if scheduler_thread is not None:
            # The poll loop checks the stop flag between sleeps; do not block
            # shutdown on a task that is mid-flight in a child process.
            scheduler_thread.join(timeout=1.0)

    async def areload(self, overrides: dict[str, Any] | None = None) -> RunConfig:
        with self._lock:
            old_config = self.config
            reminders = self._reminders
            self._reminders = None
            host = self._execution_host
        if reminders is not None:
            await reminders.close()
        config = self.reload(overrides)
        if host is not None:
            from lara.orchestration import RuntimeScopeKey

            await host.runtime_pool.retire_scope(RuntimeScopeKey.from_config(old_config))
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
                # The eternal conversation master switch is deployment-wide;
                # dropping it here would silently disable memory for the
                # dedicated says session even when the session opted in.
                eternal_conversation=self.config.eternal_conversation,
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
