from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from lara_api.dependencies import ApiContext, get_api_context
from lara_api.models.responses import ToolResultResponse
from lara_api.services.tool_results import find_tool_result

router = APIRouter(prefix="/tool-results", tags=["tool-results"])


@router.get("/{tool_call_id}", response_model=ToolResultResponse)
def get_tool_result(
    tool_call_id: str,
    context: ApiContext = Depends(get_api_context),
) -> ToolResultResponse:
    result = find_tool_result(context, tool_call_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Tool result {tool_call_id!r} was not found")
    return ToolResultResponse(**result)
