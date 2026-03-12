"""
Radio sync and offload management.

This module handles syncing contacts and channels from the radio to the database,
then removing them from the radio to free up space for new discoveries.

Also handles loading favorites plus recently active contacts TO the radio for DM ACK support.
Also handles periodic message polling as a fallback for platforms where push events
don't work reliably.
"""

import asyncio
import logging
import math
import time
from contextlib import asynccontextmanager

from meshcore import EventType, MeshCore

from app.config import settings
from app.event_handlers import cleanup_expired_acks
from app.models import Contact, ContactUpsert
from app.radio import RadioOperationBusyError
from app.repository import (
    AmbiguousPublicKeyPrefixError,
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
)
from app.services.contact_reconciliation import reconcile_contact_messages
from app.services.radio_runtime import radio_runtime as radio_manager
from app.websocket import broadcast_error

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHANNELS = 40


def _contact_sync_debug_fields(contact: Contact) -> dict[str, object]:
    """Return key contact fields for sync failure diagnostics."""
    return {
        "type": contact.type,
        "flags": contact.flags,
        "last_path": contact.last_path,
        "last_path_len": contact.last_path_len,
        "out_path_hash_mode": contact.out_path_hash_mode,
        "route_override_path": contact.route_override_path,
        "route_override_len": contact.route_override_len,
        "route_override_hash_mode": contact.route_override_hash_mode,
        "last_advert": contact.last_advert,
        "lat": contact.lat,
        "lon": contact.lon,
        "on_radio": contact.on_radio,
    }


async def _reconcile_contact_messages_background(
    public_key: str,
    contact_name: str | None,
) -> None:
    """Run contact/message reconciliation outside the radio critical path."""
    try:
        await reconcile_contact_messages(
            public_key=public_key,
            contact_name=contact_name,
            log=logger,
        )
    except Exception as exc:
        logger.warning(
            "Background contact reconciliation failed for %s: %s",
            public_key[:12],
            exc,
            exc_info=True,
        )


async def upsert_channel_from_radio_slot(payload: dict, *, on_radio: bool) -> str | None:
    """Parse a radio channel-slot payload and upsert to the database.

    Returns the uppercase hex key if a channel was upserted, or None if the
    slot was empty/invalid.
    """
    name = payload.get("channel_name", "")
    secret = payload.get("channel_secret", b"")

    # Skip empty channels
    if not name or name == "\x00" * len(name):
        return None

    is_hashtag = name.startswith("#")
    key_bytes = secret if isinstance(secret, bytes) else bytes(secret)
    key_hex = key_bytes.hex().upper()

    await ChannelRepository.upsert(
        key=key_hex,
        name=name,
        is_hashtag=is_hashtag,
        on_radio=on_radio,
    )
    return key_hex


def get_radio_channel_limit(max_channels: int | None = None) -> int:
    """Return the effective channel-slot limit for the connected firmware."""
    discovered = getattr(radio_manager, "max_channels", DEFAULT_MAX_CHANNELS)
    try:
        limit = max(1, int(discovered))
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_CHANNELS

    if max_channels is not None:
        return min(limit, max(1, int(max_channels)))

    return limit


# Message poll task handle
_message_poll_task: asyncio.Task | None = None

# Message poll interval in seconds when aggressive fallback is enabled.
MESSAGE_POLL_INTERVAL = 10

# Always-on audit interval when aggressive fallback is disabled.
MESSAGE_POLL_AUDIT_INTERVAL = 3600

# Periodic advertisement task handle
_advert_task: asyncio.Task | None = None

# Default check interval when periodic advertising is disabled (seconds)
# We still need to periodically check if it's been enabled
ADVERT_CHECK_INTERVAL = 60

# Minimum allowed advertisement interval (1 hour).
# Even if the database has a shorter value, we silently refuse to advertise
# more frequently than this.
MIN_ADVERT_INTERVAL = 3600

# Counter to pause polling during repeater operations (supports nested pauses)
_polling_pause_count: int = 0


def is_polling_paused() -> bool:
    """Check if polling is currently paused."""
    return _polling_pause_count > 0


@asynccontextmanager
async def pause_polling():
    """Context manager to pause message polling during repeater operations.

    Supports nested pauses - polling only resumes when all pause contexts have exited.
    """
    global _polling_pause_count
    _polling_pause_count += 1
    try:
        yield
    finally:
        _polling_pause_count -= 1


# Background task handle
_sync_task: asyncio.Task | None = None

# Periodic maintenance check interval in seconds (5 minutes)
SYNC_INTERVAL = 300

# Reload non-favorite contacts up to 80% of configured radio capacity after offload.
RADIO_CONTACT_REFILL_RATIO = 0.80

# Trigger a full offload/reload once occupancy reaches 95% of configured capacity.
RADIO_CONTACT_FULL_SYNC_RATIO = 0.95


def _compute_radio_contact_limits(max_contacts: int) -> tuple[int, int]:
    """Return (refill_target, full_sync_trigger) for the configured capacity."""
    capacity = max(1, max_contacts)
    refill_target = max(1, min(capacity, int((capacity * RADIO_CONTACT_REFILL_RATIO) + 0.5)))
    full_sync_trigger = max(
        refill_target,
        min(capacity, math.ceil(capacity * RADIO_CONTACT_FULL_SYNC_RATIO)),
    )
    return refill_target, full_sync_trigger


async def should_run_full_periodic_sync(mc: MeshCore) -> bool:
    """Check current radio occupancy and decide whether to offload/reload."""
    app_settings = await AppSettingsRepository.get()
    capacity = app_settings.max_radio_contacts
    refill_target, full_sync_trigger = _compute_radio_contact_limits(capacity)

    result = await mc.commands.get_contacts()
    if result is None or result.type == EventType.ERROR:
        logger.warning("Periodic sync occupancy check failed: %s", result)
        return False

    current_contacts = len(result.payload or {})
    if current_contacts >= full_sync_trigger:
        logger.info(
            "Running full radio sync: %d/%d contacts on radio (trigger=%d, refill_target=%d)",
            current_contacts,
            capacity,
            full_sync_trigger,
            refill_target,
        )
        return True

    logger.debug(
        "Skipping full radio sync: %d/%d contacts on radio (trigger=%d, refill_target=%d)",
        current_contacts,
        capacity,
        full_sync_trigger,
        refill_target,
    )
    return False


async def sync_and_offload_contacts(mc: MeshCore) -> dict:
    """
    Sync contacts from radio to database, then remove them from radio.
    Returns counts of synced and removed contacts.
    """
    synced = 0
    removed = 0

    try:
        # Get all contacts from radio
        result = await mc.commands.get_contacts()

        if result is None or result.type == EventType.ERROR:
            logger.error(
                "Failed to get contacts from radio: %s. "
                "If you see this repeatedly, the radio may be visible on the "
                "serial/TCP/BLE port but not responding to commands. Check for "
                "another process with the serial port open (other RemoteTerm "
                "instances, serial monitors, etc.), verify the firmware is "
                "up-to-date and in client mode (not repeater), or try a "
                "power cycle.",
                result,
            )
            return {"synced": 0, "removed": 0, "error": str(result)}

        contacts = result.payload or {}
        logger.info("Found %d contacts on radio", len(contacts))

        # Sync each contact to database, then remove from radio
        for public_key, contact_data in contacts.items():
            # Save to database
            await ContactRepository.upsert(
                ContactUpsert.from_radio_dict(public_key, contact_data, on_radio=False)
            )
            asyncio.create_task(
                _reconcile_contact_messages_background(
                    public_key,
                    contact_data.get("adv_name"),
                )
            )
            synced += 1

            # Remove from radio
            try:
                remove_result = await mc.commands.remove_contact(contact_data)
                if remove_result.type == EventType.OK:
                    removed += 1

                    # LIBRARY INTERNAL FIXUP: The MeshCore library's
                    # commands.remove_contact() sends the remove command over
                    # the wire but does NOT update the library's in-memory
                    # contact cache (mc._contacts). This is a gap in the
                    # library — there's no public API to clear a single
                    # contact from the cache, and the library only refreshes
                    # it on a full get_contacts() call.
                    #
                    # Why this matters: sync_recent_contacts_to_radio() uses
                    # mc.get_contact_by_key_prefix() to check whether a
                    # contact is already loaded on the radio. That method
                    # searches mc._contacts. If we don't evict the removed
                    # contact from the cache here, get_contact_by_key_prefix()
                    # will still find it and skip the add_contact() call —
                    # meaning contacts never get loaded back onto the radio
                    # after offload. The result: no DM ACKs, degraded routing
                    # for potentially minutes until the next periodic sync
                    # refreshes the cache from the (now-empty) radio.
                    #
                    # We access mc._contacts directly because the library
                    # exposes it as a read-only property (mc.contacts) with
                    # no removal API. The dict is keyed by public_key string.
                    mc._contacts.pop(public_key, None)
                else:
                    logger.warning(
                        "Failed to remove contact %s: %s", public_key[:12], remove_result.payload
                    )
            except Exception as e:
                logger.warning("Error removing contact %s: %s", public_key[:12], e)

        logger.info("Synced %d contacts, removed %d from radio", synced, removed)

    except Exception as e:
        logger.error("Error during contact sync: %s", e)
        return {"synced": synced, "removed": removed, "error": str(e)}

    return {"synced": synced, "removed": removed}


async def sync_and_offload_channels(mc: MeshCore, max_channels: int | None = None) -> dict:
    """
    Sync channels from radio to database, then clear them from radio.
    Returns counts of synced and cleared channels.
    """
    synced = 0
    cleared = 0

    try:
        radio_manager.reset_channel_send_cache()
        channel_limit = get_radio_channel_limit(max_channels)

        # Check all available channel slots for this firmware variant
        for idx in range(channel_limit):
            result = await mc.commands.get_channel(idx)

            if result.type != EventType.CHANNEL_INFO:
                continue

            key_hex = await upsert_channel_from_radio_slot(
                result.payload,
                on_radio=False,  # We're about to clear it
            )
            if key_hex is None:
                continue

            synced += 1
            logger.debug("Synced channel %s: %s", key_hex[:8], result.payload.get("channel_name"))

            # Clear from radio (set empty name and zero key)
            try:
                clear_result = await mc.commands.set_channel(
                    channel_idx=idx,
                    channel_name="",
                    channel_secret=bytes(16),
                )
                if clear_result.type == EventType.OK:
                    cleared += 1
                else:
                    logger.warning("Failed to clear channel %d: %s", idx, clear_result.payload)
            except Exception as e:
                logger.warning("Error clearing channel %d: %s", idx, e)

        logger.info("Synced %d channels, cleared %d from radio", synced, cleared)

    except Exception as e:
        logger.error("Error during channel sync: %s", e)
        return {"synced": synced, "cleared": cleared, "error": str(e)}

    return {"synced": synced, "cleared": cleared}


async def ensure_default_channels() -> None:
    """
    Ensure default channels exist in the database.
    These will be configured on the radio when needed for sending.

    This seeds the canonical Public channel row in the database if it is missing
    or misnamed. It does not make the channel undeletable through the router.
    """
    # Public channel - no hashtag, specific well-known key
    PUBLIC_CHANNEL_KEY_HEX = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"

    # Check by KEY (not name) since that's what's fixed
    existing = await ChannelRepository.get_by_key(PUBLIC_CHANNEL_KEY_HEX)
    if not existing or existing.name != "Public":
        logger.info("Ensuring default Public channel exists with correct name")
        await ChannelRepository.upsert(
            key=PUBLIC_CHANNEL_KEY_HEX,
            name="Public",
            is_hashtag=False,
            on_radio=existing.on_radio if existing else False,
        )


async def sync_and_offload_all(mc: MeshCore) -> dict:
    """Sync and offload both contacts and channels, then ensure defaults exist."""
    logger.info("Starting full radio sync and offload")

    contacts_result = await sync_and_offload_contacts(mc)
    channels_result = await sync_and_offload_channels(mc)

    # Ensure default channels exist
    await ensure_default_channels()

    # Reload favorites plus a working-set fill back onto the radio immediately
    # so they do not stay in on_radio=False limbo after offload. Pass mc directly
    # since the caller already holds the radio operation lock (asyncio.Lock is not
    # reentrant).
    reload_result = await sync_recent_contacts_to_radio(force=True, mc=mc)

    return {
        "contacts": contacts_result,
        "channels": channels_result,
        "reloaded": reload_result,
    }


async def drain_pending_messages(mc: MeshCore) -> int:
    """
    Drain all pending messages from the radio.

    Calls get_msg() repeatedly until NO_MORE_MSGS is received.
    Returns the count of messages retrieved.
    """
    count = 0
    max_iterations = 100  # Safety limit

    for _ in range(max_iterations):
        try:
            result = await mc.commands.get_msg(timeout=2.0)

            if result.type == EventType.NO_MORE_MSGS:
                break
            elif result.type == EventType.ERROR:
                logger.debug("Error during message drain: %s", result.payload)
                break
            elif result.type in (EventType.CONTACT_MSG_RECV, EventType.CHANNEL_MSG_RECV):
                count += 1

            # Small delay between fetches
            await asyncio.sleep(0.1)

        except asyncio.TimeoutError:
            break
        except Exception as e:
            logger.warning("Error draining messages: %s", e, exc_info=True)
            break

    return count


async def poll_for_messages(mc: MeshCore) -> int:
    """
    Poll the radio for any pending messages (single pass).

    This is a fallback for platforms where MESSAGES_WAITING push events
    don't work reliably.

    Returns the count of messages retrieved.
    """
    count = 0

    try:
        # Try to get one message
        result = await mc.commands.get_msg(timeout=2.0)

        if result.type == EventType.NO_MORE_MSGS:
            # No messages waiting
            return 0
        elif result.type == EventType.ERROR:
            return 0
        elif result.type in (EventType.CONTACT_MSG_RECV, EventType.CHANNEL_MSG_RECV):
            count += 1
            # If we got a message, there might be more - drain them
            count += await drain_pending_messages(mc)

    except asyncio.TimeoutError:
        pass
    except Exception as e:
        logger.warning("Message poll exception: %s", e, exc_info=True)

    return count


def _normalize_channel_secret(payload: dict) -> bytes:
    """Return a normalized bytes representation of a radio channel secret."""
    secret = payload.get("channel_secret", b"")
    if isinstance(secret, bytes):
        return secret
    return bytes(secret)


async def audit_channel_send_cache(mc: MeshCore) -> bool:
    """Verify cached send-slot expectations still match radio channel contents.

    If a mismatch is detected, the app's send-slot cache is reset so future sends
    fall back to reloading channels before reuse resumes.
    """
    if not radio_manager.channel_slot_reuse_enabled():
        return True

    cached_slots = radio_manager.get_channel_send_cache_snapshot()
    if not cached_slots:
        return True

    mismatches: list[str] = []
    for channel_key, slot in cached_slots:
        result = await mc.commands.get_channel(slot)
        if result.type != EventType.CHANNEL_INFO:
            mismatches.append(
                f"slot {slot}: expected {channel_key[:8]} but radio returned {result.type}"
            )
            continue

        observed_name = result.payload.get("channel_name") or ""
        observed_key = _normalize_channel_secret(result.payload).hex().upper()
        expected_channel = await ChannelRepository.get_by_key(channel_key)
        expected_name = expected_channel.name if expected_channel is not None else None

        if observed_key != channel_key or expected_name is None or observed_name != expected_name:
            mismatches.append(
                f"slot {slot}: expected {expected_name or '(missing db row)'} "
                f"{channel_key[:8]}, got {observed_name or '(empty)'} {observed_key[:8]}"
            )

    if not mismatches:
        return True

    logger.error(
        "A periodic radio audit discovered that the channel send-slot cache fell out of sync with radio state. This indicates that some other system, internal or external to the radio, has updated the channel slots on the radio (which the app assumes it has exclusive rights to, except on TCP-linked devices). The cache is resetting now, but you should review the README.md and consider using the environment variable MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE=true to make the radio use non-optimistic channel management and force-write the channel to radio before each send. This is a minor performance hit, but guarantees consistency. Mismatches found: %s",
        "; ".join(mismatches),
    )
    radio_manager.reset_channel_send_cache()
    broadcast_error(
        "A periodic poll task has discovered radio inconsistencies.",
        "Please check the logs for recommendations (search "
        "'MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE').",
    )
    return False


async def _message_poll_loop():
    """Background task that periodically polls for messages."""
    while True:
        try:
            aggressive_fallback = settings.enable_message_poll_fallback
            await asyncio.sleep(
                MESSAGE_POLL_INTERVAL if aggressive_fallback else MESSAGE_POLL_AUDIT_INTERVAL
            )

            if radio_manager.is_connected and not is_polling_paused():
                try:
                    async with radio_manager.radio_operation(
                        "message_poll_loop",
                        blocking=False,
                        suspend_auto_fetch=True,
                    ) as mc:
                        count = await poll_for_messages(mc)
                        await audit_channel_send_cache(mc)
                        if count > 0:
                            if aggressive_fallback:
                                logger.warning(
                                    "Poll loop caught %d message(s) missed by auto-fetch",
                                    count,
                                )
                            else:
                                logger.error(
                                    "Periodic radio audit caught %d message(s) that were not "
                                    "surfaced via event subscription. See README and consider "
                                    "setting MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK=true to "
                                    "enable more frequent polling.",
                                    count,
                                )
                                broadcast_error(
                                    "A periodic poll task has discovered radio inconsistencies.",
                                    "Please check the logs for recommendations (search "
                                    "'MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK').",
                                )
                except RadioOperationBusyError:
                    logger.debug("Skipping message poll: radio busy")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Error in message poll loop: %s", e, exc_info=True)


def start_message_polling():
    """Start the periodic message polling background task."""
    global _message_poll_task
    if _message_poll_task is None or _message_poll_task.done():
        _message_poll_task = asyncio.create_task(_message_poll_loop())
        if settings.enable_message_poll_fallback:
            logger.info(
                "Started periodic message polling task (aggressive fallback, interval: %ds)",
                MESSAGE_POLL_INTERVAL,
            )
        else:
            logger.info(
                "Started periodic message audit task (interval: %ds)",
                MESSAGE_POLL_AUDIT_INTERVAL,
            )


async def stop_message_polling():
    """Stop the periodic message polling background task."""
    global _message_poll_task
    if _message_poll_task and not _message_poll_task.done():
        _message_poll_task.cancel()
        try:
            await _message_poll_task
        except asyncio.CancelledError:
            pass
        _message_poll_task = None
        logger.info("Stopped periodic message polling")


async def send_advertisement(mc: MeshCore, *, force: bool = False) -> bool:
    """Send an advertisement to announce presence on the mesh.

    Respects the configured advert_interval - won't send if not enough time
    has elapsed since the last advertisement, unless force=True.

    Args:
        mc: The MeshCore instance to use for the advertisement.
        force: If True, send immediately regardless of interval.

    Returns True if successful, False otherwise (including if throttled).
    """
    # Check if enough time has elapsed (unless forced)
    if not force:
        settings = await AppSettingsRepository.get()
        interval = settings.advert_interval
        last_time = settings.last_advert_time
        now = int(time.time())

        # If interval is 0, advertising is disabled
        if interval <= 0:
            logger.debug("Advertisement skipped: periodic advertising is disabled")
            return False

        # Enforce minimum interval floor
        interval = max(interval, MIN_ADVERT_INTERVAL)

        # Check if enough time has passed
        elapsed = now - last_time
        if elapsed < interval:
            remaining = interval - elapsed
            logger.debug(
                "Advertisement throttled: %d seconds remaining (interval=%d, elapsed=%d)",
                remaining,
                interval,
                elapsed,
            )
            return False

    try:
        result = await mc.commands.send_advert(flood=True)
        if result.type == EventType.OK:
            # Update last_advert_time in database
            now = int(time.time())
            await AppSettingsRepository.update(last_advert_time=now)
            logger.info("Advertisement sent successfully")
            return True
        else:
            logger.warning("Failed to send advertisement: %s", result.payload)
            return False
    except Exception as e:
        logger.warning("Error sending advertisement: %s", e, exc_info=True)
        return False


async def _periodic_advert_loop():
    """Background task that periodically checks if an advertisement should be sent.

    The actual throttling logic is in send_advertisement(), which checks
    last_advert_time from the database. This loop just triggers the check
    periodically and sleeps between attempts.
    """
    while True:
        try:
            await asyncio.sleep(ADVERT_CHECK_INTERVAL)

            # Try to send - send_advertisement() handles all checks
            # (disabled, throttled, not connected)
            if radio_manager.is_connected:
                try:
                    async with radio_manager.radio_operation(
                        "periodic_advertisement",
                        blocking=False,
                    ) as mc:
                        await send_advertisement(mc)
                except RadioOperationBusyError:
                    logger.debug("Skipping periodic advertisement: radio busy")

        except asyncio.CancelledError:
            logger.info("Periodic advertisement task cancelled")
            break
        except Exception as e:
            logger.error("Error in periodic advertisement loop: %s", e, exc_info=True)


def start_periodic_advert():
    """Start the periodic advertisement background task.

    The task reads interval from app_settings dynamically, so it will
    adapt to configuration changes without restart.
    """
    global _advert_task
    if _advert_task is None or _advert_task.done():
        _advert_task = asyncio.create_task(_periodic_advert_loop())
        logger.info("Started periodic advertisement task (interval configured in settings)")


async def stop_periodic_advert():
    """Stop the periodic advertisement background task."""
    global _advert_task
    if _advert_task and not _advert_task.done():
        _advert_task.cancel()
        try:
            await _advert_task
        except asyncio.CancelledError:
            pass
        _advert_task = None
        logger.info("Stopped periodic advertisement")


async def sync_radio_time(mc: MeshCore) -> bool:
    """Sync the radio's clock with the system time.

    Returns True if successful, False otherwise.
    """
    try:
        now = int(time.time())
        await mc.commands.set_time(now)
        logger.debug("Synced radio time to %d", now)
        return True
    except Exception as e:
        logger.warning("Failed to sync radio time: %s", e, exc_info=True)
        return False


async def _periodic_sync_loop():
    """Background task that periodically syncs and offloads."""
    while True:
        try:
            await asyncio.sleep(SYNC_INTERVAL)
            cleanup_expired_acks()
            if not radio_manager.is_connected:
                continue

            try:
                async with radio_manager.radio_operation(
                    "periodic_sync",
                    blocking=False,
                ) as mc:
                    if await should_run_full_periodic_sync(mc):
                        await sync_and_offload_all(mc)
                    await sync_radio_time(mc)
            except RadioOperationBusyError:
                logger.debug("Skipping periodic sync: radio busy")
        except asyncio.CancelledError:
            logger.info("Periodic sync task cancelled")
            break
        except Exception as e:
            logger.error("Error in periodic sync: %s", e, exc_info=True)


def start_periodic_sync():
    """Start the periodic sync background task."""
    global _sync_task
    if _sync_task is None or _sync_task.done():
        _sync_task = asyncio.create_task(_periodic_sync_loop())
        logger.info("Started periodic radio sync (interval: %ds)", SYNC_INTERVAL)


async def stop_periodic_sync():
    """Stop the periodic sync background task."""
    global _sync_task
    if _sync_task and not _sync_task.done():
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
        _sync_task = None
        logger.info("Stopped periodic radio sync")


# Throttling for contact sync to radio
_last_contact_sync: float = 0.0
CONTACT_SYNC_THROTTLE_SECONDS = 30  # Don't sync more than once per 30 seconds


async def _sync_contacts_to_radio_inner(mc: MeshCore) -> dict:
    """
    Core logic for loading contacts onto the radio.

    Fill order is:
    1. Favorite contacts
    2. Most recently interacted-with non-repeaters
    3. Most recently advert-heard non-repeaters without interaction history

    Favorite contacts are always reloaded first, up to the configured capacity.
    Additional non-favorite fill stops at the refill target (80% of capacity).

    Caller must hold the radio operation lock and pass a valid MeshCore instance.
    """
    app_settings = await AppSettingsRepository.get()
    max_contacts = app_settings.max_radio_contacts
    refill_target, _full_sync_trigger = _compute_radio_contact_limits(max_contacts)
    selected_contacts: list[Contact] = []
    selected_keys: set[str] = set()

    favorite_contacts_loaded = 0
    for favorite in app_settings.favorites:
        if favorite.type != "contact":
            continue
        try:
            contact = await ContactRepository.get_by_key_or_prefix(favorite.id)
        except AmbiguousPublicKeyPrefixError:
            logger.warning(
                "Skipping favorite contact '%s': ambiguous key prefix; use full key",
                favorite.id,
            )
            continue
        if not contact:
            continue
        if len(contact.public_key) < 64:
            logger.debug(
                "Skipping unresolved prefix-only favorite contact '%s' for radio sync",
                favorite.id,
            )
            continue
        key = contact.public_key.lower()
        if key in selected_keys:
            continue
        selected_keys.add(key)
        selected_contacts.append(contact)
        favorite_contacts_loaded += 1
        if len(selected_contacts) >= max_contacts:
            break

    if len(selected_contacts) < refill_target:
        for contact in await ContactRepository.get_recently_contacted_non_repeaters(
            limit=max_contacts
        ):
            key = contact.public_key.lower()
            if key in selected_keys:
                continue
            selected_keys.add(key)
            selected_contacts.append(contact)
            if len(selected_contacts) >= refill_target:
                break

    if len(selected_contacts) < refill_target:
        for contact in await ContactRepository.get_recently_advertised_non_repeaters(
            limit=max_contacts
        ):
            key = contact.public_key.lower()
            if key in selected_keys:
                continue
            selected_keys.add(key)
            selected_contacts.append(contact)
            if len(selected_contacts) >= refill_target:
                break

    logger.debug(
        "Selected %d contacts to sync (%d favorites, refill_target=%d, capacity=%d)",
        len(selected_contacts),
        favorite_contacts_loaded,
        refill_target,
        max_contacts,
    )
    return await _load_contacts_to_radio(mc, selected_contacts)


async def ensure_contact_on_radio(
    public_key: str,
    *,
    force: bool = False,
    mc: MeshCore | None = None,
) -> dict:
    """Ensure one contact is loaded on the radio for ACK/routing support."""
    global _last_contact_sync

    now = time.time()
    if not force and (now - _last_contact_sync) < CONTACT_SYNC_THROTTLE_SECONDS:
        logger.debug(
            "Single-contact sync throttled (last sync %ds ago)",
            int(now - _last_contact_sync),
        )
        return {"loaded": 0, "throttled": True}

    try:
        contact = await ContactRepository.get_by_key_or_prefix(public_key)
    except AmbiguousPublicKeyPrefixError:
        logger.warning("Cannot sync favorite contact '%s': ambiguous key prefix", public_key)
        return {"loaded": 0, "error": "Ambiguous contact key prefix"}

    if not contact:
        logger.debug("Cannot sync favorite contact %s: not found", public_key[:12])
        return {"loaded": 0, "error": "Contact not found"}
    if len(contact.public_key) < 64:
        logger.debug("Cannot sync unresolved prefix-only contact %s to radio", public_key)
        return {"loaded": 0, "error": "Full contact key not yet known"}

    if mc is not None:
        _last_contact_sync = now
        return await _load_contacts_to_radio(mc, [contact])

    if not radio_manager.is_connected or radio_manager.meshcore is None:
        logger.debug("Cannot sync favorite contact to radio: not connected")
        return {"loaded": 0, "error": "Radio not connected"}

    try:
        async with radio_manager.radio_operation(
            "ensure_contact_on_radio",
            blocking=False,
        ) as mc:
            _last_contact_sync = now
            assert mc is not None
            return await _load_contacts_to_radio(mc, [contact])
    except RadioOperationBusyError:
        logger.debug("Skipping favorite contact sync: radio busy")
        return {"loaded": 0, "busy": True}
    except Exception as e:
        logger.error("Error syncing favorite contact to radio: %s", e, exc_info=True)
        return {"loaded": 0, "error": str(e)}


async def _load_contacts_to_radio(mc: MeshCore, contacts: list[Contact]) -> dict:
    """Load the provided contacts onto the radio."""
    loaded = 0
    already_on_radio = 0
    failed = 0

    for contact in contacts:
        if len(contact.public_key) < 64:
            logger.debug(
                "Skipping unresolved prefix-only contact %s during radio load", contact.public_key
            )
            continue
        radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
        if radio_contact:
            already_on_radio += 1
            if not contact.on_radio:
                await ContactRepository.set_on_radio(contact.public_key, True)
            continue

        try:
            radio_contact_payload = contact.to_radio_dict()
            result = await mc.commands.add_contact(radio_contact_payload)
            if result.type == EventType.OK:
                loaded += 1
                await ContactRepository.set_on_radio(contact.public_key, True)
                logger.debug("Loaded contact %s to radio", contact.public_key[:12])
            else:
                failed += 1
                reason = result.payload
                hint = ""
                if reason is None:
                    hint = (
                        " (no response from radio — if this repeats, check for "
                        "serial port contention from another process or try a "
                        "power cycle)"
                    )
                logger.warning(
                    "Failed to load contact %s: %s%s",
                    contact.public_key[:12],
                    reason,
                    hint,
                )
        except Exception as e:
            failed += 1
            logger.warning(
                "Error loading contact %s with fields=%s radio_payload=%s: %s",
                contact.public_key[:12],
                _contact_sync_debug_fields(contact),
                locals().get("radio_contact_payload"),
                e,
                exc_info=True,
            )

    if loaded > 0 or failed > 0:
        logger.info(
            "Contact sync: loaded %d, already on radio %d, failed %d",
            loaded,
            already_on_radio,
            failed,
        )

    return {
        "loaded": loaded,
        "already_on_radio": already_on_radio,
        "failed": failed,
    }


async def sync_recent_contacts_to_radio(force: bool = False, mc: MeshCore | None = None) -> dict:
    """
    Load contacts to the radio for DM ACK support.

    Fill order is favorites, then recently contacted non-repeaters,
    then recently advert-heard non-repeaters. Favorites are always reloaded
    up to the configured capacity; additional non-favorite fill stops at the
    80% refill target.
    Only runs at most once every CONTACT_SYNC_THROTTLE_SECONDS unless forced.

    Args:
        force: Skip the throttle check.
        mc: Optional MeshCore instance. When provided, the caller already holds
            the radio operation lock and the inner logic runs directly.
            When None, this function acquires its own lock.

    Returns counts of contacts loaded.
    """
    global _last_contact_sync

    # Throttle unless forced
    now = time.time()
    if not force and (now - _last_contact_sync) < CONTACT_SYNC_THROTTLE_SECONDS:
        logger.debug("Contact sync throttled (last sync %ds ago)", int(now - _last_contact_sync))
        return {"loaded": 0, "throttled": True}

    # If caller provided a MeshCore instance, use it directly (caller holds the lock)
    if mc is not None:
        _last_contact_sync = now
        assert mc is not None
        return await _sync_contacts_to_radio_inner(mc)

    if not radio_manager.is_connected or radio_manager.meshcore is None:
        logger.debug("Cannot sync contacts to radio: not connected")
        return {"loaded": 0, "error": "Radio not connected"}

    try:
        async with radio_manager.radio_operation(
            "sync_recent_contacts_to_radio",
            blocking=False,
        ) as mc:
            _last_contact_sync = now
            assert mc is not None
            return await _sync_contacts_to_radio_inner(mc)
    except RadioOperationBusyError:
        logger.debug("Skipping contact sync to radio: radio busy")
        return {"loaded": 0, "busy": True}

    except Exception as e:
        logger.error("Error syncing contacts to radio: %s", e, exc_info=True)
        return {"loaded": 0, "error": str(e)}
