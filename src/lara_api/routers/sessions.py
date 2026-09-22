from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from lara_api.dependencies import ApiContext, get_api_context
from lara_api.models.requests import CreateSessionRequest, ReorderSessionsRequest, UpdateSessionModelRequest
from lara_api.models.responses import (
    DeleteResponse,
    SessionDetailResponse,
    SessionGroupListResponse,
    SessionListResponse,
    SessionRecordResponse,
)
from lara_api.services.session_service import SessionService, session_groups_etag, session_groups_response, session_service_for_scope

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse)
def list_sessions(context: ApiContext = Depends(get_api_context)) -> SessionListResponse:
    return SessionListResponse(sessions=SessionService(context.manager).list_chat_sessions())


@router.get(
    "/groups",
    response_model=SessionGroupListResponse,
    responses={304: {"description": "Groups unchanged since the previous ETag"}},
)
def list_session_groups(
    response: Response,
    if_none_match: Annotated[str | None, Header()] = None,
    context: ApiContext = Depends(get_api_context),
) -> SessionGroupListResponse | Response:
    """Serve the sidebar groups, or 304 when nothing behind the ETag changed.

    The ETag is a stat-only fingerprint, so unchanged polls skip the full
    metadata rebuild and the renderer skips re-rendering the sidebar.
    """
    etag = f'"{session_groups_etag(context)}"'
    if if_none_match and _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return session_groups_response(context)


def _etag_matches(header_value: str, etag: str) -> bool:
    candidates = {item.strip() for item in header_value.split(",")}
    return "*" in candidates or etag in candidates


@router.patch("/groups/order", response_model=SessionGroupListResponse)
def reorder_session_groups(
    request: ReorderSessionsRequest,
    context: ApiContext = Depends(get_api_context),
) -> SessionGroupListResponse:
    context.project_state.reorder_sessions(request.scope_id, request.ordered_session_ids)
    return session_groups_response(context)


@router.post("", response_model=SessionRecordResponse)
async def create_session(
    request: CreateSessionRequest,
    context: ApiContext = Depends(get_api_context),
) -> SessionRecordResponse:
    try:
        return session_service_for_scope(
            context, request.scope_id, with_reminders=True
        ).create_session(
            case_id=request.case_id,
            mode=request.mode,
            model_group_name=request.model_group_name,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{session_id}/model", response_model=SessionRecordResponse)
async def update_session_model(
    session_id: str,
    request: UpdateSessionModelRequest,
    scope_id: str | None = None,
    context: ApiContext = Depends(get_api_context),
) -> SessionRecordResponse:
    service = session_service_for_scope(context, scope_id)
    if await context.session_coordinator.session_busy(service.manager, session_id):
        raise HTTPException(
            status_code=409,
            detail="Session model cannot change while an execution is active",
        )
    try:
        return service.update_selected_model(
            session_id, request.selected_model_key
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{session_id}", response_model=SessionDetailResponse)
def get_session(
    session_id: str,
    scope_id: str | None = None,
    history_limit: int | None = None,
    context: ApiContext = Depends(get_api_context),
) -> SessionDetailResponse:
    try:
        return session_service_for_scope(context, scope_id).load_detail(
            session_id,
            history_limit=history_limit,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id!r} is unavailable in the current workspace",
        ) from exc


@router.delete("/{session_id}", response_model=DeleteResponse)
def delete_session(
    session_id: str,
    scope_id: str | None = None,
    context: ApiContext = Depends(get_api_context),
) -> DeleteResponse:
    return DeleteResponse(
        deleted=session_service_for_scope(context, scope_id).delete_session(session_id)
    )
