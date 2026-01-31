import logging

from fastapi import APIRouter, HTTPException
from meshcore import EventType
from pydantic import BaseModel, Field

from app.dependencies import require_connected
from app.radio_sync import send_advertisement as do_send_advertisement
from app.radio_sync import sync_radio_time

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/radio", tags=["radio"])


class RadioSettings(BaseModel):
    freq: float = Field(description="Frequency in MHz")
    bw: float = Field(description="Bandwidth in kHz")
    sf: int = Field(description="Spreading factor (7-12)")
    cr: int = Field(description="Coding rate (1-4)")


class RadioConfigResponse(BaseModel):
    public_key: str = Field(description="Public key (64-char hex)")
    name: str
    lat: float
    lon: float
    tx_power: int = Field(description="Transmit power in dBm")
    max_tx_power: int = Field(description="Maximum transmit power in dBm")
    radio: RadioSettings


class RadioConfigUpdate(BaseModel):
    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    tx_power: int | None = Field(default=None, description="Transmit power in dBm")
    radio: RadioSettings | None = None


class PrivateKeyUpdate(BaseModel):
    private_key: str = Field(description="Private key as hex string")


@router.get("/config", response_model=RadioConfigResponse)
async def get_radio_config() -> RadioConfigResponse:
    """Get the current radio configuration."""
    mc = require_connected()

    info = mc.self_info
    if not info:
        raise HTTPException(status_code=503, detail="Radio info not available")

    return RadioConfigResponse(
        public_key=info.get("public_key", ""),
        name=info.get("name", ""),
        lat=info.get("adv_lat", 0.0),
        lon=info.get("adv_lon", 0.0),
        tx_power=info.get("tx_power", 0),
        max_tx_power=info.get("max_tx_power", 0),
        radio=RadioSettings(
            freq=info.get("radio_freq", 0.0),
            bw=info.get("radio_bw", 0.0),
            sf=info.get("radio_sf", 0),
            cr=info.get("radio_cr", 0),
        ),
    )


@router.patch("/config", response_model=RadioConfigResponse)
async def update_radio_config(update: RadioConfigUpdate) -> RadioConfigResponse:
    """Update radio configuration. Only provided fields will be updated."""
    mc = require_connected()

    if update.name is not None:
        logger.info("Setting radio name to %s", update.name)
        await mc.commands.set_name(update.name)

    if update.lat is not None or update.lon is not None:
        current_info = mc.self_info
        lat = update.lat if update.lat is not None else current_info.get("adv_lat", 0.0)
        lon = update.lon if update.lon is not None else current_info.get("adv_lon", 0.0)
        logger.info("Setting radio coordinates to %f, %f", lat, lon)
        await mc.commands.set_coords(lat=lat, lon=lon)

    if update.tx_power is not None:
        logger.info("Setting TX power to %d dBm", update.tx_power)
        await mc.commands.set_tx_power(val=update.tx_power)

    if update.radio is not None:
        logger.info(
            "Setting radio params: freq=%f MHz, bw=%f kHz, sf=%d, cr=%d",
            update.radio.freq,
            update.radio.bw,
            update.radio.sf,
            update.radio.cr,
        )
        await mc.commands.set_radio(
            freq=update.radio.freq,
            bw=update.radio.bw,
            sf=update.radio.sf,
            cr=update.radio.cr,
        )

    # Sync time with system clock
    await sync_radio_time()

    # Re-fetch self_info so the response reflects the changes we just made.
    # Commands like set_name() write to flash but don't update the cached
    # self_info — send_appstart() triggers a fresh SELF_INFO from the radio.
    await mc.commands.send_appstart()

    return await get_radio_config()


@router.put("/private-key")
async def set_private_key(update: PrivateKeyUpdate) -> dict:
    """Set the radio's private key. This is write-only."""
    mc = require_connected()

    try:
        key_bytes = bytes.fromhex(update.private_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex string for private key") from None

    logger.info("Importing private key")
    result = await mc.commands.import_private_key(key_bytes)

    if result.type == EventType.ERROR:
        raise HTTPException(
            status_code=500, detail=f"Failed to import private key: {result.payload}"
        )

    return {"status": "ok"}


@router.post("/advertise")
async def send_advertisement(flood: bool = True) -> dict:
    """Send a radio advertisement to announce presence on the mesh.

    Manual advertisement requests always send immediately, updating the
    last_advert_time which affects when the next periodic/startup advert
    can occur.

    Args:
        flood: Whether to flood the advertisement (default True).

    Returns:
        status: "ok" if sent successfully
    """
    require_connected()

    # Manual requests always send (force=True), but still update last_advert_time
    logger.info("Sending advertisement (flood=%s)", flood)
    success = await do_send_advertisement(force=True)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to send advertisement")

    return {"status": "ok", "flood": flood}


@router.post("/reboot")
async def reboot_radio() -> dict:
    """Reboot the radio, or reconnect if not currently connected.

    If connected: sends reboot command, connection will temporarily drop and auto-reconnect.
    If not connected: attempts to reconnect (same as /reconnect endpoint).
    """
    from app.radio import radio_manager

    # If connected, send reboot command
    if radio_manager.is_connected and radio_manager.meshcore:
        logger.info("Rebooting radio")
        await radio_manager.meshcore.commands.reboot()
        return {
            "status": "ok",
            "message": "Reboot command sent. Radio will reconnect automatically.",
        }

    # Not connected - attempt to reconnect
    if radio_manager.is_reconnecting:
        return {
            "status": "pending",
            "message": "Reconnection already in progress",
            "connected": False,
        }

    logger.info("Radio not connected, attempting reconnect")
    success = await radio_manager.reconnect()

    if success:
        # Re-register event handlers after successful reconnect
        from app.event_handlers import register_event_handlers

        if radio_manager.meshcore:
            register_event_handlers(radio_manager.meshcore)
            await radio_manager.meshcore.start_auto_message_fetching()
            logger.info("Event handlers re-registered and auto message fetching started")

        return {"status": "ok", "message": "Reconnected successfully", "connected": True}
    else:
        raise HTTPException(
            status_code=503, detail="Failed to reconnect. Check radio connection and power."
        )


@router.post("/reconnect")
async def reconnect_radio() -> dict:
    """Attempt to reconnect to the radio.

    This will try to re-establish connection to the radio, with auto-detection
    if no specific port is configured. Useful when the radio has been disconnected
    or power-cycled.
    """
    from app.radio import radio_manager

    if radio_manager.is_connected:
        return {"status": "ok", "message": "Already connected", "connected": True}

    if radio_manager.is_reconnecting:
        return {
            "status": "pending",
            "message": "Reconnection already in progress",
            "connected": False,
        }

    logger.info("Manual reconnect requested")
    success = await radio_manager.reconnect()

    if success:
        # Re-register event handlers after successful reconnect
        from app.event_handlers import register_event_handlers

        if radio_manager.meshcore:
            register_event_handlers(radio_manager.meshcore)
            # Restart auto message fetching
            await radio_manager.meshcore.start_auto_message_fetching()
            logger.info("Event handlers re-registered and auto message fetching started")

        return {"status": "ok", "message": "Reconnected successfully", "connected": True}
    else:
        raise HTTPException(
            status_code=503, detail="Failed to reconnect. Check radio connection and power."
        )
