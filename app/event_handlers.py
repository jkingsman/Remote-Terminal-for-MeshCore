import logging
import time
from typing import TYPE_CHECKING

from meshcore import EventType

from app.models import CONTACT_TYPE_REPEATER, Contact, ContactUpsert
from app.packet_processor import process_raw_packet
from app.repository import (
    AmbiguousPublicKeyPrefixError,
    ContactRepository,
)
from app.services import dm_ack_tracker
from app.services.contact_reconciliation import (
    claim_prefix_messages_for_contact,
    promote_prefix_contacts_for_contact,
    record_contact_name_and_reconcile,
)
from app.services.messages import create_fallback_direct_message, increment_ack_and_broadcast
from app.websocket import broadcast_event

if TYPE_CHECKING:
    from meshcore.events import Event, Subscription

logger = logging.getLogger(__name__)

# Track active subscriptions so we can unsubscribe before re-registering
# This prevents handler duplication after reconnects
_active_subscriptions: list["Subscription"] = []
_pending_acks = dm_ack_tracker._pending_acks


def track_pending_ack(expected_ack: str, message_id: int, timeout_ms: int) -> None:
    """Compatibility wrapper for pending DM ACK tracking."""
    dm_ack_tracker.track_pending_ack(expected_ack, message_id, timeout_ms)


def cleanup_expired_acks() -> None:
    """Compatibility wrapper for expiring stale DM ACK entries."""
    dm_ack_tracker.cleanup_expired_acks()


async def on_contact_message(event: "Event") -> None:
    """Handle incoming direct messages from MeshCore library.

    NOTE: DMs are primarily handled by the packet processor via RX_LOG_DATA,
    which decrypts using our exported private key. This handler exists as a
    fallback for cases where:
    1. The private key couldn't be exported (firmware without ENABLE_PRIVATE_KEY_EXPORT)
    2. The packet processor couldn't match the sender to a known contact

    The packet processor handles: decryption, storage, broadcast, bot trigger.
    This handler only stores if the packet processor didn't already handle it
    (detected via INSERT OR IGNORE returning None for duplicates).
    """
    payload = event.payload

    # Skip CLI command responses (txt_type=1) - these are handled by the command endpoint
    txt_type = payload.get("txt_type", 0)
    if txt_type == 1:
        logger.debug("Skipping CLI response from %s (txt_type=1)", payload.get("pubkey_prefix"))
        return

    # Get full public key if available, otherwise use prefix
    sender_pubkey = payload.get("public_key") or payload.get("pubkey_prefix", "")
    received_at = int(time.time())

    # Look up contact from database - use prefix lookup only if needed
    # (get_by_key_or_prefix does exact match first, then prefix fallback)
    try:
        contact = await ContactRepository.get_by_key_or_prefix(sender_pubkey)
    except AmbiguousPublicKeyPrefixError:
        logger.warning(
            "DM sender prefix '%s' is ambiguous; storing under prefix until full key is known",
            sender_pubkey,
        )
        contact = None
    if contact:
        sender_pubkey = contact.public_key.lower()

        # Promote any prefix-stored messages to this full key
        await claim_prefix_messages_for_contact(public_key=sender_pubkey, log=logger)

        # Skip messages from repeaters - they only send CLI responses, not chat messages.
        # CLI responses are handled by the command endpoint and txt_type filter above.
        if contact.type == CONTACT_TYPE_REPEATER:
            logger.debug(
                "Skipping message from repeater %s (not stored in chat history)",
                sender_pubkey[:12],
            )
            return
    elif sender_pubkey:
        placeholder_upsert = ContactUpsert(
            public_key=sender_pubkey.lower(),
            type=0,
            last_seen=received_at,
            last_contacted=received_at,
            first_seen=received_at,
            on_radio=False,
            out_path_hash_mode=-1,
        )
        await ContactRepository.upsert(placeholder_upsert)
        contact = await ContactRepository.get_by_key(sender_pubkey.lower())
        if contact:
            broadcast_event("contact", contact.model_dump())

    # Try to create message - INSERT OR IGNORE handles duplicates atomically
    # If the packet processor already stored this message, this returns None
    ts = payload.get("sender_timestamp")
    sender_timestamp = ts if ts is not None else received_at
    sender_name = contact.name if contact else None
    path = payload.get("path")
    path_len = payload.get("path_len")
    message = await create_fallback_direct_message(
        conversation_key=sender_pubkey,
        text=payload.get("text", ""),
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        path=path,
        path_len=path_len,
        txt_type=txt_type,
        signature=payload.get("signature"),
        sender_name=sender_name,
        sender_key=sender_pubkey,
        broadcast_fn=broadcast_event,
    )

    if message is None:
        # Already handled by packet processor (or exact duplicate) - nothing more to do
        logger.debug("DM from %s already processed by packet processor", sender_pubkey[:12])
        return

    # If we get here, the packet processor didn't handle this message
    # (likely because private key export is not available)
    logger.debug("DM from %s handled by event handler (fallback path)", sender_pubkey[:12])

    # Update contact last_contacted (contact was already fetched above)
    if contact:
        await ContactRepository.update_last_contacted(sender_pubkey, received_at)


async def on_rx_log_data(event: "Event") -> None:
    """Store raw RF packet data and process via centralized packet processor.

    This is the unified entry point for all RF packets. The packet processor
    handles channel messages (GROUP_TEXT) and advertisements (ADVERT).
    """
    payload = event.payload
    logger.debug("Received RX log data packet")

    if "payload" not in payload:
        logger.warning("RX_LOG_DATA event missing 'payload' field")
        return

    raw_hex = payload["payload"]
    raw_bytes = bytes.fromhex(raw_hex)

    await process_raw_packet(
        raw_bytes=raw_bytes,
        snr=payload.get("snr"),
        rssi=payload.get("rssi"),
    )


async def on_path_update(event: "Event") -> None:
    """Handle path update events."""
    payload = event.payload
    public_key = str(payload.get("public_key", "")).lower()
    pubkey_prefix = str(payload.get("pubkey_prefix", "")).lower()

    contact: Contact | None = None
    if public_key:
        logger.debug("Path update for %s", public_key[:12])
        contact = await ContactRepository.get_by_key(public_key)
    elif pubkey_prefix:
        # Legacy compatibility: older payloads may only include a prefix.
        logger.debug("Path update for prefix %s", pubkey_prefix)
        contact = await ContactRepository.get_by_key_prefix(pubkey_prefix)
    else:
        logger.debug("PATH_UPDATE missing public_key/pubkey_prefix, skipping")
        return

    if not contact:
        return

    # PATH_UPDATE is a serial control push event from firmware (not an RF packet).
    # Current meshcore payloads only include public_key for this event.
    # RF route/path bytes are handled via RX_LOG_DATA -> process_raw_packet,
    # so if path fields are absent here we treat this as informational only.
    path = payload.get("path")
    path_len = payload.get("path_len")
    path_hash_mode = payload.get("path_hash_mode")
    if path is None or path_len is None:
        logger.debug(
            "PATH_UPDATE for %s has no path payload, skipping DB update", contact.public_key[:12]
        )
        return

    try:
        normalized_path_len = int(path_len)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid path_len in PATH_UPDATE for %s: %r", contact.public_key[:12], path_len
        )
        return

    normalized_path_hash_mode: int | None
    if path_hash_mode is None:
        # Legacy firmware/library payloads only support 1-byte hop hashes.
        normalized_path_hash_mode = -1 if normalized_path_len == -1 else 0
    else:
        normalized_path_hash_mode = None
        try:
            normalized_path_hash_mode = int(path_hash_mode)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid path_hash_mode in PATH_UPDATE for %s: %r",
                contact.public_key[:12],
                path_hash_mode,
            )
            normalized_path_hash_mode = None

    await ContactRepository.update_path(
        contact.public_key,
        str(path),
        normalized_path_len,
        normalized_path_hash_mode,
    )


async def on_new_contact(event: "Event") -> None:
    """Handle new contact from radio's internal contact database.

    This is different from RF advertisements - these are contacts synced
    from the radio's stored contact list.
    """
    payload = event.payload
    public_key = payload.get("public_key", "")

    if not public_key:
        logger.warning("Received new contact event with no public_key, skipping")
        return

    logger.debug("New contact: %s", public_key[:12])

    contact_upsert = ContactUpsert.from_radio_dict(public_key.lower(), payload, on_radio=False)
    contact_upsert.last_seen = int(time.time())
    await ContactRepository.upsert(contact_upsert)
    promoted_keys = await promote_prefix_contacts_for_contact(
        public_key=public_key,
        log=logger,
    )

    adv_name = payload.get("adv_name")
    await record_contact_name_and_reconcile(
        public_key=public_key,
        contact_name=adv_name,
        timestamp=int(time.time()),
        log=logger,
    )

    # Read back from DB so the broadcast includes all fields (last_contacted,
    # last_read_at, etc.) matching the REST Contact shape exactly.
    db_contact = await ContactRepository.get_by_key(public_key)
    broadcast_event(
        "contact",
        (
            db_contact.model_dump()
            if db_contact
            else Contact(**contact_upsert.model_dump(exclude_none=True)).model_dump()
        ),
    )
    if db_contact:
        for old_key in promoted_keys:
            broadcast_event(
                "contact_resolved",
                {
                    "previous_public_key": old_key,
                    "contact": db_contact.model_dump(),
                },
            )


async def on_ack(event: "Event") -> None:
    """Handle ACK events for direct messages."""
    payload = event.payload
    ack_code = payload.get("code", "")

    if not ack_code:
        logger.debug("Received ACK with no code")
        return

    logger.debug("Received ACK with code %s", ack_code)

    cleanup_expired_acks()

    message_id = dm_ack_tracker.pop_pending_ack(ack_code)
    if message_id is not None:
        logger.info("ACK received for message %d", message_id)
        # DM ACKs don't carry path data, so paths is intentionally omitted.
        # The frontend's mergePendingAck handles the missing field correctly,
        # preserving any previously known paths.
        await increment_ack_and_broadcast(message_id=message_id, broadcast_fn=broadcast_event)
    else:
        logger.debug("ACK code %s does not match any pending messages", ack_code)


def register_event_handlers(meshcore) -> None:
    """Register event handlers with the MeshCore instance.

    Note: CHANNEL_MSG_RECV and ADVERTISEMENT events are NOT subscribed.
    These are handled by the packet processor via RX_LOG_DATA to avoid
    duplicate processing and ensure consistent handling.

    This function is safe to call multiple times (e.g., after reconnect).
    Existing handlers are unsubscribed before new ones are registered.
    """
    global _active_subscriptions

    # Unsubscribe existing handlers to prevent duplication after reconnects.
    # Try/except handles the case where the old dispatcher is in a bad state
    # (e.g., after reconnect with a new MeshCore instance).
    for sub in _active_subscriptions:
        try:
            sub.unsubscribe()
        except Exception:
            pass  # Old dispatcher may be gone, that's fine
    _active_subscriptions.clear()

    # Register handlers and track subscriptions
    _active_subscriptions.append(meshcore.subscribe(EventType.CONTACT_MSG_RECV, on_contact_message))
    _active_subscriptions.append(meshcore.subscribe(EventType.RX_LOG_DATA, on_rx_log_data))
    _active_subscriptions.append(meshcore.subscribe(EventType.PATH_UPDATE, on_path_update))
    _active_subscriptions.append(meshcore.subscribe(EventType.NEW_CONTACT, on_new_contact))
    _active_subscriptions.append(meshcore.subscribe(EventType.ACK, on_ack))
    logger.info("Event handlers registered")
