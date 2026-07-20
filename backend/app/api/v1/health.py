"""Health endpoint for backend runtime checks."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.config import API_VERSION, SERVICE_IDENTIFIER

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Deterministic health response contract."""

    status: Literal["ok"]
    service: Literal["convointel-backend"]
    api_version: Literal["v1"]


@router.get("/health", response_model=HealthResponse)
def read_health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=SERVICE_IDENTIFIER,
        api_version=API_VERSION,
    )
