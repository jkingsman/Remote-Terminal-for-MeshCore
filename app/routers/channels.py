import logging
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Query
from meshcore import EventType
from pydantic import BaseModel, Field

from app.dependencies import require_connected
from app.models import Channel
from app.repository import ChannelRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/channels", tags=["channels"])


class CreateChannelRequest(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    key: str | None = Field(
        default=None,
        description="Channel key as hex string (32 chars = 16 bytes). If omitted or name starts with #, key is derived from name hash."
    )


@router.get("", response_model=list[Channel])
async def list_channels() -> list[Channel]:
    """List all channels from the database."""
    return await ChannelRepository.get_all()


@router.get("/{key}", response_model=Channel)
async def get_channel(key: str) -> Channel:
    """Get a specific channel by key (32-char hex string)."""
    channel = await ChannelRepository.get_by_key(key)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    return channel


@router.post("", response_model=Channel)
async def create_channel(request: CreateChannelRequest) -> Channel:
    """Create a channel in the database.

    Channels are NOT pushed to radio on creation. They are loaded to the radio
    automatically when sending a message (see messages.py send_channel_message).
    """
    is_hashtag = request.name.startswith("#")

    # Determine the channel secret
    if request.key and not is_hashtag:
        try:
            key_bytes = bytes.fromhex(request.key)
            if len(key_bytes) != 16:
                raise HTTPException(
                    status_code=400,
                    detail="Channel key must be exactly 16 bytes (32 hex chars)"
                )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid hex string for key")
    else:
        # Derive key from name hash (same as meshcore library does)
        key_bytes = sha256(request.name.encode("utf-8")).digest()[:16]

    key_hex = key_bytes.hex().upper()
    logger.info("Creating channel %s: %s (hashtag=%s)", key_hex, request.name, is_hashtag)

    # Store in database only - radio sync happens at send time
    await ChannelRepository.upsert(
        key=key_hex,
        name=request.name,
        is_hashtag=is_hashtag,
        on_radio=False,
    )

    return Channel(
        key=key_hex,
        name=request.name,
        is_hashtag=is_hashtag,
        on_radio=False,
    )


@router.post("/sync")
async def sync_channels_from_radio(
    max_channels: int = Query(default=40, ge=1, le=40)
) -> dict:
    """Sync channels from the radio to the database."""
    mc = require_connected()

    logger.info("Syncing channels from radio (checking %d slots)", max_channels)
    count = 0

    for idx in range(max_channels):
        result = await mc.commands.get_channel(idx)

        if result.type == EventType.CHANNEL_INFO:
            payload = result.payload
            name = payload.get("channel_name", "")
            secret = payload.get("channel_secret", b"")

            # Skip empty channels
            if not name or name == "\x00" * len(name):
                continue

            is_hashtag = name.startswith("#")
            key_bytes = secret if isinstance(secret, bytes) else bytes(secret)
            key_hex = key_bytes.hex().upper()

            await ChannelRepository.upsert(
                key=key_hex,
                name=name,
                is_hashtag=is_hashtag,
                on_radio=True,
            )
            count += 1
            logger.debug("Synced channel %s: %s", key_hex, name)

    logger.info("Synced %d channels from radio", count)
    return {"synced": count}


@router.delete("/{key}")
async def delete_channel(key: str) -> dict:
    """Delete a channel from the database by key.

    Note: This does not clear the channel from the radio. The radio's channel
    slots are managed separately (channels are loaded temporarily when sending).
    """
    logger.info("Deleting channel %s from database", key)
    await ChannelRepository.delete(key)
    return {"status": "ok"}
