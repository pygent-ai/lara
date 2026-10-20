from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateSessionRequest(BaseModel):
    case_id: str = "chat"
    mode: str = "chat"
    scope_id: str | None = None
    model_group_name: str | None = None


class UpdateSessionModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    selected_model_key: str = Field(min_length=1)


class ForkSessionRequest(BaseModel):
    """Fork whole-session when omitted, or at one checkpoint boundary."""

    at_sequence: int | None = Field(default=None, ge=1)


class ReorderProjectsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ordered_scope_ids: list[str] = Field(default_factory=list)


class ReorderSessionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    scope_id: str = Field(min_length=1)
    ordered_session_ids: list[str] = Field(default_factory=list)


class PinSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    pinned: bool
    scope_id: str | None = None


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str | None = Field(default=None, min_length=1)
    session_id: str | None = None
    scope_id: str | None = None
    case_id: str = "chat"
    model_group_name: str | None = None
    turn_id: str | None = None
    execution_id: str | None = None
    after_sequence: int | None = None
    log_model_text_deltas: bool = False
    attachments: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_start_or_resume(self) -> "ChatTurnRequest":
        if self.after_sequence is not None and self.execution_id is None:
            raise ValueError("after_sequence requires execution_id")
        if self.execution_id is not None:
            if self.message is not None or self.attachments:
                raise ValueError("resume accepts neither message nor attachments")
        elif self.message is None and not self.attachments:
            raise ValueError("provide message or attachments when not resuming")
        return self


class ChatSteeringRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    session_id: str = Field(min_length=1)
    input_id: str = Field(min_length=1, max_length=128)
    message: str | None = Field(default=None, min_length=1)
    attachments: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_message_or_attachments(self) -> "ChatSteeringRequest":
        if not self.message and not self.attachments:
            raise ValueError("provide message or attachments")
        return self

class ChatCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    session_id: str = Field(min_length=1)


class ModelRetrySettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts_per_model: int = Field(default=2, ge=1)
    attempt_idle_timeout_seconds: float = Field(default=60.0, gt=0)
    backoff_initial: float = Field(default=0.5, ge=0)
    backoff_maximum: float = Field(default=4.0, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1)

    @model_validator(mode="after")
    def validate_backoff(self) -> "ModelRetrySettingsRequest":
        if self.backoff_initial > self.backoff_maximum:
            raise ValueError("backoff_initial must not exceed backoff_maximum")
        return self


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    approvals_enabled: bool | None = None
    workspace_root: str | None = None
    agent_alias: str | None = None
    max_steps: int | None = None
    context_window: int | None = None
    native_model_config: dict[str, Any] | None = Field(
        default=None, alias="model_config"
    )
    default_model_group: str | None = None
    retry: ModelRetrySettingsRequest | None = None
    credential_values: dict[str, str] = Field(default_factory=dict)


class DiscoverModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    protocol: str = Field(min_length=1)
    connection: dict[str, Any]
    credential_value: str | None = None
    timeout_seconds: float = Field(default=10.0, gt=0, le=60.0)


class ToolApprovalRequest(BaseModel):
    approved: bool
    comment: str = ""


class TerminalCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_id: str = Field(min_length=1)
    command: str = Field(min_length=1, max_length=32_000)


class TerminalResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_id: str = Field(min_length=1)
