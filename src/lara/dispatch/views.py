from __future__ import annotations

from typing import Any

from .models import DispatchItem, DispatchTask
from .store import DispatchStore


def automation_payload(
    task: DispatchTask, store: DispatchStore
) -> dict[str, Any]:
    """Map a dispatch task onto the automation JSON shape the desktop expects.

    The HTTP /automations routes and the ``lara automation`` CLI both emit
    this shape, so ScheduledPage and scripts keep working unchanged.
    """
    return {
        "automation_id": task.task_id,
        "name": task.name,
        "prompt": task.prompt,
        "workspace_root": task.workspace_root,
        "destination": task.destination,
        "target_session_id": task.target_session_id,
        "timezone": task.timezone_name,
        "at_time": task.at_time,
        "rrule": task.rrule,
        "dtstart": task.created_at,
        "status": task.status,
        "next_run_at": store.next_run_at(task),
        "last_run_at": store.last_run_at(task),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def run_payload(item: DispatchItem) -> dict[str, Any]:
    return {
        "run_id": item.item_id,
        "automation_id": item.task_id,
        "scheduled_for": item.created_at,
        "trigger": (
            "schedule"
            if (item.submission_id or "").startswith("schedule:")
            else "manual"
        ),
        "status": item.state.value,
        "session_id": item.target_session_id,
        "case_run_id": item.case_run_id,
        "execution_id": item.execution_id,
        "final_answer": item.final_answer,
        "error": item.error,
        "started_at": None,
        "finished_at": item.updated_at if item.state.terminal else None,
        "created_at": item.created_at,
    }


__all__ = ["automation_payload", "run_payload"]
