"""Reconciliation must not stamp a live fork reply as a dead run.

Fork replies run outside the coordinator and never record their execution id,
so the liveness probes cannot see them; a run inside the fork's startup window
is therefore untouchable, and only age makes a run sweepable again.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lara.core.io import read_json, write_json
from lara.schema import CaseRunStatus
from lara.sessions import SessionManager
from lara_api.container import ApiContext
from lara_api.models.responses import SessionRecordResponse
from lara_api.services.chat_runner import _should_finalize_on_resume_miss
from lara_api.services.session_service import (
    fork_startup_grace_seconds,
    reconcile_stale_running_sessions,
)
from tests.unit.test_session_manager import native_run_config


def _running_record(
    session_id: str, case_run_id: str, tmp: Path
) -> SessionRecordResponse:
    now = datetime.now(timezone.utc).isoformat()
    return SessionRecordResponse(
        session_id=session_id,
        session_dir=str(tmp),
        case_id="chat",
        mode="chat",
        created_at=now,
        updated_at=now,
        title="reconcile",
        last_case_run_id=case_run_id,
        last_case_run_status="running",
    )


def _age_run(manager: SessionManager, run, minutes: float) -> None:
    path = Path(run.run_dir) / "run_metadata.json"
    started = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    write_json(path, {**read_json(path, default={}), "started_at": started.isoformat()})


def test_fork_startup_grace_covers_both_attempt_slots(tmp_path: Path) -> None:
    manager = SessionManager(native_run_config(tmp_path))

    assert manager.config.realtime_interaction.fork_deadline_seconds == 60.0
    assert fork_startup_grace_seconds(manager) == 125.0


def test_reconcile_leaves_a_run_inside_the_fork_window_running(tmp_path: Path) -> None:
    manager = SessionManager(native_run_config(tmp_path))
    session = manager.create("chat", mode="chat")
    run = manager.start_case_run(session.session_id, "chat")
    record = _running_record(session.session_id, run.case_run_id, tmp_path)

    context = ApiContext(
        workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json")
    )
    asyncio.run(reconcile_stale_running_sessions(context, manager, [record]))

    assert manager.run_timing(run)["status"] == "running"
    assert record.last_case_run_status == "running"


def test_reconcile_finalizes_an_aged_dead_run(tmp_path: Path) -> None:
    manager = SessionManager(native_run_config(tmp_path))
    session = manager.create("chat", mode="chat")
    run = manager.start_case_run(session.session_id, "chat")
    _age_run(manager, run, minutes=10)
    record = _running_record(session.session_id, run.case_run_id, tmp_path)

    context = ApiContext(
        workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json")
    )
    asyncio.run(reconcile_stale_running_sessions(context, manager, [record]))

    assert manager.run_timing(run)["status"] == "error"
    assert record.last_case_run_status == CaseRunStatus.ERROR


def test_resume_miss_spares_a_fresh_run_and_finalizes_an_aged_one(
    tmp_path: Path,
) -> None:
    manager = SessionManager(native_run_config(tmp_path))
    session = manager.create("chat", mode="chat")
    run = manager.start_case_run(session.session_id, "chat")

    assert _should_finalize_on_resume_miss(manager, run) is False

    _age_run(manager, run, minutes=10)
    assert _should_finalize_on_resume_miss(manager, run) is True
