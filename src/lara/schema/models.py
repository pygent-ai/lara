from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from lara.core.paths import user_pygent_root


def _abs_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve())


def _require(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


# 与 SDK schema 同构的类型直接复用（dataclass 相等性 / isinstance 需要同一类）。
from pygent.agent.pygent_v1.schema.models import (  # noqa: E402
    AgentSession,
    DelegationConfig as _SDKDelegationConfig,
    native_model_config_complete,
    RunConfig as _SDKRunConfig,
    BashCliPreset,
    CaseRunRef,
    CaseRunStatus,
    ContextEvent,
    EternalConversationConfig,
    MCPServerConfig,
    ModelRetryConfig,
    RealtimeInteractionConfig,
    ResolvedAgentConfig,
    RuntimeApprovalConfig,
    RuntimeCapacityConfig,
    RuntimeDurabilityConfig,
    RuntimeSteeringConfig,
    SessionRef,
    SessionSpec,
)


def case_run_resumable(status: CaseRunStatus | None) -> bool:
    """lora 的会话恢复规则：这些状态下用户没拿到结果，新一轮可继续对话。"""

    return status in {CaseRunStatus.FAILED, CaseRunStatus.ERROR, CaseRunStatus.SKIPPED}


def default_cli_bash_presets() -> list[BashCliPreset]:
    return [
        BashCliPreset(
            name="rg",
            command="rg --help",
            description="Fast recursive text search. Prefer it for code and file text search.",
        ),
        BashCliPreset(
            name="pyright",
            command="pyright --help",
            description="Python type checker. Use it for static type validation when available.",
        ),
        BashCliPreset(
            name="lara-session",
            command="uv run lara session --help",
            description=(
                "Session CLI. Use `uv run lara session start` for a new background Agent, "
                "`uv run lara session send <target> --source-session <current>` for "
                "tool-boundary Agent messages, and "
                "`uv run lara session run` or `chat` for user turns."
            ),
        ),
        BashCliPreset(
            name="lara-automation",
            command="uv run lara automation --help",
            description=(
                "Manage Lara scheduled Agent tasks with "
                "`create|list|show|update|pause|resume|delete|run|runs`. Use `--session` with "
                "the current session ID for a heartbeat, or `--standalone` for a new "
                "session per run. Run `uv run lara automation --help` for complete syntax."
            ),
        ),
    ]


@dataclass(slots=True)
class DelegationConfig(_SDKDelegationConfig):
    """SDK delegation limits + lora's allow-list and background policy."""

    allowed_agents: tuple[str, ...] = ()
    max_parallel: int = 4
    background_enabled: bool = True

    def __post_init__(self) -> None:
        _SDKDelegationConfig.__post_init__(self)
        self.allowed_agents = tuple(
            _require(item, "delegation agent") for item in self.allowed_agents
        )
        self.max_parallel = int(self.max_parallel)
        if self.max_parallel < 1:
            raise ValueError("delegation limits must be positive")


@dataclass(slots=True)
class RunConfig(_SDKRunConfig):
    """SDK run configuration rooted at ``~/.lara`` with lora's delegation."""

    cli_bash_presets: list[BashCliPreset] = field(
        default_factory=default_cli_bash_presets
    )
    delegation: DelegationConfig = field(  # pyright: ignore[reportIncompatibleVariableOverride]
        default_factory=DelegationConfig
    )

    def __post_init__(self) -> None:
        # Resolve the data root before the SDK does, or its ``~/.pygent_v1``
        # fallback would apply when the loader did not inject one.
        self.user_pygent_root = _abs_path(self.user_pygent_root or user_pygent_root())
        if isinstance(self.delegation, dict):
            self.delegation = DelegationConfig(**self.delegation)  # pyright: ignore[reportIncompatibleVariableOverride]
        _SDKRunConfig.__post_init__(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunConfig":
        return cast(RunConfig, _SDKRunConfig.from_dict.__func__(cls, data))


@dataclass(slots=True)
class CaseRunResult:
    session_id: str
    case_id: str
    case_run_id: str
    status: CaseRunStatus
    final_answer: str = ""
    error: str | None = None
    event_count: int = 0
    message_count: int = 0

    def __post_init__(self) -> None:
        self.session_id = _require(self.session_id, "session_id")
        self.case_id = _require(self.case_id, "case_id")
        self.case_run_id = _require(self.case_run_id, "case_run_id")
        verdict = CaseRunStatus.from_value(self.status)
        if verdict is None or not verdict.terminal:
            raise ValueError("status must be one of passed, failed, error, skipped")
        self.status = verdict

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaseRunResult":
        return cls(**data)


@dataclass(slots=True)
class WorkspaceRef:
    workspace_root: str
    case_run_id: str
    baseline_path: str | None = None

    def __post_init__(self) -> None:
        self.workspace_root = _abs_path(self.workspace_root)
        self.case_run_id = _require(self.case_run_id, "case_run_id")
        if self.baseline_path is not None:
            self.baseline_path = _abs_path(self.baseline_path)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvaluationResult:
    status: Literal["passed", "failed", "error"]
    metrics: dict[str, Any] = field(default_factory=dict)
    verdict: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"passed", "failed", "error"}:
            raise ValueError("status must be one of passed, failed, error")
        if not isinstance(self.metrics, dict):
            raise ValueError("metrics must be a dict")
        if not isinstance(self.verdict, dict):
            raise ValueError("verdict must be a dict")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CaseDefinition:
    id: str
    title: str
    type: str
    session: dict[str, Any] = field(default_factory=dict)
    workspace: dict[str, Any] = field(default_factory=dict)
    input: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = _require(self.id, "id")
        self.title = _require(self.title, "title")
        self.type = _require(self.type, "type")
        for name in ("session", "workspace", "input", "expect", "metrics"):
            if not isinstance(getattr(self, name), dict):
                raise ValueError(f"{name} must be a dict")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaseDefinition":
        return cls(
            id=data["id"],
            title=data.get("title", data["id"]),
            type=data.get("type", "e2e"),
            session=dict(data.get("session") or {}),
            workspace=dict(data.get("workspace") or {}),
            input=dict(data.get("input") or {}),
            expect=dict(data.get("expect") or {}),
            metrics=dict(data.get("metrics") or {}),
        )


