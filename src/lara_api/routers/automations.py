from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from lara.dispatch import automation_payload, run_payload
from lara_api.container import ApiContext
from lara_api.dependencies import get_api_context
from lara_api.models.automations import (
    AutomationCreateRequest,
    AutomationUpdateRequest,
)

router = APIRouter(prefix="/automations", tags=["automations"])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("")
def list_automations(
    status: str | None = None, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    try:
        store = context.dispatch_store
        return {
            "automations": [
                automation_payload(item, store) for item in store.list(status=status)
            ]
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.post("", status_code=201)
def create_automation(
    request: AutomationCreateRequest,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    try:
        values = request.model_dump()
        values["timezone_name"] = values.pop("timezone")
        task = context.dispatch_service.create(**values)
        return automation_payload(task, context.dispatch_store)
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/{automation_id}")
def get_automation(
    automation_id: str, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    try:
        store = context.dispatch_store
        return automation_payload(store.get(automation_id), store)
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/{automation_id}")
def update_automation(
    automation_id: str,
    request: AutomationUpdateRequest,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    try:
        task = context.dispatch_service.update(
            automation_id, **request.model_dump(exclude_unset=True)
        )
        return automation_payload(task, context.dispatch_store)
    except Exception as exc:
        raise _error(exc) from exc


@router.delete("/{automation_id}")
def delete_automation(
    automation_id: str, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    return {"deleted": context.dispatch_store.delete(automation_id)}


@router.post("/{automation_id}/pause")
def pause_automation(
    automation_id: str, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    try:
        task = context.dispatch_service.pause(automation_id)
        return automation_payload(task, context.dispatch_store)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{automation_id}/resume")
def resume_automation(
    automation_id: str, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    try:
        task = context.dispatch_service.resume(automation_id)
        return automation_payload(task, context.dispatch_store)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{automation_id}/run")
def run_automation(
    automation_id: str, context: ApiContext = Depends(get_api_context)
) -> dict[str, object]:
    try:
        return run_payload(context.dispatch_store.request_run(automation_id))
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/{automation_id}/runs")
def list_automation_runs(
    automation_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    try:
        return {
            "runs": [
                run_payload(item)
                for item in context.dispatch_store.list_items(
                    automation_id, limit=limit
                )
            ]
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{automation_id}/runs/{run_id}/cancel")
async def cancel_automation_run(
    automation_id: str,
    run_id: str,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    try:
        automation = context.dispatch_store.get(automation_id)
        item = context.dispatch_store.get_item(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if item.task_id != automation.task_id:
        raise HTTPException(status_code=404, detail=f"automation run not found: {run_id}")
    if not await context.dispatch_scheduler.cancel_run(run_id):
        raise HTTPException(
            status_code=409, detail="该运行未在本进程内执行，无法取消。"
        )
    return run_payload(context.dispatch_store.get_item(run_id))


__all__ = ["router"]
