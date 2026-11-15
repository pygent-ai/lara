from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lara.tracing import EventStore

# Trace rows keep the full tool output; the activity list only ships a preview
# and the renderer loads the full text through /tool-results/{tool_call_id}.
RESULT_PREVIEW_CHARS = 2_000

# The session logs the feed is built from. Every run mirrors its tool, file and
# diff projections into ``<session>/logs/*.jsonl`` as the events are stored,
# so the feed is current while a turn runs and needs no run-directory walk.
TOOL_CALLS_LOG = "tool_calls.jsonl"
TOOL_RESULTS_LOG = "tool_results.jsonl"
FILE_EVENTS_LOG = "file_events.jsonl"
DIFF_EVENTS_LOG = "diff_events.jsonl"
ACTIVITY_LOGS = (TOOL_CALLS_LOG, TOOL_RESULTS_LOG, FILE_EVENTS_LOG, DIFF_EVENTS_LOG)

_CURSOR_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class ActivityCursor:
    """Byte offsets after the last complete row consumed from each session log.

    The logs are append-only, so a client that remembers this position can
    ask for "everything since" instead of re-reading the window it already
    holds. The encoding is opaque to clients.
    """

    offsets: dict[str, int]

    def encode(self) -> str:
        parts = ";".join(f"{name}={self.offsets.get(name, 0)}" for name in ACTIVITY_LOGS)
        return f"{_CURSOR_VERSION}:{parts}"

    @classmethod
    def decode(cls, value: str) -> ActivityCursor:
        version, separator, body = value.partition(":")
        if version != _CURSOR_VERSION or not separator:
            raise ValueError(f"unsupported activity cursor: {value!r}")
        offsets: dict[str, int] = {}
        for part in body.split(";"):
            name, equals, raw_offset = part.partition("=")
            if not equals or name not in ACTIVITY_LOGS or not raw_offset.isdigit():
                raise ValueError(f"malformed activity cursor: {value!r}")
            offsets[name] = int(raw_offset)
        if set(offsets) != set(ACTIVITY_LOGS):
            raise ValueError(f"incomplete activity cursor: {value!r}")
        return cls(offsets)


@dataclass(frozen=True, slots=True)
class ActivityPage:
    events: list[dict[str, Any]]
    events_total: int
    events_truncated: bool
    cursor: ActivityCursor
    tool_calls_total: int
    file_events_total: int


@dataclass(frozen=True, slots=True)
class _LogRead:
    records: list[dict[str, Any]]
    total: int
    end_offset: int


def session_activity_page(
    session_dir: Path,
    *,
    event_limit: int | None = None,
    after: ActivityCursor | None = None,
) -> ActivityPage:
    """Build the session-wide tool/file activity feed from the session logs.

    Without ``after`` the page is the latest ``event_limit`` rows (the
    initial window). With ``after`` it is every row appended since that
    cursor, unbounded: the caller polls at a fixed cadence while a turn runs,
    so a page covers a few seconds of activity, and bounding it would silently
    skip rows the cursor already moved past.

    Totals always count the whole logs, so the renderer can show how many
    tool calls the session made even when only a window of them is loaded.
    """

    logs_dir = Path(session_dir) / "logs"
    limit = event_limit if event_limit is not None and event_limit > 0 else None
    reads = {
        name: _read_log(
            logs_dir / name,
            start=after.offsets[name] if after is not None else 0,
            tail_limit=None if after is not None else limit,
        )
        for name in ACTIVITY_LOGS
    }
    events = _tool_events(reads[TOOL_CALLS_LOG].records, reads[TOOL_RESULTS_LOG].records)
    events.extend(_projection_events(reads[FILE_EVENTS_LOG].records, "file.unknown"))
    events.extend(_projection_events(reads[DIFF_EVENTS_LOG].records, "diff.created"))
    events.sort(key=lambda event: (str(event.get("created_at") or ""), str(event.get("id") or "")))
    events_total = sum(read.total for read in reads.values())
    if after is None and limit is not None and len(events) > limit:
        events = events[-limit:]
    return ActivityPage(
        events=events,
        events_total=events_total,
        events_truncated=after is None and len(events) < events_total,
        cursor=ActivityCursor({name: read.end_offset for name, read in reads.items()}),
        tool_calls_total=reads[TOOL_CALLS_LOG].total,
        file_events_total=reads[FILE_EVENTS_LOG].total + reads[DIFF_EVENTS_LOG].total,
    )


def _tool_events(
    tool_calls: list[dict[str, Any]], tool_results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    tool_names = _tool_names_by_call_id(tool_calls)
    events: list[dict[str, Any]] = []
    for record in tool_calls:
        call_id = _call_id(record)
        events.append(
            {
                "id": str(record.get("event_id") or call_id),
                "type": "tool.call",
                "created_at": record.get("created_at") or "",
                "case_run_id": record.get("case_run_id") or "",
                "payload": {
                    "tool_call_id": call_id,
                    "tool_name": str(record.get("tool_name") or "tool"),
                    "args": record.get("args"),
                    "created_at": record.get("created_at"),
                },
            }
        )
    for record in tool_results:
        call_id = _call_id(record)
        failed = record.get("error") is not None
        result = record.get("result")
        text = "" if result is None else str(result)
        payload: dict[str, Any] = {
            "tool_call_id": call_id,
            "status": str(record.get("status") or ("error" if failed else "success")),
            "result": text[:RESULT_PREVIEW_CHARS],
            "result_size": len(text.encode("utf-8")),
            "result_ref": call_id,
            "truncated": len(text) > RESULT_PREVIEW_CHARS,
            "created_at": record.get("created_at"),
        }
        if failed:
            payload["error"] = str(record.get("error"))
        tool_name = tool_names.get(call_id)
        if tool_name:
            payload["tool_name"] = tool_name
        events.append(
            {
                # A result row without an event id still needs a stable list key.
                "id": str(record.get("event_id") or f"{call_id}:result"),
                "type": "tool.result",
                "created_at": record.get("created_at") or "",
                "case_run_id": record.get("case_run_id") or "",
                "payload": payload,
            }
        )
    return events


def _projection_events(records: list[dict[str, Any]], fallback_type: str) -> list[dict[str, Any]]:
    return [
        {
            "id": str(record.get("event_id") or ""),
            "type": str(record.get("type") or fallback_type),
            "created_at": record.get("created_at") or "",
            "case_run_id": record.get("case_run_id") or "",
            "payload": record,
        }
        for record in records
    ]


def _call_id(record: dict[str, Any]) -> str:
    return str(
        record.get("model_tool_call_id")
        or record.get("tool_call_id")
        or record.get("event_id")
        or ""
    )


def _tool_names_by_call_id(records: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for record in records:
        tool_name = str(record.get("tool_name") or "")
        if not tool_name:
            continue
        for key in ("model_tool_call_id", "tool_call_id", "event_id"):
            call_id = str(record.get(key) or "")
            if call_id:
                names.setdefault(call_id, tool_name)
    return names


def _read_log(path: Path, *, start: int, tail_limit: int | None) -> _LogRead:
    """Read the complete rows of an append-only log from ``start``.

    ``total`` counts every row in the file (rows before ``start`` included),
    ``end_offset`` is the position after the last row that ended with a
    newline: a row still being written is left for the next read. With
    ``tail_limit`` only the latest rows are decoded; counting never decodes.
    """

    if not path.exists():
        if start != 0:
            raise ValueError(f"activity cursor is past the end of {path.name}")
        return _LogRead([], 0, 0)
    with path.open("rb") as handle:
        head = handle.read(start)
        if len(head) < start:
            raise ValueError(f"activity cursor is past the end of {path.name}")
        data = handle.read()
    rows_before = sum(1 for line in head.split(b"\n") if line.strip())
    consumed = data.rfind(b"\n") + 1
    lines = [line for line in data[:consumed].split(b"\n") if line.strip()]
    recent: deque[bytes] = deque(lines, maxlen=tail_limit) if tail_limit else deque(lines)
    records: list[dict[str, Any]] = []
    invalid_recent = 0
    for line in recent:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            invalid_recent += 1
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            invalid_recent += 1
    return _LogRead(records, rows_before + len(lines) - invalid_recent, start + consumed)


def recent_jsonl_records(
    path: Path, limit: int | None
) -> tuple[list[dict[str, Any]], int]:
    """Return a bounded JSONL tail while counting rows without decoding all of it."""
    if not path.exists():
        return [], 0
    if limit is None or limit <= 0:
        records = list(EventStore.iter_jsonl(path) or [])
        return records, len(records)
    read = _read_log(path, start=0, tail_limit=limit)
    return read.records, read.total
