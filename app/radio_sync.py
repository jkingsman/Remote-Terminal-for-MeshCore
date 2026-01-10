"""
Radio sync and offload management.

This module handles syncing contacts and channels from the radio to the database,
then removing them from the radio to free up space for new discoveries.

Also handles loading recent non-repeater contacts TO the radio for DM ACK support.
Also handles periodic message polling as a fallback for platforms where push events
don't work reliably.
"""

import asyncio
import logging
import time

from meshcore import EventType

from app.config import settings
from app.models import CONTACT_TYPE_REPEATER, Contact
from app.radio import radio_manager
from app.repository import ChannelRepository, ContactRepository

logger = logging.getLogger(__name__)

# Message poll task handle
_message_poll_task: asyncio.Task | None = None

# Message poll interval in seconds
MESSAGE_POLL_INTERVAL = 5

# Background task handle
_sync_task: asyncio.Task | None = None

# Sync interval in seconds (5 minutes)
SYNC_INTERVAL = 300


async def sync_and_offload_contacts() -> dict:
    """
    Sync contacts from radio to database, then remove them from radio.
    Returns counts of synced and removed contacts.
    """
    if not radio_manager.is_connected or radio_manager.meshcore is None:
        logger.warning("Cannot sync contacts: radio not connected")
        return {"synced": 0, "removed": 0, "error": "Radio not connected"}

    mc = radio_manager.meshcore
    synced = 0
    removed = 0

    try:
        # Get all contacts from radio
        result = await mc.commands.get_contacts()

        if result is None or result.type == EventType.ERROR:
            logger.error("Failed to get contacts from radio: %s", result)
            return {"synced": 0, "removed": 0, "error": str(result)}

        contacts = result.payload or {}
        logger.info("Found %d contacts on radio", len(contacts))

        # Sync each contact to database, then remove from radio
        for public_key, contact_data in contacts.items():
            # Save to database
            await ContactRepository.upsert(
                Contact.from_radio_dict(public_key, contact_data, on_radio=False)
            )
            synced += 1

            # Remove from radio
            try:
                remove_result = await mc.commands.remove_contact(contact_data)
                if remove_result.type == EventType.OK:
                    removed += 1
                else:
                    logger.warning(
                        "Failed to remove contact %s: %s",
                        public_key[:12], remove_result.payload
                    )
            except Exception as e:
                logger.warning("Error removing contact %s: %s", public_key[:12], e)

        logger.info("Synced %d contacts, removed %d from radio", synced, removed)

    except Exception as e:
        logger.error("Error during contact sync: %s", e)
        return {"synced": synced, "removed": removed, "error": str(e)}

    return {"synced": synced, "removed": removed}


async def sync_and_offload_channels() -> dict:
    """
    Sync channels from radio to database, then clear them from radio.
    Returns counts of synced and cleared channels.
    """
    if not radio_manager.is_connected or radio_manager.meshcore is None:
        logger.warning("Cannot sync channels: radio not connected")
        return {"synced": 0, "cleared": 0, "error": "Radio not connected"}

    mc = radio_manager.meshcore
    synced = 0
    cleared = 0

    try:
        # Check all 40 channel slots
        for idx in range(40):
            result = await mc.commands.get_channel(idx)

            if result.type != EventType.CHANNEL_INFO:
                continue

            payload = result.payload
            name = payload.get("channel_name", "")
            secret = payload.get("channel_secret", b"")

            # Skip empty channels
            if not name or name == "\x00" * len(name) or all(b == 0 for b in secret):
                continue

            is_hashtag = name.startswith("#")

            # Convert key bytes to hex string
            key_bytes = secret if isinstance(secret, bytes) else bytes(secret)
            key_hex = key_bytes.hex().upper()

            # Save to database
            await ChannelRepository.upsert(
                key=key_hex,
                name=name,
                is_hashtag=is_hashtag,
                on_radio=False,  # We're about to clear it
            )
            synced += 1
            logger.debug("Synced channel %s: %s", key_hex[:8], name)

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
                    logger.warning(
                        "Failed to clear channel %d: %s",
                        idx, clear_result.payload
                    )
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
    """
    # Public channel - no hashtag, specific key
    PUBLIC_CHANNEL_KEY_HEX = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"

    existing = await ChannelRepository.get_by_name("Public")
    if not existing:
        logger.info("Creating default Public channel")
        await ChannelRepository.upsert(
            key=PUBLIC_CHANNEL_KEY_HEX,
            name="Public",
            is_hashtag=False,
            on_radio=False,
        )


async def sync_and_offload_all() -> dict:
    """Sync and offload both contacts and channels, then ensure defaults exist."""
    logger.info("Starting full radio sync and offload")

    contacts_result = await sync_and_offload_contacts()
    channels_result = await sync_and_offload_channels()

    # Ensure default channels exist
    await ensure_default_channels()

    return {
        "contacts": contacts_result,
        "channels": channels_result,
    }


async def drain_pending_messages() -> int:
    """
    Drain all pending messages from the radio.

    Calls get_msg() repeatedly until NO_MORE_MSGS is received.
    Returns the count of messages retrieved.
    """
    if not radio_manager.is_connected or radio_manager.meshcore is None:
        return 0

    mc = radio_manager.meshcore
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
            logger.debug("Error draining messages: %s", e)
            break

    return count


async def poll_for_messages() -> int:
    """
    Poll the radio for any pending messages (single pass).

    This is a fallback for platforms where MESSAGES_WAITING push events
    don't work reliably.

    Returns the count of messages retrieved.
    """
    if not radio_manager.is_connected or radio_manager.meshcore is None:
        return 0

    mc = radio_manager.meshcore
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
            count += await drain_pending_messages()

    except asyncio.TimeoutError:
        pass
    except Exception as e:
        logger.debug("Message poll exception: %s", e)

    return count


async def _message_poll_loop():
    """Background task that periodically polls for messages."""
    while True:
        try:
            await asyncio.sleep(MESSAGE_POLL_INTERVAL)

            if radio_manager.is_connected:
                await poll_for_messages()

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Error in message poll loop: %s", e)


def start_message_polling():
    """Start the periodic message polling background task."""
    global _message_poll_task
    if _message_poll_task is None or _message_poll_task.done():
        _message_poll_task = asyncio.create_task(_message_poll_loop())
        logger.info("Started periodic message polling (interval: %ds)", MESSAGE_POLL_INTERVAL)


def stop_message_polling():
    """Stop the periodic message polling background task."""
    global _message_poll_task
    if _message_poll_task and not _message_poll_task.done():
        _message_poll_task.cancel()


async def _periodic_sync_loop():
    """Background task that periodically syncs and offloads."""
    while True:
        try:
            await asyncio.sleep(SYNC_INTERVAL)
            logger.debug("Running periodic radio sync")
            await sync_and_offload_all()
        except asyncio.CancelledError:
            logger.info("Periodic sync task cancelled")
            break
        except Exception as e:
            logger.error("Error in periodic sync: %s", e)


def start_periodic_sync():
    """Start the periodic sync background task."""
    global _sync_task
    if _sync_task is None or _sync_task.done():
        _sync_task = asyncio.create_task(_periodic_sync_loop())
        logger.info("Started periodic radio sync (interval: %ds)", SYNC_INTERVAL)


def stop_periodic_sync():
    """Stop the periodic sync background task."""
    global _sync_task
    if _sync_task and not _sync_task.done():
        _sync_task.cancel()
        logger.info("Stopped periodic radio sync")


# Throttling for contact sync to radio
_last_contact_sync: float = 0.0
CONTACT_SYNC_THROTTLE_SECONDS = 30  # Don't sync more than once per 30 seconds


async def sync_recent_contacts_to_radio(force: bool = False) -> dict:
    """
    Load recent non-repeater contacts to the radio for DM ACK support.

    This ensures the radio can auto-ACK incoming DMs from recent contacts.
    Only runs at most once every CONTACT_SYNC_THROTTLE_SECONDS unless forced.

    Returns counts of contacts loaded.
    """
    global _last_contact_sync

    # Throttle unless forced
    now = time.time()
    if not force and (now - _last_contact_sync) < CONTACT_SYNC_THROTTLE_SECONDS:
        logger.debug("Contact sync throttled (last sync %ds ago)", int(now - _last_contact_sync))
        return {"loaded": 0, "throttled": True}

    if not radio_manager.is_connected or radio_manager.meshcore is None:
        logger.debug("Cannot sync contacts to radio: not connected")
        return {"loaded": 0, "error": "Radio not connected"}

    mc = radio_manager.meshcore
    _last_contact_sync = now

    try:
        # Get recent non-repeater contacts from database
        max_contacts = settings.max_radio_contacts
        contacts = await ContactRepository.get_recent_non_repeaters(limit=max_contacts)
        logger.debug("Found %d recent non-repeater contacts to sync", len(contacts))

        loaded = 0
        already_on_radio = 0
        failed = 0

        for contact in contacts:
            # Check if already on radio
            radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
            if radio_contact:
                already_on_radio += 1
                # Update DB if not marked as on_radio
                if not contact.on_radio:
                    await ContactRepository.set_on_radio(contact.public_key, True)
                continue

            try:
                result = await mc.commands.add_contact(contact.to_radio_dict())
                if result.type == EventType.OK:
                    loaded += 1
                    await ContactRepository.set_on_radio(contact.public_key, True)
                    logger.debug("Loaded contact %s to radio", contact.public_key[:12])
                else:
                    failed += 1
                    logger.warning(
                        "Failed to load contact %s: %s",
                        contact.public_key[:12], result.payload
                    )
            except Exception as e:
                failed += 1
                logger.warning("Error loading contact %s: %s", contact.public_key[:12], e)

        if loaded > 0 or failed > 0:
            logger.info(
                "Contact sync: loaded %d, already on radio %d, failed %d",
                loaded, already_on_radio, failed
            )

        return {
            "loaded": loaded,
            "already_on_radio": already_on_radio,
            "failed": failed,
        }

    except Exception as e:
        logger.error("Error syncing contacts to radio: %s", e)
        return {"loaded": 0, "error": str(e)}
