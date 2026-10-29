from __future__ import annotations

from pathlib import Path
from typing import Any

from lara.config import load_run_config
from lara.schema import RunConfig
from lara.sessions import SessionDirectory, SessionManager, user_lara_root_for

from .models import DispatchDestination, DispatchItem, DispatchItemKind, DispatchTask, DispatchTaskStatus
from .store import DispatchStore


class DispatchService:
    """Validated application operations shared by CLI, HTTP, and agent tools."""

    def __init__(self, store: DispatchStore) -> None:
        self.store = store

    def create(self, **values: Any) -> DispatchTask:
        self._validate_target(
            str(values.get("workspace_root") or ""),
            str(values.get("destination") or ""),
            values.get("target_session_id"),
        )
        return self.store.create(**values)

    def update(self, task_id: str, **changes: Any) -> DispatchTask:
        current = self.store.get(task_id)
        workspace = str(changes.get("workspace_root") or current.workspace_root)
        destination = str(changes.get("destination") or current.destination)
        target = changes.get("target_session_id", current.target_session_id)
        self._validate_target(workspace, destination, target)
        return self.store.update(task_id, **changes)

    def pause(self, task_id: str) -> DispatchTask:
        return self.store.set_status(task_id, DispatchTaskStatus.PAUSED.value)

    def resume(self, task_id: str) -> DispatchTask:
        return self.store.set_status(task_id, DispatchTaskStatus.ACTIVE.value)

    def submit_start(
        self,
        *,
        config: RunConfig,
        message: str,
        source_session_id: str | None = None,
        submission_id: str | None = None,
        case_id: str = "collaboration",
        model_group: str | None = None,
    ) -> DispatchItem:
        """Submit one background agent start; the dispatch scheduler executes it."""
        if model_group is not None:
            self._require_model_group(config, model_group)
        if source_session_id is not None:
            self._resolve_session(config, source_session_id)
        return self.store.submit_item(
            kind=DispatchItemKind.START.value,
            workspace_root=str(Path(config.workspace_root).resolve()),
            message=message,
            agent_alias=config.agent_alias,
            case_id=case_id,
            source_session_id=source_session_id,
            model_group=model_group,
            submission_id=submission_id,
        )

    def submit_fork(
        self,
        *,
        config: RunConfig,
        fork_from: str,
        message: str,
        submission_id: str | None = None,
        case_id: str = "collaboration",
        model_group: str | None = None,
    ) -> DispatchItem:
        """Submit one background fork; the fork source must be project-local."""
        if model_group is not None:
            self._require_model_group(config, model_group)
        # The fork copies session files, so the source has to be in this project.
        SessionManager(config).load(fork_from)
        return self.store.submit_item(
            kind=DispatchItemKind.FORK.value,
            workspace_root=str(Path(config.workspace_root).resolve()),
            message=message,
            agent_alias=config.agent_alias,
            case_id=case_id,
            source_session_id=fork_from,
            model_group=model_group,
            submission_id=submission_id,
        )

    @staticmethod
    def _require_model_group(config: RunConfig, model_group: str) -> None:
        clean = model_group.strip()
        if not clean:
            raise ValueError("model_group must be non-empty when provided")
        native = config.model_config
        if native is None or clean not in native.model_groups:
            raise ValueError(f"unknown model group {clean!r}")

    @staticmethod
    def _resolve_session(config: RunConfig, session_id: str) -> None:
        """Resolve the project owning a session; unknown sessions fail closed."""
        try:
            SessionManager(config).load(session_id)
        except FileNotFoundError:
            directory = SessionDirectory(user_lara_root_for(config.lara_root))
            if directory.locate_session(session_id) is None:
                raise

    @staticmethod
    def _validate_target(
        workspace_root: str, destination: str, target_session_id: object
    ) -> None:
        workspace = Path(workspace_root).expanduser().resolve()
        if not workspace.is_dir():
            raise FileNotFoundError(f"workspace does not exist: {workspace}")
        mode = DispatchDestination(destination)
        if mode is DispatchDestination.HEARTBEAT:
            session_id = str(target_session_id or "").strip()
            if not session_id:
                raise ValueError("heartbeat automation requires target_session_id")
            config = load_run_config(workspace_root=str(workspace))
            SessionManager(config).load(session_id)


__all__ = ["DispatchService"]
