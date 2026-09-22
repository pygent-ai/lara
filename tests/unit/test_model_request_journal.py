from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from lara.runtime.model_request_journal import ModelRequestJournal

EXECUTION_ID = "b5d2b414-3e39-4e5a-b922-3539fb6cf6db"
OTHER_EXECUTION_ID = "00000000-0000-0000-0000-000000000000"


def _prepared_event(
    event_id: str,
    *,
    messages: list[dict[str, str]] | None = None,
    current_message: dict[str, str] | None = None,
    system_prompt: str = "You are Lara.",
    projection_revision: int = 1,
) -> dict:
    return {
        "kind": "model.request.prepared",
        "event_id": event_id,
        "sequence": 1,
        "timestamp_unix_ns": 1789868736413033900,
        "data": {
            "request": {
                "system_prompt": system_prompt,
                "messages": messages if messages is not None else [{"role": "user", "content": "hi"}],
                "current_message": current_message,
                "tools": [{"name": "bash", "description": "run", "parameters": {}}],
                "projection_revision": projection_revision,
            }
        },
    }


def _seed_journal(path: Path, rows: list[tuple[str, int, dict]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE events (
                execution_id TEXT NOT NULL,
                event_index INTEGER NOT NULL,
                event_json TEXT NOT NULL,
                PRIMARY KEY(execution_id, event_index)
            )
            """
        )
        connection.executemany(
            "INSERT INTO events (execution_id, event_index, event_json) VALUES (?, ?, ?)",
            [
                (execution_id, index, json.dumps(event, ensure_ascii=False))
                for execution_id, index, event in rows
            ],
        )


def test_list_maps_prepared_events_into_request_snapshots(tmp_path: Path) -> None:
    journal_path = tmp_path / "runtime" / "executions-v1.sqlite3"
    _seed_journal(
        journal_path,
        [
            (
                EXECUTION_ID,
                0,
                _prepared_event(
                    "event-0",
                    current_message={"role": "user", "content": "next"},
                    projection_revision=3,
                ),
            ),
        ],
    )

    snapshots = ModelRequestJournal(journal_path).list(EXECUTION_ID)

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["snapshot_id"] == "request-event-0"
    assert snapshot["phase"] == "request"
    assert snapshot["system_prompt"] == "You are Lara."
    assert [message["role"] for message in snapshot["messages"]] == ["user", "user"]
    assert snapshot["message_count"] == 2
    assert snapshot["tool_count"] == 1
    assert snapshot["projection_revision"] == 3
    assert snapshot["compression_version"] == 0
    assert snapshot["captured_at"] == "2026-09-20T01:45:36.413034+00:00"


def test_list_orders_by_event_index_and_filters_other_data(tmp_path: Path) -> None:
    journal_path = tmp_path / "runtime" / "executions-v1.sqlite3"
    _seed_journal(
        journal_path,
        [
            (EXECUTION_ID, 2, _prepared_event("event-2")),
            (OTHER_EXECUTION_ID, 0, _prepared_event("event-other")),
            (
                EXECUTION_ID,
                1,
                {"kind": "model.reasoning.delta", "event_id": "event-delta", "data": {}},
            ),
            (EXECUTION_ID, 0, _prepared_event("event-0")),
        ],
    )

    snapshots = ModelRequestJournal(journal_path).list(EXECUTION_ID)

    assert [snapshot["snapshot_id"] for snapshot in snapshots] == [
        "request-event-0",
        "request-event-2",
    ]


def test_list_returns_empty_for_missing_journal_or_execution(tmp_path: Path) -> None:
    missing = tmp_path / "runtime" / "executions-v1.sqlite3"
    assert ModelRequestJournal(missing).list(EXECUTION_ID) == []

    journal_path = tmp_path / "runtime" / "journal.sqlite3"
    _seed_journal(journal_path, [(EXECUTION_ID, 0, _prepared_event("event-0"))])
    journal = ModelRequestJournal(journal_path)

    assert journal.list(OTHER_EXECUTION_ID) == []
    assert journal.list("") == []
