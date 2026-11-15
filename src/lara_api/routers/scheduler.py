"""`/scheduler`: the HTTP face of the SDK scheduler for this workspace.

Writes are signed `user` submissions that the daemon promotes on its next
poll (the response is the submission, not the task). Reads come straight from
the SDK task/run tables.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lara.cli.scheduler import submit_user, task_payload
from lara_api.container import ApiContext
from lara_api.dependencies import get_api_context

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


class TaskCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    schedule: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    instructions: str = ""
    session: str | None = None
    priority: Literal["high", "normal", "low"] = "normal"
    delivery: Literal["sync-reply", "speak", "fire-and-forget"] | None = None


class EventRequest(BaseModel):
    event: str = Field(min_length=1)
    data: dict[str, object] = Field(default_factory=dict)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/tasks")
def list_tasks(
    status: str | None = None, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    store = context.scheduler_store
    return {"tasks": [task.to_dict() for task in store.list_tasks(status=status)]}


@router.post("/tasks", status_code=202)
def create_task(
    request: TaskCreateRequest, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    try:
        payload = task_payload(
            name=request.name,
            schedule=request.schedule,
            prompt=request.prompt,
            instructions=request.instructions,
            session=request.session,
            priority=request.priority,
            delivery=request.delivery,
            default_session=context.config.scheduler.session,
        )
        return submit_user(context.config, kind="task", payload=payload)
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/tasks/{task_id}")
def get_task(
    task_id: str, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    store = context.scheduler_store
    task = store.task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"unknown task {task_id!r}")
    return {
        "task": task.to_dict(),
        "runs": [run.to_dict() for run in store.list_runs(task_id=task_id)],
    }


@router.post("/tasks/{task_id}/{action}", status_code=202)
def control_task(
    task_id: str,
    action: Literal["approve", "reject", "pause", "resume", "delete"],
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    if context.scheduler_store.task(task_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown task {task_id!r}")
    try:
        return submit_user(
            context.config,
            kind="control",
            payload={"action": action, "task_id": task_id},
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/runs")
def list_runs(
    task_id: str | None = None, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    store = context.scheduler_store
    return {"runs": [run.to_dict() for run in store.list_runs(task_id=task_id)]}


@router.post("/events", status_code=202)
def emit_event(
    request: EventRequest, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    try:
        return submit_user(
            context.config,
            kind="event",
            payload={"event": request.event, "data": request.data},
        )
    except Exception as exc:
        raise _error(exc) from exc


__all__ = ["router"]
