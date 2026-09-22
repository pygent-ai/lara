from __future__ import annotations

from pathlib import Path
from typing import Any

from lara.tracing import EventStore

# Trace rows keep the full tool output; the activity list only ships a preview
# and the renderer loads the full text through /tool-results/{tool_call_id}.
RESULT_PREVIEW_CHARS = 2_000


def session_activity_events(session_dir: Path) -> tuple[list[dict[str, Any]], int]:
    """Build the session-wide tool/file activity feed from the session logs.

    Every run mirrors its tool, file, and diff projections into
    ``<session>/logs/*.jsonl`` when the events are stored, so reading those
    files yields the full history without walking the run directories.

    Returns the events in chronological order together with the untruncated
    total count.
    """

    logs_dir = Path(session_dir) / "logs"
    tool_names = _tool_names_by_call_id(logs_dir / "tool_calls.jsonl")
    events: list[dict[str, Any]] = []
    for record in EventStore.iter_jsonl(logs_dir / "tool_calls.jsonl") or []:
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
    for record in EventStore.iter_jsonl(logs_dir / "tool_results.jsonl") or []:
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
    for name, fallback_type in (("file_events.jsonl", "file.unknown"), ("diff_events.jsonl", "diff.created")):
        for record in EventStore.iter_jsonl(logs_dir / name) or []:
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
    return events, len(events)


def _call_id(record: dict[str, Any]) -> str:
    return str(
        record.get("model_tool_call_id")
        or record.get("tool_call_id")
        or record.get("event_id")
        or ""
    )


def _tool_names_by_call_id(path: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    for record in EventStore.iter_jsonl(path) or []:
        tool_name = str(record.get("tool_name") or "")
        if not tool_name:
            continue
        for key in ("model_tool_call_id", "tool_call_id", "event_id"):
            call_id = str(record.get(key) or "")
            if call_id:
                names.setdefault(call_id, tool_name)
    return names
