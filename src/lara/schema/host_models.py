"""Host-only schema models.

These classes live with the lora host layer (evaluation / case-run / CLI);
every kernel schema type comes from :mod:`pygent.lara.schema.models`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pygent.lara.schema.models import CaseRunStatus, SessionRef as KernelSessionRef, _abs_path, _require

__all__ = [
    "CaseDefinition",
    "CaseRunResult",
    "EvaluationResult",
    "SessionRef",
    "WorkspaceRef",
    "status_resumable",
]


def status_resumable(status: CaseRunStatus | None) -> bool:
    """Whether a finished run may be continued by a fresh turn.

    The kernel's CaseRunStatus carries no resumable set; the host's session
    recovery policy owns it (these runs ended before the user got their
    outcome).
    """

    return status in {CaseRunStatus.FAILED, CaseRunStatus.ERROR, CaseRunStatus.SKIPPED}


class SessionRef(KernelSessionRef):
    """Host round-trip: the kernel SessionRef ships without ``from_dict``."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionRef":
        return cls(**data)


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
