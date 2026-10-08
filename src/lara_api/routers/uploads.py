from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from lara_api.dependencies import ApiContext, get_api_context
from lara_api.services.session_service import workspace_root_for_scope
from lara_api.services.uploads import delete_upload, read_upload, store_upload

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("")
async def upload_attachment(
    file: UploadFile = File(...),
    scope_id: str | None = Form(default=None),
    context: ApiContext = Depends(get_api_context),
) -> dict[str, object]:
    try:
        workspace_root = workspace_root_for_scope(context, scope_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = await file.read()
    try:
        stored = store_upload(data, file.filename, workspace_root)
    except ValueError as exc:
        status = 413 if "exceeds" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {
        "attachment_id": stored.attachment_id,
        "path": stored.relative,
        "name": stored.name,
        "mime_type": stored.mime_type,
        "size_bytes": stored.size_bytes,
    }


@router.get("/content")
def upload_content(
    scope_id: str | None = None,
    path: str = "",
    context: ApiContext = Depends(get_api_context),
) -> Response:
    try:
        workspace_root = workspace_root_for_scope(context, scope_id)
        data, mime = read_upload(workspace_root, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Upload {path!r} was not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=data, media_type=mime, headers={"Content-Disposition": "inline"})


@router.delete("", status_code=204)
def delete_stored_upload(
    scope_id: str | None = None,
    path: str = "",
    context: ApiContext = Depends(get_api_context),
) -> Response:
    try:
        workspace_root = workspace_root_for_scope(context, scope_id)
        delete_upload(workspace_root, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Upload {path!r} was not found") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)
