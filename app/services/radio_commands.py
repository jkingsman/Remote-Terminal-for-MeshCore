import logging
from collections.abc import Awaitable, Callable
from typing import Any

from meshcore import EventType

logger = logging.getLogger(__name__)


class RadioCommandServiceError(RuntimeError):
    """Base error for reusable radio command workflows."""


class PathHashModeUnsupportedError(RadioCommandServiceError):
    """Raised when firmware does not support path hash mode updates."""


class RepeatModeUnsupportedError(RadioCommandServiceError):
    """Raised when firmware does not support repeat mode updates."""


class RadioCommandRejectedError(RadioCommandServiceError):
    """Raised when the radio reports an error for a command."""


class KeystoreRefreshError(RadioCommandServiceError):
    """Raised when server-side keystore refresh fails after import."""


def _format_freq_ranges(ranges: list[tuple[float, float]]) -> str:
    return (
        ", ".join(
            f"{lo:.3f}" if round(lo * 1000) == round(hi * 1000) else f"{lo:.3f}–{hi:.3f}"
            for lo, hi in ranges
        )
        + " MHz"
    )


async def apply_radio_config_update(
    mc,
    update,
    *,
    path_hash_mode_supported: bool,
    set_path_hash_mode: Callable[[int], None],
    repeat_supported: bool,
    repeat_enabled: bool,
    allowed_repeat_freqs: list[tuple[float, float]],
    set_repeat_enabled: Callable[[bool], None],
    sync_radio_time_fn: Callable[[Any], Awaitable[Any]],
) -> None:
    """Apply a validated radio-config update to the connected radio."""
    if update.advert_location_source is not None:
        advert_loc_policy = 0 if update.advert_location_source == "off" else 1
        logger.info(
            "Setting advert location policy to %s",
            update.advert_location_source,
        )
        result = await mc.commands.set_advert_loc_policy(advert_loc_policy)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(
                f"Failed to set advert location policy: {result.payload}"
            )

    if update.multi_acks_enabled is not None:
        multi_acks = 1 if update.multi_acks_enabled else 0
        logger.info("Setting multi ACKs to %d", multi_acks)
        result = await mc.commands.set_multi_acks(multi_acks)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(f"Failed to set multi ACKs: {result.payload}")

    if update.telemetry_mode_base is not None:
        logger.info("Setting telemetry_mode_base to %d", update.telemetry_mode_base)
        result = await mc.commands.set_telemetry_mode_base(update.telemetry_mode_base)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(
                f"Failed to set telemetry mode (base): {result.payload}"
            )

    if update.telemetry_mode_loc is not None:
        logger.info("Setting telemetry_mode_loc to %d", update.telemetry_mode_loc)
        result = await mc.commands.set_telemetry_mode_loc(update.telemetry_mode_loc)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(
                f"Failed to set telemetry mode (location): {result.payload}"
            )

    if update.telemetry_mode_env is not None:
        logger.info("Setting telemetry_mode_env to %d", update.telemetry_mode_env)
        result = await mc.commands.set_telemetry_mode_env(update.telemetry_mode_env)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(
                f"Failed to set telemetry mode (environment): {result.payload}"
            )

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

    if update.radio is not None or update.repeat_enabled is not None:
        if update.repeat_enabled is not None and not repeat_supported:
            raise RepeatModeUnsupportedError("Firmware does not support repeat mode setting")

        if update.radio is not None:
            params = update.radio.model_dump()
        else:
            params = {k: mc.self_info[f"radio_{k}"] for k in ("freq", "bw", "sf", "cr")}
        # Firmware persists repeat=0 when the byte is omitted, so always resend the current value.
        repeat = None
        if repeat_supported:
            repeat = update.repeat_enabled if update.repeat_enabled is not None else repeat_enabled

        logger.info("Setting radio params: %s, repeat=%s", params, repeat)
        result = await mc.commands.set_radio(**params, repeat=repeat)
        if result is not None and result.type == EventType.ERROR:
            reason = (
                result.payload.get("code_string") or result.payload.get("error")
                if isinstance(result.payload, dict)
                else None
            ) or str(result.payload)
            detail = f"Firmware rejected the radio settings ({reason})"
            if repeat and allowed_repeat_freqs:
                detail += (
                    f". Repeat mode is only allowed on: {_format_freq_ranges(allowed_repeat_freqs)}"
                )
            elif repeat:
                detail += (
                    ". Repeat mode is only allowed on the firmware's permitted frequencies "
                    "(stock builds: 433.000, 869.495, 918.000 MHz)"
                )
            raise RadioCommandRejectedError(detail)
        if repeat is not None:
            set_repeat_enabled(repeat)

    if update.path_hash_mode is not None:
        if not path_hash_mode_supported:
            raise PathHashModeUnsupportedError("Firmware does not support path hash mode setting")

        logger.info("Setting path hash mode to %d", update.path_hash_mode)
        result = await mc.commands.set_path_hash_mode(update.path_hash_mode)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(f"Failed to set path hash mode: {result.payload}")
        set_path_hash_mode(update.path_hash_mode)

    await sync_radio_time_fn(mc)

    # Commands like set_name() write to flash but don't update cached self_info.
    # send_appstart() forces a fresh SELF_INFO so the response reflects changes.
    await mc.commands.send_appstart()


async def import_private_key_and_refresh_keystore(
    mc,
    key_bytes: bytes,
    *,
    export_and_store_private_key_fn: Callable[[Any], Awaitable[bool]],
) -> None:
    """Import a private key and refresh the in-memory keystore immediately."""
    result = await mc.commands.import_private_key(key_bytes)
    if result.type == EventType.ERROR:
        raise RadioCommandRejectedError(f"Failed to import private key: {result.payload}")

    keystore_refreshed = await export_and_store_private_key_fn(mc)
    if not keystore_refreshed:
        logger.warning("Keystore refresh failed after import, retrying once")
        keystore_refreshed = await export_and_store_private_key_fn(mc)

    if not keystore_refreshed:
        raise KeystoreRefreshError(
            "Private key imported on radio, but server-side keystore refresh failed. "
            "Reconnect to apply the new key for DM decryption."
        )
