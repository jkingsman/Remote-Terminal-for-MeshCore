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
from datetime import UTC, datetime, timedelta
from typing import Literal

from meshcore import EventType, MeshCore

from app.channel_constants import PUBLIC_CHANNEL_KEY, PUBLIC_CHANNEL_NAME
from app.config import settings
from app.event_handlers import cleanup_expired_acks, on_contact_message
from app.models import _VALID_CONTACT_TYPES, Contact, ContactUpsert
from app.radio import RadioOperationBusyError
from app.repository import (
    AmbiguousPublicKeyPrefixError,
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
    RepeaterTelemetryRepository,
)
from app.repository.contact_telemetry import ContactTelemetryRepository
from app.services.contact_reconciliation import (
    promote_prefix_contacts_for_contact,
    reconcile_contact_messages,
)
from app.services.messages import create_fallback_channel_message
from app.services.radio_runtime import radio_runtime as radio_manager
from app.telemetry_interval import clamp_telemetry_interval
from app.websocket import broadcast_error, broadcast_event

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHANNELS = 40
_GET_CONTACTS_TIMEOUT = 10

AdvertMode = Literal["flood", "zero_hop"]

_AUTO_ADD_OVERWRITE_OLDEST = 0x01
_RADIO_CONTACT_FAVORITE = 0x01


async def _enable_autoevict_on_radio(mc: MeshCore) -> bool:
    """Ensure the radio's AUTO_ADD_OVERWRITE_OLDEST preference bit is set."""
    try:
        current = await mc.commands.get_autoadd_config()
        if current is None or current.type == EventType.ERROR:
            logger.warning("Could not read autoadd config from radio: %s", current)
            return False
        current_flags = current.payload.get("config", 0)
        if current_flags & _AUTO_ADD_OVERWRITE_OLDEST:
            logger.debug("Radio autoevict already enabled (autoadd_config=0x%02x)", current_flags)
            return True
        new_flags = current_flags | _AUTO_ADD_OVERWRITE_OLDEST
        result = await mc.commands.set_autoadd_config(new_flags)
        if result is not None and result.type == EventType.OK:
            logger.info(
                "Enabled radio autoevict (autoadd_config 0x%02x -> 0x%02x)",
                current_flags,
                new_flags,
            )
            return True
        else:
            logger.warning("Failed to enable radio autoevict: %s", result)
            return False
    except Exception as exc:
        logger.warning("Error enabling radio autoevict: %s", exc)
        return False


def _contact_sync_debug_fields(contact: Contact) -> dict[str, object]:
    """Return key contact fields for sync failure diagnostics."""
    return {
        "type": contact.type,
        "flags": contact.flags,
        "direct_path": contact.direct_path,
        "direct_path_len": contact.direct_path_len,
        "direct_path_hash_mode": contact.direct_path_hash_mode,
        "direct_path_updated_at": contact.direct_path_updated_at,
        "route_override_path": contact.route_override_path,
        "route_override_len": contact.route_override_len,
        "route_override_hash_mode": contact.route_override_hash_mode,
        "last_advert": contact.last_advert,
        "lat": contact.lat,
        "lon": contact.lon,
    }


async def _reconcile_contact_messages_background(
    public_key: str,
    contact_name: str | None,
) -> None:
    """Run prefix promotion and contact/message reconciliation outside the radio critical path."""
    try:
        promoted_keys = await promote_prefix_contacts_for_contact(
            public_key=public_key,
            log=logger,
        )
        await reconcile_contact_messages(
            public_key=public_key,
            contact_name=contact_name,
            log=logger,
        )
        if promoted_keys:
            contact = await ContactRepository.get_by_key(public_key.lower())
            if contact is not None:
                for old_key in promoted_keys:
                    broadcast_event(
                        "contact_resolved",
                        {"previous_public_key": old_key, "contact": contact.model_dump()},
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

# Periodic telemetry collection task handle
_telemetry_collect_task: asyncio.Task | None = None

# Initial delay before the scheduler starts (let radio settle). After this,
# the loop wakes at each UTC top-of-hour and decides whether to run a cycle
# based on the user's telemetry_interval_hours preference, clamped up to
# the shortest-legal interval for the current tracked-repeater count.
TELEMETRY_COLLECT_INITIAL_DELAY = 60

# Room-poll task handle.
_room_poll_task: asyncio.Task | None = None

# How often the room poller wakes to look for due subscriptions. Individual
# rooms are polled on their own per-room interval; this is just the scan tick.
ROOM_POLL_CHECK_INTERVAL = 60

# Cap on the exponential backoff multiplier applied to a room's interval after
# consecutive failures (interval * min(2**errors, cap)). Bounds how far a
# flaky/offline room drifts out before it is retried again.
ROOM_POLL_MAX_BACKOFF = 8

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

# Startup/background contact reconciliation task handle
_contact_reconcile_task: asyncio.Task | None = None

# Periodic maintenance check interval in seconds (5 minutes)
SYNC_INTERVAL = 300

# Reload non-favorite contacts up to 80% of configured radio capacity after offload.
RADIO_CONTACT_REFILL_RATIO = 0.80

# Trigger a full offload/reload once occupancy reaches 95% of configured capacity.
RADIO_CONTACT_FULL_SYNC_RATIO = 0.95


def _effective_radio_capacity(configured: int) -> int:
    """Return the effective radio contact capacity.

    Uses the lower of the user-configured ``max_radio_contacts`` and the
    hardware limit reported by the radio at connect time.  The existing
    80% refill ratio already reserves headroom for the radio to
    organically add contacts it hears via adverts, so no additional
    reduction is applied here.
    """
    capacity = max(1, configured)
    hw_limit = radio_manager.max_contacts
    if hw_limit is not None:
        capacity = min(capacity, hw_limit)
    return max(1, capacity)


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
    capacity = _effective_radio_capacity(app_settings.max_radio_contacts)
    refill_target, full_sync_trigger = _compute_radio_contact_limits(capacity)

    result = await mc.commands.get_contacts(timeout=_GET_CONTACTS_TIMEOUT)
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

            radio_manager.remember_pending_message_channel_slot(key_hex, idx)
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

        logger.debug("Synced %d channels, cleared %d from radio", synced, cleared)

    except Exception as e:
        logger.error("Error during channel sync: %s", e)
        return {"synced": synced, "cleared": cleared, "error": str(e)}

    return {"synced": synced, "cleared": cleared}


def _split_channel_sender_and_text(text: str) -> tuple[str | None, str]:
    """Parse the canonical MeshCore "<sender>: <message>" channel text format."""
    sender = None
    message_text = text
    colon_idx = text.find(": ")
    if 0 < colon_idx < 50:
        potential_sender = text[:colon_idx]
        if not any(char in potential_sender for char in ":[]\x00"):
            sender = potential_sender
            message_text = text[colon_idx + 2 :]
    return sender, message_text


async def _resolve_channel_for_pending_message(
    mc: MeshCore,
    channel_idx: int,
) -> tuple[str | None, str | None]:
    """Resolve a pending channel message's slot to a channel key and name."""
    try:
        result = await mc.commands.get_channel(channel_idx)
    except Exception as exc:
        logger.debug("Failed to fetch channel slot %s for pending message: %s", channel_idx, exc)
    else:
        if result.type == EventType.CHANNEL_INFO:
            key_hex = await upsert_channel_from_radio_slot(result.payload, on_radio=False)
            if key_hex is not None:
                radio_manager.remember_pending_message_channel_slot(key_hex, channel_idx)
                return key_hex, result.payload.get("channel_name") or None

    current_slot_map = getattr(radio_manager, "_channel_key_by_slot", {})
    cached_key = current_slot_map.get(channel_idx)
    if cached_key is None:
        cached_key = radio_manager.get_pending_message_channel_key(channel_idx)
    if cached_key is None:
        return None, None

    channel = await ChannelRepository.get_by_key(cached_key)
    return cached_key, channel.name if channel else None


async def _store_pending_direct_message(event) -> None:
    """Route a CONTACT_MSG_RECV event pulled via get_msg() through the DM ingest path."""
    try:
        await on_contact_message(event)
    except Exception:
        logger.warning("Failed to store pending direct message", exc_info=True)


async def _store_pending_channel_message(mc: MeshCore, payload: dict) -> None:
    """Persist a CHANNEL_MSG_RECV event pulled via get_msg()."""
    channel_idx = payload.get("channel_idx")
    if channel_idx is None:
        logger.warning("Pending channel message missing channel_idx; dropping payload")
        return

    try:
        normalized_channel_idx = int(channel_idx)
    except (TypeError, ValueError):
        logger.warning("Pending channel message had invalid channel_idx=%r", channel_idx)
        return

    channel_key, channel_name = await _resolve_channel_for_pending_message(
        mc, normalized_channel_idx
    )
    if channel_key is None:
        logger.warning(
            "Could not resolve channel slot %d for pending message; message cannot be stored",
            normalized_channel_idx,
        )
        return

    received_at = int(time.time())
    ts = payload.get("sender_timestamp")
    sender_timestamp = ts if ts is not None else received_at
    sender_name, message_text = _split_channel_sender_and_text(payload.get("text", ""))

    await create_fallback_channel_message(
        conversation_key=channel_key,
        message_text=message_text,
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        path=payload.get("path"),
        path_len=payload.get("path_len"),
        txt_type=payload.get("txt_type", 0),
        sender_name=sender_name,
        channel_name=channel_name,
        broadcast_fn=broadcast_event,
    )


async def ensure_default_channels() -> None:
    """
    Ensure default channels exist in the database.
    These will be configured on the radio when needed for sending.

    This seeds the canonical Public channel row in the database if it is missing
    or misnamed. It does not make the channel undeletable through the router.
    """
    # Check by KEY (not name) since that's what's fixed
    existing = await ChannelRepository.get_by_key(PUBLIC_CHANNEL_KEY)
    if not existing or existing.name != PUBLIC_CHANNEL_NAME:
        logger.info("Ensuring default Public channel exists with correct name")
        await ChannelRepository.upsert(
            key=PUBLIC_CHANNEL_KEY,
            name=PUBLIC_CHANNEL_NAME,
            is_hashtag=False,
            on_radio=existing.on_radio if existing else False,
        )


async def sync_and_offload_all(mc: MeshCore) -> dict:
    """Run fast startup sync, then background contact reconcile."""
    autoevict_requested = settings.load_with_autoevict
    autoevict = False

    if autoevict_requested:
        autoevict = await _enable_autoevict_on_radio(mc)
        if not autoevict:
            logger.warning(
                "Autoevict requested but unavailable; falling back to snapshot-based "
                "background contact reconcile"
            )

    # Contact on_radio is legacy/stale metadata. Clear it during the offload/reload
    # cycle so old rows stop claiming radio residency we do not actively track.
    await ContactRepository.clear_on_radio_except([])

    contacts_result = await sync_contacts_from_radio(mc)
    channels_result = await sync_and_offload_channels(mc)

    # Ensure default channels exist
    await ensure_default_channels()

    snapshot_failed = "error" in contacts_result
    if snapshot_failed and not autoevict:
        logger.warning(
            "Radio contact snapshot failed — attempting best-effort contact "
            "loading without a full picture of what's already on the radio"
        )
        broadcast_error(
            "Could not enumerate radio contacts",
            "Loading favorites and recent contacts on a best-effort basis — "
            "some adds may be redundant or fail if the radio's contact table "
            "is already full. Set MESHCORE_LOAD_WITH_AUTOEVICT=true for more "
            "reliable loading without needing to read the radio first. "
            "See 'Contact Loading Issues' in the Advanced Setup documentation.",
        )

    start_background_contact_reconciliation(
        initial_radio_contacts=contacts_result.get("radio_contacts", {}),
        expected_mc=mc,
        autoevict=autoevict,
    )

    return {
        "contacts": contacts_result,
        "channels": channels_result,
        "contact_reconcile_started": True,
    }


async def drain_pending_messages(mc: MeshCore) -> int:
    """
    Drain all pending messages from the radio.

    Calls get_msg() repeatedly until NO_MORE_MSGS is received.
    Returns the count of messages retrieved.
    """
    count = 0

    while True:
        try:
            result = await mc.commands.get_msg(timeout=2.0)

            if result.type == EventType.NO_MORE_MSGS:
                break
            elif result.type == EventType.ERROR:
                logger.debug("Error during message drain: %s", result.payload)
                break
            elif result.type in (EventType.CONTACT_MSG_RECV, EventType.CHANNEL_MSG_RECV):
                if result.type == EventType.CHANNEL_MSG_RECV:
                    await _store_pending_channel_message(mc, result.payload)
                elif result.type == EventType.CONTACT_MSG_RECV:
                    await _store_pending_direct_message(result)
                count += 1

            # Small delay between fetches
            await asyncio.sleep(0.1)

        except TimeoutError:
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
            if result.type == EventType.CHANNEL_MSG_RECV:
                await _store_pending_channel_message(mc, result.payload)
            elif result.type == EventType.CONTACT_MSG_RECV:
                await _store_pending_direct_message(result)
            count += 1
            # If we got a message, there might be more - drain them
            count += await drain_pending_messages(mc)

    except TimeoutError:
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
        "[RADIO SYNC ERROR] A periodic radio audit discovered that the channel send-slot cache fell out of sync with radio state. This indicates that some other system, internal or external to the radio, has updated the channel slots on the radio (which the app assumes it has exclusive rights to, except on TCP-linked devices). The cache is resetting now, but you should review the README.md and consider using the environment variable MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE=true to make the radio use non-optimistic channel management and force-write the channel to radio before each send. This is a minor performance hit, but guarantees consistency. Mismatches found: %s",
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
                                    "[RADIO SYNC ERROR] Periodic radio audit caught %d message(s) that were not "
                                    "surfaced via event subscription. This means that the method of event (new contacts, messages, etc.) awareness we want isn't giving us everything. There is a fallback method available; see README.md and consider "
                                    "setting MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK=true to "
                                    "enable active radio polling every few seconds.",
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


async def send_advertisement(
    mc: MeshCore,
    *,
    force: bool = False,
    mode: AdvertMode = "flood",
) -> bool:
    """Send an advertisement to announce presence on the mesh.

    Respects the configured advert_interval - won't send if not enough time
    has elapsed since the last advertisement, unless force=True.

    Args:
        mc: The MeshCore instance to use for the advertisement.
        force: If True, send immediately regardless of interval.
        mode: Advertisement mode. Flood adverts use the persisted flood-advert
            throttle state; zero-hop adverts currently send immediately.

    Returns True if successful, False otherwise (including if throttled).
    """
    use_flood = mode == "flood"

    # Only flood adverts currently participate in persisted throttle state.
    if use_flood and not force:
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
        result = await mc.commands.send_advert(flood=use_flood)
        if result.type == EventType.OK:
            if use_flood:
                # Track flood advert timing for periodic/startup throttling.
                now = int(time.time())
                await AppSettingsRepository.update(last_advert_time=now)
            logger.info("%s advertisement sent successfully", mode.replace("_", "-"))
            return True
        else:
            logger.warning("Failed to send %s advertisement: %s", mode, result.payload)
            return False
    except Exception as e:
        logger.warning("Error sending %s advertisement: %s", mode, e, exc_info=True)
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


# Prevents reboot-loop: once we've rebooted to fix clock skew this session,
# don't do it again (the hardware RTC case can't be fixed by reboot).
_clock_reboot_attempted: bool = False
_CLOCK_WRAP_TARGET = 0xFFFFFFFF
_CLOCK_WRAP_POLL_INTERVAL = 0.2
_CLOCK_WRAP_TIMEOUT = 3.0


async def _query_radio_time(mc: MeshCore) -> int | None:
    """Return the radio's current epoch, or None if it can't be read."""
    try:
        result = await mc.commands.get_time()
    except Exception:
        return None
    if result.payload is None:
        return None
    value = result.payload.get("time")
    if isinstance(value, int):
        return value
    return None


async def _attempt_clock_wraparound(mc: MeshCore, *, now: int, observed_radio_time: int) -> bool:
    """Try the experimental uint32 wraparound trick, then retry normal time sync."""
    logger.warning(
        "Experimental __CLOWNTOWN_DO_CLOCK_WRAPAROUND enabled: attempting uint32 "
        "clock wraparound before normal time sync (radio=%d, system=%d).",
        observed_radio_time,
        now,
    )
    result = await mc.commands.set_time(_CLOCK_WRAP_TARGET)
    if result.type != EventType.OK:
        logger.warning(
            "Clock wraparound pre-step failed: set_time(%d) returned %s.",
            _CLOCK_WRAP_TARGET,
            result.type,
        )
        return False

    deadline = time.monotonic() + _CLOCK_WRAP_TIMEOUT
    wrapped_time: int | None = None
    while time.monotonic() < deadline:
        await asyncio.sleep(_CLOCK_WRAP_POLL_INTERVAL)
        wrapped_time = await _query_radio_time(mc)
        if wrapped_time is not None and wrapped_time < 60:
            break
    else:
        wrapped_time = None

    if wrapped_time is None:
        logger.warning(
            "Clock wraparound experiment did not observe a wrapped epoch within %.1f "
            "seconds; falling back to normal recovery.",
            _CLOCK_WRAP_TIMEOUT,
        )
        return False

    logger.warning(
        "Clock wraparound experiment observed wrapped epoch %d; retrying normal time sync.",
        wrapped_time,
    )
    retry = await mc.commands.set_time(now)
    if retry.type == EventType.OK:
        logger.warning("Clock sync succeeded after experimental wraparound.")
        return True

    logger.warning(
        "Clock sync still failed after experimental wraparound: set_time(%d) returned %s.",
        now,
        retry.type,
    )
    return False


async def sync_radio_time(mc: MeshCore, *, warn_on_failure: bool = True) -> bool:
    """Sync the radio's clock with the system time.

    The firmware only accepts forward time adjustments (new >= current).
    If the radio's clock is already ahead, set_time is silently rejected
    with an ERROR response.  We detect this by checking the response and,
    on failure, querying the radio's actual time so we can log the skew.

    When significant forward skew is detected for the first time in a
    session, the radio is rebooted so that boards with a volatile clock
    (most companion radios) reset to their default epoch and accept the
    correct time on the next connection setup.  The reboot is attempted
    only once; if it doesn't help (hardware RTC persists the wrong time),
    the skew is logged as a warning on subsequent syncs.

    ``warn_on_failure`` controls log severity for rejected/failed sync attempts.
    Startup and reconnect setup should leave this enabled so operators see the
    initial skew problem. Periodic maintenance syncs pass ``False`` to avoid
    repeating the same warning every few minutes after startup.

    Returns True if the radio accepted the new time, False otherwise.
    """
    global _clock_reboot_attempted  # noqa: PLW0603
    log_failure = logger.warning if warn_on_failure else logger.debug
    try:
        now = int(time.time())
        preflight_radio_time: int | None = None
        wraparound_attempted = False

        if settings.clowntown_do_clock_wraparound:
            preflight_radio_time = await _query_radio_time(mc)
            if preflight_radio_time is not None and preflight_radio_time > now:
                wraparound_attempted = True
                if await _attempt_clock_wraparound(
                    mc,
                    now=now,
                    observed_radio_time=preflight_radio_time,
                ):
                    return True

        result = await mc.commands.set_time(now)

        if result.type == EventType.OK:
            logger.debug("Synced radio time to %d", now)
            return True

        # Firmware rejected the time (most likely radio clock is ahead).
        # Query actual radio time so we can report the delta.
        radio_time = await _query_radio_time(mc)

        if radio_time is not None:
            delta = radio_time - now
            log_failure(
                "Radio rejected time sync: radio clock is %+d seconds "
                "(%+.1f hours) from system time (radio=%d, system=%d).",
                delta,
                delta / 3600.0,
                radio_time,
                now,
            )
        else:
            delta = None
            log_failure(
                "Radio rejected time sync (set_time returned %s) "
                "and get_time query failed; cannot determine clock skew.",
                result.type,
            )

        if (
            settings.clowntown_do_clock_wraparound
            and not wraparound_attempted
            and radio_time is not None
            and radio_time > now
            and await _attempt_clock_wraparound(
                mc,
                now=now,
                observed_radio_time=radio_time,
            )
        ):
            return True

        # If the clock is significantly ahead and we haven't already tried
        # a corrective reboot this session, reboot the radio.  Boards with
        # a volatile RTC (most companion radios) will reset their clock on
        # reboot, allowing the next post-connect sync to succeed.
        if not _clock_reboot_attempted and (delta is None or delta > 30):
            _clock_reboot_attempted = True
            log_failure(
                "Rebooting radio to reset clock skew.  Boards with a "
                "volatile RTC will accept the correct time after restart."
            )
            try:
                await mc.commands.reboot()
            except Exception:
                log_failure("Reboot command failed", exc_info=True)
        elif _clock_reboot_attempted:
            logger.debug(
                "Clock skew persists after reboot (hardware RTC); ignoring until next session."
            )

        return False
    except Exception as e:
        log_failure("Failed to sync radio time: %s", e, exc_info=True)
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
                    await sync_radio_time(mc, warn_on_failure=False)
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
CONTACT_RECONCILE_BATCH_SIZE = 2
CONTACT_RECONCILE_YIELD_SECONDS = 0.05
CONTACT_RECONCILE_BUSY_BACKOFF_SECONDS = 2.0


def _evict_removed_contact_from_library_cache(mc: MeshCore, public_key: str) -> None:
    """Keep the library's contact cache consistent after a successful removal."""
    # LIBRARY INTERNAL FIXUP: The MeshCore library's remove_contact() sends the
    # remove command over the wire but does NOT update the library's in-memory
    # contact cache (mc._contacts). This is a gap in the library — there's no
    # public API to clear a single contact from the cache, and the library only
    # refreshes it on a full get_contacts() call.
    #
    # Why this matters: contact sync and targeted ensure/load paths use
    # mc.get_contact_by_key_prefix() to check whether a contact is already
    # loaded on the radio. That method searches mc._contacts. If we don't evict
    # the removed contact from the cache here, later syncs will still find it
    # and skip add_contact() calls, leaving the radio without the contact even
    # though the app thinks it is resident.
    mc._contacts.pop(public_key, None)


def _normalize_radio_contacts_payload(contacts: dict | None) -> dict[str, dict]:
    """Return radio contacts keyed by normalized lowercase full public key."""
    normalized: dict[str, dict] = {}
    for public_key, contact_data in (contacts or {}).items():
        normalized[str(public_key).lower()] = contact_data
    return normalized


async def sync_contacts_from_radio(mc: MeshCore) -> dict:
    """Pull contacts from the radio and persist them to the database without removing them."""
    synced = 0

    try:
        result = await mc.commands.get_contacts(timeout=_GET_CONTACTS_TIMEOUT)

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
            return {"synced": 0, "radio_contacts": {}, "error": str(result)}

        contacts = _normalize_radio_contacts_payload(result.payload)
        logger.debug("Found %d contacts on radio", len(contacts))

        for public_key, contact_data in contacts.items():
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

        logger.debug("Synced %d contacts from radio snapshot", synced)

        # Import radio-favorited contacts into app favorites.
        # Only trust the favorite bit on contacts with a valid type (0-4);
        # garbled radio data can have junk flags with bit 0 set.
        radio_fav_keys = [
            pk
            for pk, data in contacts.items()
            if data.get("flags", 0) & 0x01 and data.get("type", -1) in _VALID_CONTACT_TYPES
        ]
        if radio_fav_keys:
            try:
                imported = 0
                for pk in radio_fav_keys:
                    existing = await ContactRepository.get_by_key(pk)
                    if existing and not existing.favorite:
                        await ContactRepository.set_favorite(pk, True)
                        imported += 1
                if imported:
                    logger.info("Imported %d radio favorite(s) into app favorites", imported)
            except Exception as e:
                logger.warning("Failed to import radio favorites: %s", e)

        return {"synced": synced, "radio_contacts": contacts}
    except Exception as e:
        logger.error("Error during contact snapshot sync: %s", e)
        return {"synced": synced, "radio_contacts": {}, "error": str(e)}


async def _reconcile_radio_contacts_in_background(
    *,
    initial_radio_contacts: dict[str, dict],
    expected_mc: MeshCore,
    autoevict: bool = False,
) -> None:
    """Converge radio contacts toward the desired favorites+recents working set.

    When *autoevict* is ``True`` the removal phase is skipped entirely and the
    desired working set is blind-refreshed. Re-adding the full desired list
    refreshes each contact's recency on supported firmware, so one successful
    full pass converges the radio toward the desired working set without relying
    on a stale contact snapshot.
    """
    radio_contacts = dict(initial_radio_contacts)
    removed = 0
    loaded = 0
    failed = 0
    table_full = False
    autoevict_next_index = 0
    autoevict_full_pass_retries = 0
    _MAX_AUTOEVICT_RETRIES = 3

    try:
        while True:
            if not radio_manager.is_connected or radio_manager.meshcore is not expected_mc:
                logger.info("Stopping background contact reconcile: radio transport changed")
                break

            # Pre-lock snapshot for quick-exit checks; authoritative list is
            # re-fetched inside the radio lock below.
            selected_contacts = await get_contacts_selected_for_radio_sync()
            desired_fill_contacts = [
                contact for contact in selected_contacts if len(contact.public_key) >= 64
            ]

            if autoevict:
                if not desired_fill_contacts:
                    logger.info(
                        "Background contact blind fill complete: no desired contacts selected"
                    )
                    break
                if autoevict_next_index >= len(desired_fill_contacts):
                    autoevict_next_index = 0
            desired_contacts = {
                contact.public_key.lower(): contact for contact in desired_fill_contacts
            }
            removable_keys = (
                [] if autoevict else [key for key in radio_contacts if key not in desired_contacts]
            )
            missing_contacts = [
                contact for key, contact in desired_contacts.items() if key not in radio_contacts
            ]

            if not autoevict and not removable_keys and not missing_contacts:
                logger.info(
                    "Background contact reconcile complete: %d contacts on radio working set",
                    len(radio_contacts),
                )
                break

            progressed = False
            autoevict_pass_complete = False
            autoevict_pass_failed = False
            try:
                async with radio_manager.radio_operation(
                    "background_contact_reconcile",
                    blocking=False,
                ) as mc:
                    if mc is not expected_mc:
                        logger.info(
                            "Stopping background contact reconcile: radio transport changed"
                        )
                        break

                    budget = CONTACT_RECONCILE_BATCH_SIZE
                    selected_contacts = await get_contacts_selected_for_radio_sync()
                    desired_fill_contacts = [
                        contact for contact in selected_contacts if len(contact.public_key) >= 64
                    ]
                    if autoevict and autoevict_next_index >= len(desired_fill_contacts):
                        autoevict_next_index = 0
                    desired_contacts = {
                        contact.public_key.lower(): contact for contact in desired_fill_contacts
                    }

                    if not autoevict:
                        for public_key in list(radio_contacts):
                            if budget <= 0:
                                break
                            if public_key in desired_contacts:
                                continue

                            remove_payload = (
                                mc.get_contact_by_key_prefix(public_key[:12])
                                or radio_contacts.get(public_key)
                                or {"public_key": public_key}
                            )
                            try:
                                remove_result = await mc.commands.remove_contact(remove_payload)
                            except Exception as exc:
                                failed += 1
                                budget -= 1
                                logger.warning(
                                    "Error removing contact %s during background reconcile: %s",
                                    public_key[:12],
                                    exc,
                                )
                                continue

                            budget -= 1
                            not_found = (
                                remove_result.type != EventType.OK
                                and isinstance(remove_result.payload, dict)
                                and remove_result.payload.get("error_code") == 2
                            )
                            if remove_result.type == EventType.OK or not_found:
                                radio_contacts.pop(public_key, None)
                                _evict_removed_contact_from_library_cache(mc, public_key)
                                removed += 1
                                progressed = True
                            else:
                                failed += 1
                                logger.warning(
                                    "Failed to remove contact %s during background reconcile: %s",
                                    public_key[:12],
                                    remove_result.payload,
                                )

                    if budget > 0:
                        if autoevict:
                            # Budget is consumed by the slice bound rather than
                            # per-operation decrement — autoevict skips the
                            # removal phase so the full budget is always available.
                            batch_contacts = desired_fill_contacts[
                                autoevict_next_index : autoevict_next_index + budget
                            ]
                            processed_contacts = 0
                            for contact in batch_contacts:
                                public_key = contact.public_key.lower()
                                try:
                                    add_payload = contact.to_radio_dict()
                                    # In autoevict mode, app-loaded contacts should
                                    # remain evictable by the radio even if the
                                    # stored contact record carries the favorite bit.
                                    add_payload["flags"] = (
                                        int(add_payload.get("flags", 0)) & ~_RADIO_CONTACT_FAVORITE
                                    )
                                    add_result = await mc.commands.add_contact(add_payload)
                                except Exception as exc:
                                    failed += 1
                                    logger.warning(
                                        "Error blind-filling contact %s during background reconcile: %s",
                                        public_key[:12],
                                        exc,
                                        exc_info=True,
                                    )
                                    autoevict_pass_failed = True
                                    processed_contacts += 1
                                    continue

                                if add_result.type == EventType.OK:
                                    radio_contacts[public_key] = add_payload
                                    loaded += 1
                                    progressed = True
                                else:
                                    failed += 1
                                    autoevict_pass_failed = True
                                    reason = add_result.payload
                                    if isinstance(reason, dict) and reason.get("error_code") == 3:
                                        logger.warning(
                                            "Radio contact table full — stopping "
                                            "contact reconcile (loaded %d this cycle)",
                                            loaded,
                                        )
                                        table_full = True
                                        break
                                    hint = ""
                                    if reason is None:
                                        hint = (
                                            " (no response from radio — if this repeats, check for "
                                            "serial port contention from another process or try a "
                                            "power cycle)"
                                        )
                                    logger.warning(
                                        "Failed to blind-fill contact %s during background reconcile: %s%s",
                                        public_key[:12],
                                        reason,
                                        hint,
                                    )
                                processed_contacts += 1

                            autoevict_next_index += processed_contacts
                            autoevict_pass_complete = autoevict_next_index >= len(
                                desired_fill_contacts
                            )
                        else:
                            for public_key, contact in desired_contacts.items():
                                if budget <= 0:
                                    break
                                if public_key in radio_contacts:
                                    continue

                                if mc.get_contact_by_key_prefix(public_key[:12]):
                                    radio_contacts[public_key] = {"public_key": public_key}
                                    continue

                                try:
                                    add_payload = contact.to_radio_dict()
                                    add_result = await mc.commands.add_contact(add_payload)
                                except Exception as exc:
                                    failed += 1
                                    budget -= 1
                                    logger.warning(
                                        "Error adding contact %s during background reconcile: %s",
                                        public_key[:12],
                                        exc,
                                        exc_info=True,
                                    )
                                    continue

                                budget -= 1
                                if add_result.type == EventType.OK:
                                    radio_contacts[public_key] = add_payload
                                    loaded += 1
                                    progressed = True
                                else:
                                    failed += 1
                                    reason = add_result.payload
                                    if isinstance(reason, dict) and reason.get("error_code") == 3:
                                        logger.warning(
                                            "Radio contact table full — stopping "
                                            "contact reconcile (loaded %d this cycle)",
                                            loaded,
                                        )
                                        table_full = True
                                        break
                                    hint = ""
                                    if reason is None:
                                        hint = (
                                            " (no response from radio — if this repeats, check for "
                                            "serial port contention from another process or try a "
                                            "power cycle)"
                                        )
                                    logger.warning(
                                        "Failed to add contact %s during background reconcile: %s%s",
                                        public_key[:12],
                                        reason,
                                        hint,
                                    )
            except RadioOperationBusyError:
                logger.debug("Background contact reconcile yielding: radio busy")
                await asyncio.sleep(CONTACT_RECONCILE_BUSY_BACKOFF_SECONDS)
                continue

            if table_full:
                if autoevict:
                    logger.error(
                        "We're expecting the radio to be in AUTO_ADD_OVERWRITE_OLDEST mode, "
                        "so a full-table error means we have no idea what is going on with "
                        "this radio; it is misbehaving. You should consider DM auto-acking "
                        "to be unreliable and/or not working for this radio. Sending and "
                        "receiving messages are not impacted by this error unless other "
                        "things are broken on your radio."
                    )
                    broadcast_error(
                        "Could not load all desired contacts onto the radio for auto-DM ack",
                        "Despite having auto-evict enabled, we got a contact-table-full error "
                        "from your radio. DM auto-ack is likely unavailable.",
                    )
                else:
                    normal_table_full_message = (
                        "The radio's contact table is full. Clearing your radio contacts "
                        "using another client, lowering your contact fill target in "
                        "settings, or setting MESHCORE_LOAD_WITH_AUTOEVICT=true may "
                        "relieve this. See 'Contact Loading Issues' in the Advanced "
                        "README.md"
                    )
                    logger.error(
                        "Contact reconcile hit TABLE_FULL. %s",
                        normal_table_full_message,
                    )
                    broadcast_error(
                        "Could not load all desired contacts onto the radio for auto-DM ack",
                        normal_table_full_message,
                    )
                break

            if autoevict and autoevict_pass_complete:
                if autoevict_pass_failed:
                    autoevict_full_pass_retries += 1
                    if autoevict_full_pass_retries >= _MAX_AUTOEVICT_RETRIES:
                        logger.warning(
                            "Background contact blind fill giving up after %d full passes "
                            "with persistent failures (loaded %d, failed %d)",
                            autoevict_full_pass_retries,
                            loaded,
                            failed,
                        )
                        break
                    autoevict_next_index = 0
                else:
                    logger.info(
                        "Background contact blind fill complete: refreshed %d desired contacts",
                        len(desired_fill_contacts),
                    )
                    break

            await asyncio.sleep(CONTACT_RECONCILE_YIELD_SECONDS)
            if not progressed:
                continue
    except asyncio.CancelledError:
        logger.info("Background contact reconcile task cancelled")
        raise
    except Exception as exc:
        logger.error("Background contact reconcile failed: %s", exc, exc_info=True)
    finally:
        if removed > 0 or loaded > 0 or failed > 0:
            logger.info(
                "Background contact reconcile summary: removed %d, loaded %d, failed %d",
                removed,
                loaded,
                failed,
            )


def start_background_contact_reconciliation(
    *,
    initial_radio_contacts: dict[str, dict],
    expected_mc: MeshCore,
    autoevict: bool = False,
) -> None:
    """Start or replace the background contact reconcile task for the current radio."""
    global _contact_reconcile_task

    if _contact_reconcile_task is not None and not _contact_reconcile_task.done():
        _contact_reconcile_task.cancel()

    _contact_reconcile_task = asyncio.create_task(
        _reconcile_radio_contacts_in_background(
            initial_radio_contacts=initial_radio_contacts,
            expected_mc=expected_mc,
            autoevict=autoevict,
        )
    )
    logger.info(
        "Started background contact reconcile for %d radio contact(s)%s",
        len(initial_radio_contacts),
        " (autoevict mode)" if autoevict else "",
    )


async def stop_background_contact_reconciliation() -> None:
    """Stop the background contact reconcile task."""
    global _contact_reconcile_task

    if _contact_reconcile_task and not _contact_reconcile_task.done():
        _contact_reconcile_task.cancel()
        try:
            await _contact_reconcile_task
        except asyncio.CancelledError:
            pass
    _contact_reconcile_task = None


async def get_contacts_selected_for_radio_sync() -> list[Contact]:
    """Return the contacts that would be loaded onto the radio right now.

    Fill order:
    1. Favorites (up to full capacity)
    2. Most recently DM-active non-repeaters (sent or received, up to 80% refill target)
    3. Most recently advertised non-repeaters (up to 80% refill target)
    """
    app_settings = await AppSettingsRepository.get()
    max_contacts = _effective_radio_capacity(app_settings.max_radio_contacts)
    refill_target, _full_sync_trigger = _compute_radio_contact_limits(max_contacts)
    selected_contacts: list[Contact] = []
    selected_keys: set[str] = set()

    # Favorites first — always loaded up to max_contacts
    favorite_contacts_loaded = 0
    for contact in await ContactRepository.get_favorites():
        key = contact.public_key.lower()
        if key in selected_keys:
            continue
        selected_keys.add(key)
        selected_contacts.append(contact)
        favorite_contacts_loaded += 1
        if len(selected_contacts) >= max_contacts:
            break

    if len(selected_contacts) < refill_target:
        for contact in await ContactRepository.get_recently_dm_active_non_repeaters(
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
    return selected_contacts


async def _sync_contacts_to_radio_inner(mc: MeshCore) -> dict:
    """
    Core logic for loading contacts onto the radio.

    Fill order is:
    1. Favorite contacts
    2. Most recently DM-active non-repeaters (sent or received)
    3. Most recently advert-heard non-repeaters

    Favorite contacts are always reloaded first, up to the configured capacity.
    Additional non-favorite fill stops at the refill target (80% of capacity).

    Caller must hold the radio operation lock and pass a valid MeshCore instance.
    """
    selected_contacts = await get_contacts_selected_for_radio_sync()
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
            continue

        try:
            radio_contact_payload = contact.to_radio_dict()
            result = await mc.commands.add_contact(radio_contact_payload)
            if result.type == EventType.OK:
                loaded += 1
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

    Fill order is favorites, then recently DM-active non-repeaters (sent or
    received), then recently advert-heard non-repeaters. Favorites are always reloaded
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


# ---------------------------------------------------------------------------
# Periodic repeater telemetry collection
# ---------------------------------------------------------------------------


async def _collect_repeater_telemetry(mc: MeshCore, contact: Contact) -> bool:
    """Fetch status telemetry from a single repeater and record it.

    Returns True on success, False on failure (logged, not raised).
    """
    try:
        await mc.commands.add_contact(contact.to_radio_dict())
        status = await mc.commands.req_status_sync(contact.public_key, timeout=10, min_timeout=5)
    except Exception as e:
        logger.debug(
            "Telemetry collect: radio command failed for %s: %s",
            contact.public_key[:12],
            e,
        )
        return False

    if status is None:
        logger.debug("Telemetry collect: no response from %s", contact.public_key[:12])
        return False

    # Map to the same field names as the manual repeater status endpoint
    data = {
        "battery_volts": status.get("bat", 0) / 1000.0,
        "tx_queue_len": status.get("tx_queue_len", 0),
        "noise_floor_dbm": status.get("noise_floor", 0),
        "last_rssi_dbm": status.get("last_rssi", 0),
        "last_snr_db": status.get("last_snr", 0.0),
        "packets_received": status.get("nb_recv", 0),
        "packets_sent": status.get("nb_sent", 0),
        "airtime_seconds": status.get("airtime", 0),
        "rx_airtime_seconds": status.get("rx_airtime", 0),
        "uptime_seconds": status.get("uptime", 0),
        "sent_flood": status.get("sent_flood", 0),
        "sent_direct": status.get("sent_direct", 0),
        "recv_flood": status.get("recv_flood", 0),
        "recv_direct": status.get("recv_direct", 0),
        "flood_dups": status.get("flood_dups", 0),
        "direct_dups": status.get("direct_dups", 0),
        "full_events": status.get("full_evts", 0),
        "recv_errors": status.get("recv_errors"),
    }

    # Best-effort LPP sensor fetch — failure here does not fail the overall
    # collection; status telemetry is still recorded without sensor data.
    try:
        lpp_raw = await mc.commands.req_telemetry_sync(
            contact.public_key, timeout=10, min_timeout=5
        )
        if lpp_raw:
            lpp_sensors = []
            for entry in lpp_raw:
                value = entry.get("value", 0)
                # Skip multi-value sensors (GPS, accelerometer, etc.)
                if isinstance(value, dict):
                    continue
                lpp_sensors.append(
                    {
                        "channel": entry.get("channel", 0),
                        "type_name": str(entry.get("type", "unknown")),
                        "value": value,
                    }
                )
            if lpp_sensors:
                data["lpp_sensors"] = lpp_sensors
    except Exception as e:
        logger.debug(
            "Telemetry collect: LPP sensor fetch failed for %s (non-fatal): %s",
            contact.public_key[:12],
            e,
        )

    try:
        timestamp = int(time.time())
        await RepeaterTelemetryRepository.record(
            public_key=contact.public_key,
            timestamp=timestamp,
            data=data,
        )
        logger.info(
            "Telemetry collect: recorded snapshot for %s (%s)",
            contact.name or contact.public_key[:12],
            contact.public_key[:12],
        )

        # Dispatch to fanout modules (e.g. HA MQTT discovery)
        from app.fanout.manager import fanout_manager

        asyncio.create_task(
            fanout_manager.broadcast_telemetry(
                {
                    "public_key": contact.public_key,
                    "name": contact.name or contact.public_key[:12],
                    "timestamp": timestamp,
                    **data,
                }
            )
        )

        return True
    except Exception as e:
        logger.warning(
            "Telemetry collect: failed to record for %s: %s",
            contact.public_key[:12],
            e,
        )
        return False


async def _collect_contact_telemetry(mc: MeshCore, contact: Contact) -> bool:
    """Fetch LPP telemetry from a non-repeater contact and record it.

    Unlike repeaters, companions/rooms/sensors only respond to
    req_telemetry_sync (LPP), not req_status_sync (repeater status struct).
    All sensor values including multi-value (GPS, accel) are stored.

    Returns True on success, False on failure (logged, not raised).
    """
    try:
        await mc.commands.add_contact(contact.to_radio_dict())
        lpp_raw = await mc.commands.req_telemetry_sync(
            contact.public_key, timeout=10, min_timeout=5
        )
    except Exception as e:
        logger.debug(
            "Contact telemetry collect: radio command failed for %s: %s",
            contact.public_key[:12],
            e,
        )
        return False

    if lpp_raw is None:
        logger.debug("Contact telemetry collect: no response from %s", contact.public_key[:12])
        return False

    lpp_sensors = []
    for entry in lpp_raw:
        lpp_sensors.append(
            {
                "channel": entry.get("channel", 0),
                "type_name": str(entry.get("type", "unknown")),
                "value": entry.get("value", 0),
            }
        )

    data: dict = {}
    if lpp_sensors:
        data["lpp_sensors"] = lpp_sensors

    try:
        timestamp = int(time.time())
        await ContactTelemetryRepository.record(
            public_key=contact.public_key,
            timestamp=timestamp,
            data=data,
        )
        logger.info(
            "Contact telemetry collect: recorded snapshot for %s (%s)",
            contact.name or contact.public_key[:12],
            contact.public_key[:12],
        )

        # Dispatch to fanout modules
        from app.fanout.manager import fanout_manager

        asyncio.create_task(
            fanout_manager.broadcast_telemetry(
                {
                    "public_key": contact.public_key,
                    "name": contact.name or contact.public_key[:12],
                    "timestamp": timestamp,
                    **data,
                }
            )
        )

        return True
    except Exception as e:
        logger.warning(
            "Contact telemetry collect: failed to record for %s: %s",
            contact.public_key[:12],
            e,
        )
        return False


async def _run_telemetry_cycle(
    *,
    routed_only: bool = False,
    collect_repeaters: bool = True,
    collect_contacts: bool = True,
) -> None:
    """Collect one telemetry sample from tracked repeaters and/or contacts.

    When *routed_only* is True, only targets whose effective route is
    ``"direct"`` or ``"override"`` (i.e. not ``"flood"``) are collected.
    This is used by the hourly routed-path fast-poll feature.

    *collect_repeaters* and *collect_contacts* allow the scheduler to
    selectively skip one list when its interval hasn't elapsed yet.
    """
    if not radio_manager.is_connected:
        logger.debug("Telemetry collect: radio not connected, skipping cycle")
        return

    app_settings = await AppSettingsRepository.get()
    tracked_repeaters = app_settings.tracked_telemetry_repeaters if collect_repeaters else []
    tracked_contacts = app_settings.tracked_telemetry_contacts if collect_contacts else []
    if not tracked_repeaters and not tracked_contacts:
        return

    # Build repeater candidates
    candidates: list[tuple[str, Contact, bool]] = []  # (key, contact, is_repeater)
    for pub_key in tracked_repeaters:
        contact = await ContactRepository.get_by_key(pub_key)
        if not contact or contact.type != 2:
            logger.debug(
                "Telemetry collect: skipping %s (not found or not repeater)",
                pub_key[:12],
            )
            continue
        if routed_only and (not contact.effective_route or contact.effective_route.path_len < 0):
            continue
        candidates.append((pub_key, contact, True))

    # Build contact (non-repeater) candidates
    for pub_key in tracked_contacts:
        contact = await ContactRepository.get_by_key(pub_key)
        if not contact:
            logger.debug(
                "Telemetry collect: skipping contact %s (not found)",
                pub_key[:12],
            )
            continue
        if routed_only and (not contact.effective_route or contact.effective_route.path_len < 0):
            continue
        candidates.append((pub_key, contact, False))

    if not candidates:
        if routed_only:
            logger.debug("Telemetry collect: no routed targets to poll this hour")
        return

    label = "routed" if routed_only else "full"
    logger.info(
        "Telemetry collect: starting %s cycle for %d target(s)",
        label,
        len(candidates),
    )
    collected = 0

    for _pub_key, contact, is_repeater in candidates:
        try:
            async with radio_manager.radio_operation(
                "telemetry_collect",
                blocking=False,
                suspend_auto_fetch=True,
            ) as mc:
                if is_repeater:
                    success = await _collect_repeater_telemetry(mc, contact)
                else:
                    success = await _collect_contact_telemetry(mc, contact)
                if success:
                    collected += 1
        except RadioOperationBusyError:
            logger.debug(
                "Telemetry collect: radio busy, skipping %s",
                contact.public_key[:12],
            )

    logger.info(
        "Telemetry collect: %s cycle complete, %d/%d successful",
        label,
        collected,
        len(candidates),
    )


async def _sleep_until_next_utc_top_of_hour() -> None:
    """Sleep until the next UTC top-of-hour (or a minimum of 1 second)."""
    now = datetime.now(UTC)
    next_top = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    delay = (next_top - now).total_seconds()
    if delay < 1:
        delay = 1
    await asyncio.sleep(delay)


async def _maybe_run_scheduled_cycle(now: datetime) -> None:
    """Evaluate the modulo gate for the given UTC time and run a cycle if due.

    Factored out of the loop so we can also invoke it immediately after the
    post-boot initial delay — otherwise a restart within the initial-delay
    window before a scheduled boundary would carry the task past that boundary
    and skip a due cycle (for 24h cadence users, that's a full day of missed
    telemetry).
    """
    app_settings = await AppSettingsRepository.get()
    n_repeaters = len(app_settings.tracked_telemetry_repeaters)
    n_contacts = len(app_settings.tracked_telemetry_contacts)
    if n_repeaters == 0 and n_contacts == 0:
        return

    pref = app_settings.telemetry_interval_hours
    routed_hourly = app_settings.telemetry_routed_hourly

    # Each list has its own 24/day ceiling.  Check eligibility independently
    # so 8 repeaters on an 8h interval don't drag 1 contact to 8h too.
    repeaters_due = False
    contacts_due = False

    if n_repeaters > 0:
        eff_rep = clamp_telemetry_interval(pref, n_repeaters)
        if now.hour % eff_rep == 0:
            repeaters_due = True

    if n_contacts > 0:
        eff_ct = clamp_telemetry_interval(pref, n_contacts)
        if now.hour % eff_ct == 0:
            contacts_due = True

    if repeaters_due or contacts_due:
        await _run_telemetry_cycle(
            collect_repeaters=repeaters_due,
            collect_contacts=contacts_due,
        )
    elif routed_hourly:
        await _run_telemetry_cycle(routed_only=True)


async def _telemetry_collect_loop() -> None:
    """Background task that runs tracked-repeater telemetry collection.

    After an initial post-boot delay we evaluate the modulo gate once
    (covers the edge case where the initial delay crossed a scheduled
    boundary on restart). Then we wake at every UTC top-of-hour and
    evaluate the gate again. A cycle runs only when
    ``current_utc_hour % effective_interval_hours == 0``, where the
    effective interval is the user preference clamped up to the shortest
    legal interval for the current tracked-repeater count. This keeps the
    total daily check count bounded at ``DAILY_CHECK_CEILING`` (24).

    The loop never updates the stored user preference. If the user picks a
    short interval and then adds repeaters that make it illegal, they keep
    their pick stored and we silently use the clamped value until they drop
    repeaters.

    Radio lock is acquired per-repeater (non-blocking) so manual ops can
    interleave. Failures are logged and skipped.
    """
    try:
        await asyncio.sleep(TELEMETRY_COLLECT_INITIAL_DELAY)
    except asyncio.CancelledError:
        logger.info("Telemetry collect task cancelled before initial delay")
        return

    # Post-boot boundary check: if the delay carried us into a matching hour
    # (or we booted exactly at a matching hour), run now rather than waiting
    # another full cycle.
    try:
        await _maybe_run_scheduled_cycle(datetime.now(UTC))
    except asyncio.CancelledError:
        logger.info("Telemetry collect task cancelled after initial delay")
        return
    except Exception as e:
        logger.error("Error in post-boot telemetry check: %s", e, exc_info=True)

    while True:
        try:
            await _sleep_until_next_utc_top_of_hour()
            await _maybe_run_scheduled_cycle(datetime.now(UTC))

        except asyncio.CancelledError:
            logger.info("Telemetry collect task cancelled")
            break
        except Exception as e:
            logger.error("Error in telemetry collect loop: %s", e, exc_info=True)


def start_telemetry_collect() -> None:
    """Start the periodic telemetry collection background task."""
    global _telemetry_collect_task
    if _telemetry_collect_task is None or _telemetry_collect_task.done():
        _telemetry_collect_task = asyncio.create_task(_telemetry_collect_loop())
        logger.info("Started periodic telemetry collection (UTC-hourly scheduler)")


async def stop_telemetry_collect() -> None:
    """Stop the periodic telemetry collection background task."""
    global _telemetry_collect_task
    if _telemetry_collect_task and not _telemetry_collect_task.done():
        _telemetry_collect_task.cancel()
        try:
            await _telemetry_collect_task
        except asyncio.CancelledError:
            pass
        _telemetry_collect_task = None
        logger.info("Stopped periodic telemetry collection")


def _room_poll_effective_interval(sub) -> int:
    """Per-room interval widened by exponential backoff on consecutive failures.

    A healthy room polls on its configured interval; a failing one backs off up
    to ROOM_POLL_MAX_BACKOFF× so an offline or misconfigured room does not burn
    airtime every cycle.
    """
    backoff = min(2**sub.consecutive_errors, ROOM_POLL_MAX_BACKOFF)
    return sub.interval_seconds * backoff


def _room_poll_is_due(sub, now: float) -> bool:
    if sub.last_poll_at is None:
        return True
    return (now - sub.last_poll_at) >= _room_poll_effective_interval(sub)


async def _poll_one_room(sub) -> None:
    """Log in to one room with its stored credential and drain its delta.

    The login is what makes the room server enqueue the messages posted since
    our last sync; the existing drain then captures them and dm_ingest stores
    them, deduped. We never trust a cursor — re-pulling an overlapping delta is
    made safe by the incoming-message dedup, so over-polling only wastes airtime,
    never duplicates a message.
    """
    from app.repository import ContactRepository
    from app.repository.room_poll import RoomPollRepository
    from app.routers.server_control import prepare_authenticated_contact_connection

    contact = await ContactRepository.get_by_key(sub.room_key)
    if contact is None:
        await RoomPollRepository.record_result(
            sub.room_key, ok=False, result="error", error="room contact not found"
        )
        return

    try:
        async with radio_manager.radio_operation(
            f"room_poll:{sub.room_key[:12]}",
            blocking=False,
            suspend_auto_fetch=True,
        ) as mc:
            # sub.credential is three-state; only pollable rows (credential IS
            # NOT NULL) reach here, and "" is a valid guest login. Never gate on
            # truthiness.
            login = await prepare_authenticated_contact_connection(
                mc,
                contact,
                sub.credential,
                label="room server",
            )
            if not login.authenticated:
                # An explicit rejection means the stored credential is wrong;
                # disable so we stop hammering the room with bad auth. A timeout
                # is transient — leave it enabled and let backoff space retries.
                if login.status == "error":
                    await RoomPollRepository.set_enabled(sub.room_key, False)
                    await RoomPollRepository.record_result(
                        sub.room_key,
                        ok=False,
                        result="login_failed",
                        error=(login.message or "login failed") + " — polling disabled",
                    )
                    logger.warning(
                        "Room poll login rejected for %s; disabled polling",
                        sub.room_key[:12],
                    )
                else:
                    await RoomPollRepository.record_result(
                        sub.room_key,
                        ok=False,
                        result=login.status,
                        error=login.message,
                    )
                return

            count = await poll_for_messages(mc)
            await RoomPollRepository.record_result(
                sub.room_key, ok=True, result=f"ok ({count} drained)", error=None
            )
            if count > 0:
                logger.info("Room poll %s drained %d message(s)", sub.room_key[:12], count)
    except RadioOperationBusyError:
        # Radio busy this cycle; do not count it as a failure — just retry next
        # scan without advancing backoff.
        logger.debug("Room poll skipped for %s: radio busy", sub.room_key[:12])
    except Exception as exc:
        await RoomPollRepository.record_result(
            sub.room_key, ok=False, result="error", error=f"{type(exc).__name__}: {exc}"
        )
        logger.warning("Room poll error for %s: %s", sub.room_key[:12], exc, exc_info=True)


async def _room_poll_loop() -> None:
    """Background task: periodically log in to subscribed rooms to pull deltas."""
    from app.repository.room_poll import RoomPollRepository

    while True:
        try:
            await asyncio.sleep(ROOM_POLL_CHECK_INTERVAL)

            if not radio_manager.is_connected or is_polling_paused():
                continue

            subs = await RoomPollRepository.get_pollable()
            now = time.time()
            for sub in subs:
                if not _room_poll_is_due(sub, now):
                    continue
                await _poll_one_room(sub)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Error in room poll loop: %s", e, exc_info=True)


def start_room_polling() -> None:
    """Start the periodic room-poll background task."""
    global _room_poll_task
    if _room_poll_task is None or _room_poll_task.done():
        _room_poll_task = asyncio.create_task(_room_poll_loop())
        logger.info("Started room poll task (scan interval: %ds)", ROOM_POLL_CHECK_INTERVAL)


async def stop_room_polling() -> None:
    """Stop the periodic room-poll background task."""
    global _room_poll_task
    if _room_poll_task and not _room_poll_task.done():
        _room_poll_task.cancel()
        try:
            await _room_poll_task
        except asyncio.CancelledError:
            pass
        _room_poll_task = None
        logger.info("Stopped room poll task")
