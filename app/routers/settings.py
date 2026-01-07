import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


class AppSettingsResponse(BaseModel):
    max_radio_contacts: int = Field(description="Maximum non-repeater contacts to keep on radio for DM ACKs")


class AppSettingsUpdate(BaseModel):
    max_radio_contacts: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description="Maximum non-repeater contacts to keep on radio (1-1000)"
    )


@router.get("", response_model=AppSettingsResponse)
async def get_settings() -> AppSettingsResponse:
    """Get current application settings."""
    return AppSettingsResponse(
        max_radio_contacts=settings.max_radio_contacts,
    )


@router.patch("", response_model=AppSettingsResponse)
async def update_settings(update: AppSettingsUpdate) -> AppSettingsResponse:
    """Update application settings.

    Note: Changes are applied immediately but not persisted across restarts.
    Set MESHCORE_MAX_RADIO_CONTACTS environment variable for persistent changes.
    """
    if update.max_radio_contacts is not None:
        logger.info("Updating max_radio_contacts from %d to %d",
                    settings.max_radio_contacts, update.max_radio_contacts)
        # Pydantic settings are mutable, we can update them directly
        object.__setattr__(settings, 'max_radio_contacts', update.max_radio_contacts)

    return AppSettingsResponse(
        max_radio_contacts=settings.max_radio_contacts,
    )
