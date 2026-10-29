from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from tzlocal import get_localzone_name


class DispatchTaskStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class DispatchDestination(StrEnum):
    STANDALONE = "standalone"
    HEARTBEAT = "heartbeat"


class DispatchItemKind(StrEnum):
    TURN = "turn"
    START = "start"
    FORK = "fork"


class DispatchItemState(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            DispatchItemState.PASSED,
            DispatchItemState.FAILED,
            DispatchItemState.ERROR,
            DispatchItemState.SKIPPED,
            DispatchItemState.CANCELLED,
        }


@dataclass(frozen=True, slots=True)
class DispatchTask:
    task_id: str
    name: str
    prompt: str
    workspace_root: str
    destination: str
    timezone_name: str
    status: str
    created_at: str
    updated_at: str
    target_session_id: str | None = None
    at_time: str | None = None
    rrule: str | None = None
    last_run_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DispatchItem:
    item_id: str
    kind: str
    workspace_root: str
    agent_alias: str
    case_id: str
    message: str
    state: DispatchItemState
    created_at: str
    updated_at: str
    # Task-defined turn items carry their task; start/fork submissions are NULL.
    task_id: str | None = None
    # Session the item runs in: heartbeat targets and fork sources are recorded
    # at submission, standalone turns attach the created session at execution.
    target_session_id: str | None = None
    source_session_id: str | None = None
    model_group: str | None = None
    submission_id: str | None = None
    fingerprint: str | None = None
    owner_id: str | None = None
    lease_until_epoch: float | None = None
    execution_id: str | None = None
    case_run_id: str | None = None
    final_answer: str = ""
    error: str | None = None

    @property
    def done(self) -> bool:
        return self.state.terminal

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class DispatchOperationView:
    """Collaboration-operation-shaped projection of a dispatch item.

    Agent tools and the CLI keep consuming operation JSON (operation_id,
    status, ...) while the durable record lives in dispatch_items.
    """

    operation_id: str
    submission_id: str | None
    source_session_id: str | None
    target_session_id: str | None
    agent_alias: str
    case_id: str
    message: str
    state: DispatchItemState
    created_at: str
    updated_at: str
    execution_id: str | None = None
    case_run_id: str | None = None
    final_answer: str = ""
    error: str | None = None
    model_group: str | None = None

    @classmethod
    def from_item(cls, item: DispatchItem) -> "DispatchOperationView":
        return cls(
            operation_id=item.item_id,
            submission_id=item.submission_id,
            source_session_id=item.source_session_id,
            target_session_id=item.target_session_id,
            agent_alias=item.agent_alias,
            case_id=item.case_id,
            message=item.message,
            state=item.state,
            created_at=item.created_at,
            updated_at=item.updated_at,
            execution_id=item.execution_id,
            case_run_id=item.case_run_id,
            final_answer=item.final_answer,
            error=item.error,
            model_group=item.model_group,
        )

    @property
    def done(self) -> bool:
        return self.state.terminal

    def to_dict(self, *, include_message: bool = False) -> dict[str, object]:
        value: dict[str, object] = {
            "operation_id": self.operation_id,
            "submission_id": self.submission_id,
            "source_session_id": self.source_session_id,
            "target_session_id": self.target_session_id,
            "agent_alias": self.agent_alias,
            "case_id": self.case_id,
            "status": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "execution_id": self.execution_id,
            "case_run_id": self.case_run_id,
            "final_answer": self.final_answer,
            "error": self.error,
            "model_group": self.model_group,
        }
        if include_message:
            value["message"] = self.message
        return value


def normalize_timezone(value: str | None) -> str:
    name = (value or _local_timezone_name()).strip()
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {name}") from exc
    return name


def normalize_rrule(value: str) -> str:
    rule = value.strip()
    if rule.upper().startswith("RRULE:"):
        rule = rule[6:]
    if not rule:
        raise ValueError("rrule must be non-empty")
    return rule.upper()


def parse_at_time(value: str, timezone_name: str) -> str:
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.astimezone(UTC).isoformat()


def next_occurrence(
    *, rrule: str, timezone_name: str, dtstart: str, after: str
) -> str | None:
    zone = ZoneInfo(timezone_name)
    start = datetime.fromisoformat(dtstart).astimezone(zone)
    boundary = datetime.fromisoformat(after).astimezone(zone)
    recurrence = rrulestr(normalize_rrule(rrule), dtstart=start)
    value = recurrence.after(boundary, inc=False)
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return value.astimezone(UTC).isoformat()


def _local_timezone_name() -> str:
    try:
        return get_localzone_name()
    except Exception:  # noqa: BLE001 - platform timezone discovery is best effort
        local = datetime.now().astimezone().tzinfo
        return getattr(local, "key", None) or "UTC"


__all__ = [
    "DispatchDestination",
    "DispatchItem",
    "DispatchItemKind",
    "DispatchItemState",
    "DispatchOperationView",
    "DispatchTask",
    "DispatchTaskStatus",
    "next_occurrence",
    "normalize_rrule",
    "normalize_timezone",
    "parse_at_time",
    "utc_now",
]
