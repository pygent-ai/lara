from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from lara_api.dependencies import ApiContext, get_api_context
from lara_api.models.requests import ReorderProjectsRequest
from lara_api.models.responses import DeleteResponse, ProjectListResponse
from lara_api.services.project_service import project_list_response, remove_project

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=ProjectListResponse)
def list_projects(context: ApiContext = Depends(get_api_context)) -> ProjectListResponse:
    return project_list_response(context)


@router.patch("/order", response_model=ProjectListResponse)
def reorder_projects(
    request: ReorderProjectsRequest,
    context: ApiContext = Depends(get_api_context),
) -> ProjectListResponse:
    context.project_state.reorder_projects(request.ordered_scope_ids)
    return project_list_response(context)


@router.delete("", response_model=DeleteResponse)
def delete_project(
    scope_id: str,
    context: ApiContext = Depends(get_api_context),
) -> DeleteResponse:
    try:
        return DeleteResponse(deleted=remove_project(context, scope_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
