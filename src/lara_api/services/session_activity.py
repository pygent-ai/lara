from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

from lara.tracing import EventStore

# Trace rows keep the full tool output; the activity list only ships a preview
# and the renderer loads the full text through /tool-results/{tool_call_id}.
RESULT_PREVIEW_CHARS = 2_000


def session_activity_events(
    session_dir: Path, *, event_limit: int | None = None
) -> tuple[list[dict[str, Any]], int]:
    """Build the session-wide tool/file activity feed from the session logs.

    Every run mirrors its tool, file, and diff projections into
    ``<session>/logs/*.jsonl`` when the events are stored, so reading those
    files yields the full history without walking the run directories.

    Returns the events in chronological order together with the untruncated
    total count.
    """

    logs_dir = Path(session_dir) / "logs"
    limit = event_limit if event_limit is not None and event_limit > 0 else None
    tool_calls, tool_calls_total = recent_jsonl_records(
        logs_dir / "tool_calls.jsonl", limit
    )
    tool_results, tool_results_total = recent_jsonl_records(
        logs_dir / "tool_results.jsonl", limit
    )
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
    events_total = tool_calls_total + tool_results_total
    for name, fallback_type in (("file_events.jsonl", "file.unknown"), ("diff_events.jsonl", "diff.created")):
        records, records_total = recent_jsonl_records(logs_dir / name, limit)
        events_total += records_total
        for record in records:
            events.append(
                {
                    "id": str(record.get("event_id") or ""),
                    "type": str(record.get("type") or fallback_type),
                    "created_at": record.get("created_at") or "",
                    "case_run_id": record.get("case_run_id") or "",
                    "payload": record,
                }
            )
    events.sort(key=lambda event: (str(event.get("created_at") or ""), str(event.get("id") or "")))
    if limit is not None and len(events) > limit:
        events = events[-limit:]
    return events, events_total


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


def recent_jsonl_records(
    path: Path, limit: int | None
) -> tuple[list[dict[str, Any]], int]:
    """Return a bounded JSONL tail while counting rows without decoding all of it.

    Each source log is append-only.  Keeping the latest ``limit`` raw rows from
    every source is sufficient to find the latest ``limit`` rows after the
    sources are merged, and avoids constructing every historical event.
    """
    if not path.exists():
        return [], 0
    if limit is None or limit <= 0:
        records = list(EventStore.iter_jsonl(path) or [])
        return records, len(records)

    recent: deque[bytes] = deque(maxlen=limit)
    total = 0
    with path.open("rb") as handle:
        for line in handle:
            if not line.strip():
                continue
            total += 1
            recent.append(line)

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
    return records, max(0, total - invalid_recent)
