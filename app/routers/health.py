from fastapi import APIRouter
from pydantic import BaseModel

from app.radio import radio_manager


router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    radio_connected: bool
    serial_port: str | None


@router.get("/health", response_model=HealthResponse)
async def healthcheck() -> HealthResponse:
    """Check if the API is running and if the radio is connected."""
    return HealthResponse(
        status="ok" if radio_manager.is_connected else "degraded",
        radio_connected=radio_manager.is_connected,
        serial_port=radio_manager.port,
    )
