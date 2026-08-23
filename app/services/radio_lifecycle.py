import asyncio
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

POST_CONNECT_SETUP_TIMEOUT_SECONDS = 300
POST_CONNECT_SETUP_MAX_ATTEMPTS = 2


def _clean_device_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _decode_fixed_string(raw: bytes, start: int, length: int) -> str | None:
    if len(raw) < start:
        return None
    return _clean_device_string(
        raw[start : start + length].decode("utf-8", "ignore").replace("\0", "")
    )


async def run_post_connect_setup(radio_manager) -> None:
    """Run shared radio initialization after a transport connection succeeds."""
    from app.event_handlers import register_event_handlers
    from app.keystore import export_and_store_private_key
    from app.radio_sync import (
        drain_pending_messages,
        send_advertisement,
        start_message_polling,
        start_periodic_advert,
        start_periodic_sync,
        start_room_polling,
        start_telemetry_collect,
        sync_and_offload_all,
        sync_radio_time,
    )

    if not radio_manager.meshcore:
        return

    if radio_manager._setup_lock is None:
        radio_manager._setup_lock = asyncio.Lock()

    async def _setup_body() -> None:
        if not radio_manager.meshcore:
            return
        radio_manager._setup_in_progress = True
        radio_manager._setup_complete = False
        try:
            # Hold the operation lock for all radio I/O during setup.
            # This prevents user-initiated operations (send message, etc.)
            # from interleaving commands on the serial link.
            await radio_manager._acquire_operation_lock("post_connect_setup", blocking=True)
            try:
                mc = radio_manager.meshcore
                if not mc:
                    return

                # Register event handlers against the locked, current transport.
                register_event_handlers(mc)

                await export_and_store_private_key(mc)

                # Sync radio clock with system time
                await sync_radio_time(mc)

                # Query path hash mode support (best-effort; older firmware won't report it).
                # If the library's parsed payload is missing path_hash_mode (e.g. stale
                # .pyc on WSL2 Windows mounts), fall back to raw-frame extraction.
                reader = mc._reader
                _original_handle_rx = reader.handle_rx
                _captured_frame: list[bytes] = []

                async def _capture_handle_rx(data: bytearray) -> None:
                    from meshcore.packets import PacketType

                    if len(data) > 0 and data[0] == PacketType.DEVICE_INFO.value:
                        _captured_frame.append(bytes(data))
                    return await _original_handle_rx(data)

                reader.handle_rx = _capture_handle_rx
                radio_manager.device_info_loaded = False
                radio_manager.max_contacts = None
                radio_manager.device_model = None
                radio_manager.firmware_build = None
                radio_manager.firmware_version = None
                radio_manager.firmware_ver_code = None
                radio_manager.max_channels = 40
                radio_manager.path_hash_mode = 0
                radio_manager.path_hash_mode_supported = False
                try:
                    device_query = await mc.commands.send_device_query()
                    payload = (
                        device_query.payload
                        if device_query is not None and isinstance(device_query.payload, dict)
                        else {}
                    )

                    payload_max_contacts = payload.get("max_contacts")
                    if isinstance(payload_max_contacts, int):
                        radio_manager.max_contacts = max(1, payload_max_contacts)

                    payload_max_channels = payload.get("max_channels")
                    if isinstance(payload_max_channels, int):
                        radio_manager.max_channels = max(1, payload_max_channels)

                    radio_manager.device_model = _clean_device_string(payload.get("model"))
                    radio_manager.firmware_build = _clean_device_string(payload.get("fw_build"))
                    radio_manager.firmware_version = _clean_device_string(payload.get("ver"))

                    fw_ver = payload.get("fw ver")
                    payload_reports_device_info = isinstance(fw_ver, int) and fw_ver >= 3
                    if payload_reports_device_info:
                        radio_manager.device_info_loaded = True
                        radio_manager.firmware_ver_code = fw_ver

                    if "path_hash_mode" in payload and isinstance(payload["path_hash_mode"], int):
                        radio_manager.path_hash_mode = payload["path_hash_mode"]
                        radio_manager.path_hash_mode_supported = True

                    if _captured_frame:
                        # Raw-frame fallback / completion:
                        # byte 1 = fw_ver, byte 2 = max_contacts/2, byte 3 = max_channels,
                        # bytes 8:20 = fw_build, 20:60 = model, 60:80 = ver, byte 81 = path_hash_mode
                        raw = _captured_frame[-1]
                        fw_ver = raw[1] if len(raw) > 1 else 0
                        if fw_ver >= 3:
                            radio_manager.device_info_loaded = True
                            radio_manager.firmware_ver_code = fw_ver
                            if radio_manager.max_contacts is None and len(raw) >= 3:
                                radio_manager.max_contacts = max(1, raw[2] * 2)
                            if len(raw) >= 4 and not isinstance(payload_max_channels, int):
                                radio_manager.max_channels = max(1, raw[3])
                            if radio_manager.firmware_build is None:
                                radio_manager.firmware_build = _decode_fixed_string(raw, 8, 12)
                            if radio_manager.device_model is None:
                                radio_manager.device_model = _decode_fixed_string(raw, 20, 40)
                            if radio_manager.firmware_version is None:
                                radio_manager.firmware_version = _decode_fixed_string(raw, 60, 20)
                        if (
                            not radio_manager.path_hash_mode_supported
                            and fw_ver >= 10
                            and len(raw) >= 82
                        ):
                            radio_manager.path_hash_mode = raw[81]
                            radio_manager.path_hash_mode_supported = True
                            logger.warning(
                                "path_hash_mode=%d extracted from raw frame "
                                "(stale .pyc? try: rm %s)",
                                radio_manager.path_hash_mode,
                                getattr(
                                    __import__("meshcore.reader", fromlist=["reader"]),
                                    "__cached__",
                                    "meshcore __pycache__/reader.*.pyc",
                                ),
                            )
                    if radio_manager.path_hash_mode_supported:
                        logger.info("Path hash mode: %d (supported)", radio_manager.path_hash_mode)
                    else:
                        logger.debug("Firmware does not report path_hash_mode")
                    if radio_manager.device_info_loaded:
                        logger.info(
                            "Radio device info: model=%s build=%s version=%s max_contacts=%s max_channels=%d",
                            radio_manager.device_model or "unknown",
                            radio_manager.firmware_build or "unknown",
                            radio_manager.firmware_version or "unknown",
                            radio_manager.max_contacts
                            if radio_manager.max_contacts is not None
                            else "unknown",
                            radio_manager.max_channels,
                        )
                        try:
                            time_result = await mc.commands.get_time()
                            radio_time = (
                                time_result.payload.get("time")
                                if time_result is not None and time_result.payload
                                else None
                            )
                            if isinstance(radio_time, int):
                                logger.info(
                                    "Radio clock at connect: epoch=%d utc=%s",
                                    radio_time,
                                    datetime.fromtimestamp(radio_time, UTC).strftime(
                                        "%Y-%m-%d %H:%M:%S UTC"
                                    ),
                                )
                        except Exception as exc:
                            logger.debug("Failed to query radio clock after device info: %s", exc)
                    logger.info("Max channel slots: %d", radio_manager.max_channels)
                except Exception as exc:
                    logger.debug("Failed to query device info capabilities: %s", exc)
                finally:
                    reader.handle_rx = _original_handle_rx

                # Apply flood scope from settings (best-effort; older firmware may
                # not support the mode-1 unscoped command). Done after the device
                # query so radio_manager.firmware_ver_code is known and the unscoped
                # path can pick the correct firmware command.
                from app.region_scope import normalize_region_scope
                from app.repository import AppSettingsRepository
                from app.services.flood_scope import set_radio_flood_scope

                app_settings = await AppSettingsRepository.get()
                scope = normalize_region_scope(app_settings.flood_scope)
                try:
                    await set_radio_flood_scope(mc, scope, fw_ver=radio_manager.firmware_ver_code)
                    logger.info("Applied flood_scope=%r", scope or "(disabled)")
                except Exception as exc:
                    logger.warning("Failed to apply configured flood scope to radio: %s", exc)

                from app.config import settings as app_settings_config

                if app_settings_config.skip_post_connect_sync:
                    logger.info(
                        "Skipping sync/offload/advert/drain (MESHCORE_SKIP_POST_CONNECT_SYNC)"
                    )
                else:
                    # Sync contacts/channels from radio to DB and clear radio
                    logger.info("Syncing and offloading radio data...")
                    result = await sync_and_offload_all(mc)
                    c = result.get("contacts", {})
                    ch = result.get("channels", {})
                    logger.info(
                        "Sync complete: %d contacts synced, %d channels synced, %d channels cleared",
                        c.get("synced", 0),
                        ch.get("synced", 0),
                        ch.get("cleared", 0),
                    )

                    # Send advertisement to announce our presence (if enabled and not throttled)
                    if await send_advertisement(mc):
                        logger.info("Advertisement sent")
                    else:
                        logger.debug("Advertisement skipped (disabled or throttled)")

                    # Drain any messages that were queued before we connected.
                    # This must happen BEFORE starting auto-fetch, otherwise both
                    # compete on get_msg() with interleaved radio I/O.
                    drained = await drain_pending_messages(mc)
                    if drained > 0:
                        logger.info("Drained %d pending message(s)", drained)
                    radio_manager.clear_pending_message_channel_slots()

                await mc.start_auto_message_fetching()
                logger.info("Auto message fetching started")
            finally:
                radio_manager._release_operation_lock("post_connect_setup")

            if not app_settings_config.skip_post_connect_sync:
                # Start background tasks AFTER releasing the operation lock.
                # These tasks acquire their own locks when they need radio access.
                start_periodic_sync()
                start_periodic_advert()
                start_message_polling()
                start_telemetry_collect()
                start_room_polling()

            radio_manager._setup_complete = True
        finally:
            radio_manager._setup_in_progress = False

    async with radio_manager._setup_lock:
        await asyncio.wait_for(_setup_body(), timeout=POST_CONNECT_SETUP_TIMEOUT_SECONDS)

    logger.info("Post-connect setup complete")


async def prepare_connected_radio(radio_manager, *, broadcast_on_success: bool = True) -> bool:
    """Finish setup for an already-connected radio and optionally broadcast health."""
    from app.websocket import broadcast_error, broadcast_health

    if not radio_manager.connection_desired:
        if radio_manager.is_connected:
            await radio_manager.disconnect()
        return False

    for attempt in range(1, POST_CONNECT_SETUP_MAX_ATTEMPTS + 1):
        try:
            await radio_manager.post_connect_setup()
            break
        except TimeoutError as exc:
            if attempt < POST_CONNECT_SETUP_MAX_ATTEMPTS:
                logger.warning(
                    "Post-connect setup timed out after %ds on attempt %d/%d; retrying once",
                    POST_CONNECT_SETUP_TIMEOUT_SECONDS,
                    attempt,
                    POST_CONNECT_SETUP_MAX_ATTEMPTS,
                )
                continue

            logger.error(
                "Post-connect setup timed out after %ds on %d attempts. Initial radio offload "
                "took too long; something is probably wrong.",
                POST_CONNECT_SETUP_TIMEOUT_SECONDS,
                POST_CONNECT_SETUP_MAX_ATTEMPTS,
            )
            broadcast_error(
                "Radio startup appears stuck",
                "Initial radio offload took too long. Reboot the radio and restart the server.",
            )
            raise RuntimeError("Post-connect setup timed out") from exc

    if not radio_manager.connection_desired:
        if radio_manager.is_connected:
            await radio_manager.disconnect()
        return False

    radio_manager._last_connected = True
    if broadcast_on_success:
        broadcast_health(True, radio_manager.connection_info)
    return True


async def reconnect_and_prepare_radio(
    radio_manager,
    *,
    broadcast_on_success: bool = True,
) -> bool:
    """Reconnect the transport, then run post-connect setup before reporting healthy."""
    connected = await radio_manager.reconnect(broadcast_on_success=False)
    if not connected:
        return False

    return await prepare_connected_radio(radio_manager, broadcast_on_success=broadcast_on_success)


async def connection_monitor_loop(radio_manager) -> None:
    """Monitor radio health and keep transport/setup state converged."""
    from app.websocket import broadcast_health

    check_interval_seconds = 5
    unresponsive_threshold = 3
    consecutive_setup_failures = 0

    while True:
        try:
            await asyncio.sleep(check_interval_seconds)

            current_connected = radio_manager.is_connected
            connection_desired = radio_manager.connection_desired

            if radio_manager._last_connected and not current_connected:
                logger.warning("Radio connection lost, broadcasting status change")
                broadcast_health(False, radio_manager.connection_info)
                radio_manager._last_connected = False
                consecutive_setup_failures = 0

            if not connection_desired:
                if current_connected:
                    logger.info("Radio connection paused by operator; disconnecting transport")
                    await radio_manager.disconnect()
                consecutive_setup_failures = 0
                continue

            if not current_connected:
                if not radio_manager.is_reconnecting and await reconnect_and_prepare_radio(
                    radio_manager,
                    broadcast_on_success=True,
                ):
                    consecutive_setup_failures = 0

            elif not radio_manager._last_connected and current_connected:
                logger.info("Radio connection restored")
                await prepare_connected_radio(radio_manager, broadcast_on_success=True)
                consecutive_setup_failures = 0

            elif (
                current_connected
                and not radio_manager.is_setup_complete
                and not radio_manager.is_setup_in_progress
            ):
                logger.info("Retrying post-connect setup...")
                await prepare_connected_radio(radio_manager, broadcast_on_success=True)
                consecutive_setup_failures = 0

        except asyncio.CancelledError:
            break
        except Exception as e:
            consecutive_setup_failures += 1
            if consecutive_setup_failures == unresponsive_threshold:
                logger.error(
                    "Post-connect setup has failed %d times in a row. "
                    "The radio port appears open but the radio is not "
                    "responding to commands. Common causes: another "
                    "process has the serial port open (check for other "
                    "RemoteTerm instances, serial monitors, etc.), the "
                    "firmware is in repeater mode (not client), or the "
                    "radio needs a power cycle. Will keep retrying.",
                    consecutive_setup_failures,
                )
            elif consecutive_setup_failures < unresponsive_threshold:
                logger.exception("Error in connection monitor, continuing: %s", e)
