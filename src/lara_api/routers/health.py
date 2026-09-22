from __future__ import annotations

import os

from fastapi import APIRouter, Response

from lara_api.models.responses import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    instance_id = os.environ.get("LARA_BACKEND_INSTANCE_ID")
    if instance_id:
        response.headers["X-Lara-Backend-Instance"] = instance_id
    return HealthResponse(status="ok", service="lara-api")
