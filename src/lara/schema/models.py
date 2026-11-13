from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pygent.llm import ModelConfig

from lara.core.paths import project_pygent_root, user_pygent_root


def _abs_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve())


def _require(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def native_model_config_complete(mapping: dict[str, Any]) -> bool:
    """True when the native subtree can satisfy Pygent's non-empty invariants.

    Pygent's ``ModelConfig.from_mapping()`` rejects empty ``connections`` and
    ``models``; the settings UI also saves connection-only or group-less
    states, which the runtime must load as an explicit unconfigured state
    instead of failing.
    """
    return all(mapping.get(key) for key in ("connections", "models", "model_groups"))


@dataclass(slots=True)
class DelegationConfig:
    allowed_agents: tuple[str, ...] = ()
    max_depth: int = 4
    max_parallel: int = 4
    background_enabled: bool = True

    def __post_init__(self) -> None:
        self.allowed_agents = tuple(
            _require(item, "delegation agent") for item in self.allowed_agents
        )
        self.max_depth = int(self.max_depth)
        self.max_parallel = int(self.max_parallel)
        if self.max_depth < 1 or self.max_parallel < 1:
            raise ValueError("delegation limits must be positive")


# 与 SDK schema 同构的类型直接复用（dataclass 相等性 / isinstance 需要同一类）。
from pygent.agent.pygent_v1.schema.models import (  # noqa: E402
    AgentSession,
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
class RunConfig:
    workspace_root: str
    pygent_root: str = ""
    session_id: str | None = None
    case_file: str | None = None
    max_steps: int = -1
    agent_alias: str = "default"
    # The configured static system prompt: the composition root's text, kept
    # as data. The prompt registry ships empty; modules are injected by the
    # composition root on top of this base.
    system_prompt: str = ""
    resolved_agent: ResolvedAgentConfig | None = field(
        default=None, repr=False, compare=False
    )
    # Retry policy from the agent profile; it exists regardless of whether the
    # model configuration is complete enough to bind a model group.
    model_retry: ModelRetryConfig = field(
        default_factory=ModelRetryConfig, repr=False, compare=False
    )
    model_config_mapping: dict[str, Any] = field(default_factory=dict)
    model_config: ModelConfig | None = field(default=None, repr=False, compare=False)
    model_configuration_status: Literal["configured", "unconfigured", "legacy"] = (
        "unconfigured"
    )
    model_configuration_error: str | None = None
    user_identity: str = "default"
    cli_bash_presets: list[BashCliPreset] = field(
        default_factory=default_cli_bash_presets
    )
    bash_full_output_allowlist: list[str] = field(default_factory=list)
    allow_read_outside_workspace: bool = True
    user_pygent_root: str = ""
    context_window: int | None = None
    context_compression_enabled: bool = True
    context_compression_trigger_ratio: float = 0.9
    context_compression_file_read_count: int = 5
    context_compression_file_read_max_chars: int = 5000
    runtime_durability: RuntimeDurabilityConfig = field(
        default_factory=RuntimeDurabilityConfig
    )
    runtime_steering: RuntimeSteeringConfig = field(
        default_factory=RuntimeSteeringConfig
    )
    runtime_capacity: RuntimeCapacityConfig = field(
        default_factory=RuntimeCapacityConfig
    )
    runtime_approvals: RuntimeApprovalConfig = field(
        default_factory=RuntimeApprovalConfig
    )
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    delegation: DelegationConfig = field(default_factory=DelegationConfig)
    eternal_conversation: EternalConversationConfig = field(
        default_factory=EternalConversationConfig
    )
    realtime_interaction: RealtimeInteractionConfig = field(
        default_factory=RealtimeInteractionConfig
    )

    def __post_init__(self) -> None:
        self.workspace_root = _abs_path(self.workspace_root)
        self.user_pygent_root = _abs_path(self.user_pygent_root or user_pygent_root())
        self.pygent_root = _abs_path(
            self.pygent_root
            or project_pygent_root(self.workspace_root, self.user_pygent_root)
        )
        if self.case_file is not None:
            self.case_file = _abs_path(self.case_file)
        if self.max_steps != -1 and self.max_steps <= 0:
            raise ValueError("max_steps must be -1 or greater than 0")
        if self.context_window is not None:
            self.context_window = int(self.context_window)
            if self.context_window <= 0:
                raise ValueError("context_window must be greater than 0")
        self.agent_alias = _require(self.agent_alias, "agent_alias")
        if not isinstance(self.model_retry, ModelRetryConfig):
            self.model_retry = ModelRetryConfig(**self.model_retry)
        if (
            self.model_config is None
            and native_model_config_complete(self.model_config_mapping)
        ):
            self.model_config = ModelConfig.from_mapping(self.model_config_mapping)
        if self.model_config is not None:
            self.model_configuration_status = "configured"
            self.model_configuration_error = None
        elif self.model_configuration_status == "configured":
            raise ValueError("configured model status requires model_config")
        self.user_identity = _require(self.user_identity or "default", "user_identity")
        self.cli_bash_presets = [
            preset if isinstance(preset, BashCliPreset) else BashCliPreset(**preset)
            for preset in self.cli_bash_presets
        ]
        if not isinstance(self.bash_full_output_allowlist, list):
            raise ValueError("bash_full_output_allowlist must be a list")
        self.bash_full_output_allowlist = [
            _require(item, "bash_full_output_allowlist item")
            for item in self.bash_full_output_allowlist
        ]
        self.context_compression_enabled = bool(self.context_compression_enabled)
        self.context_compression_trigger_ratio = float(
            self.context_compression_trigger_ratio
        )
        if self.context_compression_trigger_ratio <= 0:
            raise ValueError("context_compression_trigger_ratio must be greater than 0")
        self.context_compression_file_read_count = int(
            self.context_compression_file_read_count
        )
        if self.context_compression_file_read_count < 0:
            raise ValueError(
                "context_compression_file_read_count must be greater than or equal to 0"
            )
        self.context_compression_file_read_max_chars = int(
            self.context_compression_file_read_max_chars
        )
        if self.context_compression_file_read_max_chars < 0:
            raise ValueError(
                "context_compression_file_read_max_chars must be greater than or equal to 0"
            )
        for name, cls in (
            ("runtime_durability", RuntimeDurabilityConfig),
            ("runtime_steering", RuntimeSteeringConfig),
            ("runtime_capacity", RuntimeCapacityConfig),
            ("runtime_approvals", RuntimeApprovalConfig),
            ("delegation", DelegationConfig),
            ("eternal_conversation", EternalConversationConfig),
            ("realtime_interaction", RealtimeInteractionConfig),
        ):
            value = getattr(self, name)
            if not isinstance(value, cls):
                setattr(self, name, cls(**value))
        for settings, field_name in (
            (self.runtime_durability, "history_path"),
            (self.runtime_capacity, "coordinator_path"),
        ):
            path = Path(getattr(settings, field_name)).expanduser()
            if not path.is_absolute():
                path = Path(self.pygent_root) / path
            setattr(settings, field_name, str(path.resolve()))
        self.mcp_servers = [
            item if isinstance(item, MCPServerConfig) else MCPServerConfig(**item)
            for item in self.mcp_servers
        ]

    def to_dict(self) -> dict[str, Any]:
        data = {
            item.name: _plain_dataclass_value(getattr(self, item.name))
            for item in fields(self)
            if item.name != "model_config"
        }
        data["resolved_agent"] = (
            None if self.resolved_agent is None else self.resolved_agent.safe_dict()
        )
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunConfig":
        clean = dict(data)
        resolved = clean.get("resolved_agent")
        if isinstance(resolved, dict):
            # Run settings persisted before retry moved from the agent binding
            # to model_retry still carry it inside resolved_agent.
            legacy = dict(resolved)
            retry = legacy.pop("retry", None)
            clean["resolved_agent"] = ResolvedAgentConfig(**legacy)
            if retry is not None and "model_retry" not in clean:
                clean["model_retry"] = retry
        return cls(**clean)


def _plain_dataclass_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _plain_dataclass_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, dict):
        return {key: _plain_dataclass_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_dataclass_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_plain_dataclass_value(item) for item in value)
    return value


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


