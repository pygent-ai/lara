from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from lara_api.dependencies import ApiContext, get_api_context
from lara_api.models.requests import (
    ChatCancelRequest,
    ChatMessageRequest,
    ChatTurnRequest,
    ToolApprovalRequest,
)
from lara_api.services.chat_runner import stream_chat_turn

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/stream")
def stream_turn(
    request: ChatTurnRequest,
    context: ApiContext = Depends(get_api_context),
) -> StreamingResponse:
    return StreamingResponse(stream_chat_turn(context, request), media_type="text/event-stream")


@router.post("/approvals/{approval_id}")
async def deliver_approval(
    approval_id: str,
    request: ToolApprovalRequest,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    try:
        delivered = await context.chat_registry.deliver_approval(
            context,
            approval_id,
            approved=request.approved,
            comment=request.comment,
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"approval_id": approval_id, "delivered": delivered}


@router.post("/sessions/{session_id}/messages")
async def deliver_message(
    session_id: str,
    request: ChatMessageRequest,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    """Deliver a user message to a session that is already streaming a turn.

    The host routes it: the realtime session answers through a fork reply
    (returned here); a plain session steers its running turn; a session whose
    turn cannot take the input queues a turn behind the running one.
    """

    try:
        return await context.chat_registry.deliver_message(
            context,
            session_id=session_id,
            scope_id=request.scope_id,
            input_id=request.input_id,
            message=request.message or "",
            attachments=request.attachments,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(
    execution_id: str,
    request: ChatCancelRequest,
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    try:
        cancelled = await context.chat_registry.cancel_turn(
            context, execution_id, session_id=request.session_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not cancelled:
        raise HTTPException(status_code=409, detail="当前执行已结束，无需取消。")
    return {"execution_id": execution_id, "cancelled": True}
