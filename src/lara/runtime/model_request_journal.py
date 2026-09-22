from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ModelRequestJournal:
    """Per-model-call request snapshots read from the pygent durable journal.

    The journal (executions-v1.sqlite3) records every prepared model request
    with its full message context, so the Trace inspector reads snapshots from
    there instead of Lara keeping a second copy on disk.
    """

    def __init__(self, journal_path: str | Path):
        self.path = Path(journal_path)

    def list(self, execution_id: str) -> list[dict[str, Any]]:
        if not execution_id or not self.path.exists():
            return []
        query = (
            "SELECT event_json FROM events "
            "WHERE execution_id = ? "
            "AND json_extract(event_json, '$.kind') = 'model.request.prepared' "
            "ORDER BY event_index"
        )
        with closing(
            sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        ) as connection:
            rows = connection.execute(query, (execution_id,)).fetchall()
        return [_snapshot_from_event(json.loads(row[0])) for row in rows]


def _snapshot_from_event(event: dict[str, Any]) -> dict[str, Any]:
    request = (event.get("data") or {}).get("request") or {}
    messages = list(request.get("messages") or [])
    current_message = request.get("current_message")
    if current_message is not None:
        messages.append(current_message)
    tools = request.get("tools") or []
    return {
        "snapshot_id": f"request-{event.get('event_id') or event.get('sequence')}",
        "phase": "request",
        "system_prompt": request.get("system_prompt") or "",
        "messages": messages,
        "message_count": len(messages),
        "tools": tools,
        "tool_count": len(tools),
        "compression_version": 0,
        "projection_revision": int(request.get("projection_revision") or 0),
        "captured_at": _iso_timestamp(event.get("timestamp_unix_ns")),
    }


def _iso_timestamp(timestamp_unix_ns: Any) -> str:
    if not isinstance(timestamp_unix_ns, int) or timestamp_unix_ns <= 0:
        return ""
    return datetime.fromtimestamp(timestamp_unix_ns / 1_000_000_000, tz=UTC).isoformat()


__all__ = ["ModelRequestJournal"]
