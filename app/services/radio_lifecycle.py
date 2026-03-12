import asyncio
import logging

logger = logging.getLogger(__name__)

POST_CONNECT_SETUP_TIMEOUT_SECONDS = 300
POST_CONNECT_SETUP_MAX_ATTEMPTS = 2


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

                # Apply flood scope from settings (best-effort; older firmware
                # may not support set_flood_scope)
                from app.region_scope import normalize_region_scope
                from app.repository import AppSettingsRepository

                app_settings = await AppSettingsRepository.get()
                scope = normalize_region_scope(app_settings.flood_scope)
                try:
                    await mc.commands.set_flood_scope(scope if scope else "")
                    logger.info("Applied flood_scope=%r", scope or "(disabled)")
                except Exception as exc:
                    logger.warning("set_flood_scope failed (firmware may not support it): %s", exc)

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
                radio_manager.max_channels = 40
                radio_manager.path_hash_mode = 0
                radio_manager.path_hash_mode_supported = False
                try:
                    device_query = await mc.commands.send_device_query()
                    if device_query and "max_channels" in device_query.payload:
                        radio_manager.max_channels = max(
                            1, int(device_query.payload["max_channels"])
                        )
                    if device_query and "path_hash_mode" in device_query.payload:
                        radio_manager.path_hash_mode = device_query.payload["path_hash_mode"]
                        radio_manager.path_hash_mode_supported = True
                    elif _captured_frame:
                        # Raw-frame fallback:
                        # byte 1 = fw_ver, byte 3 = max_channels, byte 81 = path_hash_mode
                        raw = _captured_frame[-1]
                        fw_ver = raw[1] if len(raw) > 1 else 0
                        if fw_ver >= 3 and len(raw) >= 4:
                            radio_manager.max_channels = max(1, raw[3])
                        if fw_ver >= 10 and len(raw) >= 82:
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
                    logger.info("Max channel slots: %d", radio_manager.max_channels)
                except Exception as exc:
                    logger.debug("Failed to query device info capabilities: %s", exc)
                finally:
                    reader.handle_rx = _original_handle_rx

                # Sync contacts/channels from radio to DB and clear radio
                logger.info("Syncing and offloading radio data...")
                result = await sync_and_offload_all(mc)
                logger.info("Sync complete: %s", result)

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

                await mc.start_auto_message_fetching()
                logger.info("Auto message fetching started")
            finally:
                radio_manager._release_operation_lock("post_connect_setup")

            # Start background tasks AFTER releasing the operation lock.
            # These tasks acquire their own locks when they need radio access.
            start_periodic_sync()
            start_periodic_advert()
            start_message_polling()

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
        except asyncio.TimeoutError as exc:
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
