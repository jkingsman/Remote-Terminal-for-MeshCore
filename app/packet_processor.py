"""
Centralized packet processing for MeshCore messages.

This module handles:
- Storing raw packets
- Decrypting channel messages (GroupText) with stored channel keys
- Decrypting direct messages with stored contact keys (if private key available)
- Creating message entries for successfully decrypted packets
- Broadcasting updates via WebSocket

This is the primary path for message processing when channel/contact keys
are offloaded from the radio to the server.
"""

import asyncio
import logging
import time
from itertools import count

from app.decoder import (
    DecryptedDirectMessage,
    PacketInfo,
    PayloadType,
    derive_public_key,
    parse_advertisement,
    parse_packet,
    try_decrypt_dm,
    try_decrypt_packet_with_channel_key,
)
from app.keystore import get_private_key, get_public_key, has_private_key
from app.models import (
    CONTACT_TYPE_REPEATER,
    CONTACT_TYPE_ROOM,
    Message,
    MessagePath,
    RawPacketBroadcast,
    RawPacketDecryptedInfo,
)
from app.notifications import enqueue_incoming_message_notification
from app.repository import (
    ChannelRepository,
    ContactAdvertPathRepository,
    ContactNameHistoryRepository,
    ContactRepository,
    MessageRepository,
    RawPacketRepository,
)
from app.websocket import broadcast_error, broadcast_event

logger = logging.getLogger(__name__)

_raw_observation_counter = count(1)


async def _handle_duplicate_message(
    packet_id: int,
    msg_type: str,
    conversation_key: str,
    text: str,
    sender_timestamp: int,
    path: str | None,
    received: int,
) -> None:
    """Handle a duplicate message by updating paths/acks on the existing record.

    Called when MessageRepository.create returns None (INSERT OR IGNORE hit a duplicate).
    Looks up the existing message, adds the new path, increments ack count for outgoing
    messages, and broadcasts the update to clients.
    """
    existing_msg = await MessageRepository.get_by_content(
        msg_type=msg_type,
        conversation_key=conversation_key,
        text=text,
        sender_timestamp=sender_timestamp,
    )
    if not existing_msg:
        label = "message" if msg_type == "CHAN" else "DM"
        logger.warning(
            "Duplicate %s for %s but couldn't find existing",
            label,
            conversation_key[:12],
        )
        return

    logger.debug(
        "Duplicate %s for %s (msg_id=%d, outgoing=%s) - adding path",
        msg_type,
        conversation_key[:12],
        existing_msg.id,
        existing_msg.outgoing,
    )

    # Add path if provided
    if path is not None:
        paths = await MessageRepository.add_path(existing_msg.id, path, received)
    else:
        # Get current paths for broadcast
        paths = existing_msg.paths or []

    # Increment ack count for outgoing messages (echo confirmation)
    if existing_msg.outgoing:
        ack_count = await MessageRepository.increment_ack_count(existing_msg.id)
    else:
        ack_count = existing_msg.acked

    # Only broadcast when something actually changed:
    # - outgoing: ack count was incremented
    # - path provided: a new path entry was appended
    # The path=None case happens for direct-delivery DMs (0-hop, no routing bytes).
    # A non-outgoing duplicate with no new path changes nothing in the DB, so skip.
    if existing_msg.outgoing or path is not None:
        broadcast_event(
            "message_acked",
            {
                "message_id": existing_msg.id,
                "ack_count": ack_count,
                "paths": [p.model_dump() for p in paths] if paths else [],
            },
        )

    # Mark this packet as decrypted
    await RawPacketRepository.mark_decrypted(packet_id, existing_msg.id)


async def create_message_from_decrypted(
    packet_id: int,
    channel_key: str,
    sender: str | None,
    message_text: str,
    timestamp: int,
    received_at: int | None = None,
    path: str | None = None,
    channel_name: str | None = None,
    trigger_bot: bool = True,
) -> int | None:
    """Create a message record from decrypted channel packet content.

    This is the shared logic for storing decrypted channel messages,
    used by both real-time packet processing and historical decryption.

    Args:
        packet_id: ID of the raw packet being processed
        channel_key: Hex string channel key
        channel_name: Channel name (e.g. "#general"), for bot context
        sender: Sender name (will be prefixed to message) or None
        message_text: The decrypted message content
        timestamp: Sender timestamp from the packet
        received_at: When the packet was received (defaults to now)
        path: Hex-encoded routing path
        trigger_bot: Whether to trigger bot response (False for historical decryption)

    Returns the message ID if created, None if duplicate.
    """
    received = received_at or int(time.time())

    # Format the message text with sender prefix if present
    text = f"{sender}: {message_text}" if sender else message_text

    # Normalize channel key to uppercase for consistency
    channel_key_normalized = channel_key.upper()

    # Resolve sender_key: look up contact by exact name match
    resolved_sender_key: str | None = None
    resolved_sender_type: int | None = None
    if sender:
        candidates = await ContactRepository.get_by_name(sender)
        if len(candidates) == 1:
            resolved_sender_key = candidates[0].public_key
            resolved_sender_type = candidates[0].type

    # Try to create message - INSERT OR IGNORE handles duplicates atomically
    msg_id = await MessageRepository.create(
        msg_type="CHAN",
        text=text,
        conversation_key=channel_key_normalized,
        sender_timestamp=timestamp,
        received_at=received,
        path=path,
        sender_name=sender,
        sender_key=resolved_sender_key,
    )

    if msg_id is None:
        # Duplicate message detected - this happens when:
        # 1. Our own outgoing message echoes back (flood routing)
        # 2. Same message arrives via multiple paths before first is committed
        # In either case, add the path to the existing message.
        await _handle_duplicate_message(
            packet_id, "CHAN", channel_key_normalized, text, timestamp, path, received
        )
        return None

    logger.info("Stored channel message %d for channel %s", msg_id, channel_key_normalized[:8])

    # Mark the raw packet as decrypted
    await RawPacketRepository.mark_decrypted(packet_id, msg_id)

    # Build paths array for broadcast
    # Use "is not None" to include empty string (direct/0-hop messages)
    paths = [MessagePath(path=path or "", received_at=received)] if path is not None else None

    # Broadcast new message to connected clients
    broadcast_event(
        "message",
        Message(
            id=msg_id,
            type="CHAN",
            conversation_key=channel_key_normalized,
            text=text,
            sender_timestamp=timestamp,
            received_at=received,
            paths=paths,
            sender_name=sender,
            sender_key=resolved_sender_key,
        ).model_dump(),
    )

    await enqueue_incoming_message_notification(
        msg_type="CHAN",
        conversation_id=channel_key_normalized,
        text=message_text,
        conversation_name=channel_name,
        sender_name=sender,
        contact_type=resolved_sender_type,
        path=path,
    )

    # Run bot if enabled (for incoming channel messages, not historical decryption)
    if trigger_bot:
        from app.bot import run_bot_for_message

        asyncio.create_task(
            run_bot_for_message(
                sender_name=sender,
                sender_key=None,  # Channel messages don't have a sender public key
                message_text=message_text,
                is_dm=False,
                channel_key=channel_key_normalized,
                channel_name=channel_name,
                sender_timestamp=timestamp,
                path=path,
                is_outgoing=False,
            )
        )

    return msg_id


async def create_dm_message_from_decrypted(
    packet_id: int,
    decrypted: DecryptedDirectMessage,
    their_public_key: str,
    our_public_key: str | None,
    received_at: int | None = None,
    path: str | None = None,
    outgoing: bool = False,
    trigger_bot: bool = True,
) -> int | None:
    """Create a message record from decrypted direct message packet content.

    This is the shared logic for storing decrypted direct messages,
    used by both real-time packet processing and historical decryption.

    Args:
        packet_id: ID of the raw packet being processed
        decrypted: DecryptedDirectMessage from decoder
        their_public_key: The contact's full 64-char public key (conversation_key)
        our_public_key: Our public key (to determine direction), or None
        received_at: When the packet was received (defaults to now)
        path: Hex-encoded routing path
        outgoing: Whether this is an outgoing message (we sent it)
        trigger_bot: Whether to trigger bot response (False for historical decryption)

    Returns the message ID if created, None if duplicate.
    """
    # Check if sender is a repeater - repeaters only send CLI responses, not chat messages.
    # CLI responses are handled by the command endpoint, not stored in chat history.
    contact = await ContactRepository.get_by_key(their_public_key)
    if contact and contact.type in (CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM):
        logger.debug(
            "Skipping message from non-chat contact type %s (%s): %s",
            contact.type,
            their_public_key[:12],
            (decrypted.message or "")[:50],
        )
        return None

    received = received_at or int(time.time())

    # conversation_key is always the other party's public key
    conversation_key = their_public_key.lower()

    # Try to create message - INSERT OR IGNORE handles duplicates atomically
    msg_id = await MessageRepository.create(
        msg_type="PRIV",
        text=decrypted.message,
        conversation_key=conversation_key,
        sender_timestamp=decrypted.timestamp,
        received_at=received,
        path=path,
        outgoing=outgoing,
        sender_key=conversation_key if not outgoing else None,
    )

    if msg_id is None:
        # Duplicate message detected
        await _handle_duplicate_message(
            packet_id,
            "PRIV",
            conversation_key,
            decrypted.message,
            decrypted.timestamp,
            path,
            received,
        )
        return None

    logger.info(
        "Stored direct message %d for contact %s (outgoing=%s)",
        msg_id,
        conversation_key[:12],
        outgoing,
    )

    # Mark the raw packet as decrypted
    await RawPacketRepository.mark_decrypted(packet_id, msg_id)

    # Build paths array for broadcast
    paths = [MessagePath(path=path or "", received_at=received)] if path is not None else None

    # Broadcast new message to connected clients
    broadcast_event(
        "message",
        Message(
            id=msg_id,
            type="PRIV",
            conversation_key=conversation_key,
            text=decrypted.message,
            sender_timestamp=decrypted.timestamp,
            received_at=received,
            paths=paths,
            outgoing=outgoing,
        ).model_dump(),
    )

    if not outgoing:
        await enqueue_incoming_message_notification(
            msg_type="PRIV",
            conversation_id=conversation_key,
            text=decrypted.message,
            conversation_name=contact.name if contact else None,
            sender_name=contact.name if contact else None,
            contact_type=contact.type if contact else None,
            path=path,
        )

    # Update contact's last_contacted timestamp (for sorting)
    await ContactRepository.update_last_contacted(conversation_key, received)

    # Run bot if enabled (for all real-time DMs, including our own outgoing messages)
    if trigger_bot:
        from app.bot import run_bot_for_message

        # Get contact name for the bot
        contact = await ContactRepository.get_by_key(their_public_key)
        sender_name = contact.name if contact else None

        asyncio.create_task(
            run_bot_for_message(
                sender_name=sender_name,
                sender_key=their_public_key,
                message_text=decrypted.message,
                is_dm=True,
                channel_key=None,
                channel_name=None,
                sender_timestamp=decrypted.timestamp,
                path=path,
                is_outgoing=outgoing,
            )
        )

    return msg_id


async def run_historical_dm_decryption(
    private_key_bytes: bytes,
    contact_public_key_bytes: bytes,
    contact_public_key_hex: str,
    display_name: str | None = None,
) -> None:
    """Background task to decrypt historical DM packets with contact's key."""
    from app.websocket import broadcast_success

    packets = await RawPacketRepository.get_undecrypted_text_messages()
    total = len(packets)
    decrypted_count = 0

    if total == 0:
        logger.info("No undecrypted TEXT_MESSAGE packets to process")
        return

    logger.info("Starting historical DM decryption of %d TEXT_MESSAGE packets", total)

    # Derive our public key from the private key
    our_public_key_bytes = derive_public_key(private_key_bytes)

    for packet_id, packet_data, packet_timestamp in packets:
        # Note: passing our_public_key=None disables the outbound hash check in
        # try_decrypt_dm (only the inbound check src_hash == their_first_byte runs).
        # For the 255/256 case where our first byte differs from the contact's,
        # outgoing packets fail the inbound check and are skipped — which is correct
        # since outgoing DMs are stored directly by the send endpoint.
        # For the 1/256 case where bytes match, an outgoing packet may decrypt
        # successfully, but the dual-hash direction check below correctly identifies
        # it and the DB dedup constraint prevents a duplicate insert.
        result = try_decrypt_dm(
            packet_data,
            private_key_bytes,
            contact_public_key_bytes,
            our_public_key=None,
        )

        if result is not None:
            # Determine direction using both hashes (mirrors _process_direct_message
            # logic at lines 806-818) to handle the 1/256 case where our first
            # public key byte matches the contact's.
            src_hash = result.src_hash.lower()
            dest_hash = result.dest_hash.lower()
            our_first_byte = format(our_public_key_bytes[0], "02x").lower()

            if src_hash == our_first_byte and dest_hash != our_first_byte:
                outgoing = True
            else:
                # Incoming, ambiguous (both match), or neither matches.
                # Default to incoming — outgoing DMs are stored by the send
                # endpoint, so historical decryption only recovers incoming.
                outgoing = False

            # Extract path from the raw packet for storage
            packet_info = parse_packet(packet_data)
            path_hex = packet_info.path.hex() if packet_info else None

            msg_id = await create_dm_message_from_decrypted(
                packet_id=packet_id,
                decrypted=result,
                their_public_key=contact_public_key_hex,
                our_public_key=our_public_key_bytes.hex(),
                received_at=packet_timestamp,
                path=path_hex,
                outgoing=outgoing,
                trigger_bot=False,  # Historical decryption should not trigger bot
            )

            if msg_id is not None:
                decrypted_count += 1

    logger.info(
        "Historical DM decryption complete: %d/%d packets decrypted",
        decrypted_count,
        total,
    )

    # Notify frontend
    if decrypted_count > 0:
        name = display_name or contact_public_key_hex[:12]
        broadcast_success(
            f"Historical decrypt complete for {name}",
            f"Decrypted {decrypted_count} message{'s' if decrypted_count != 1 else ''}",
        )


async def start_historical_dm_decryption(
    background_tasks,
    contact_public_key_hex: str,
    display_name: str | None = None,
) -> None:
    """Start historical DM decryption using the stored private key."""
    if not has_private_key():
        logger.warning(
            "Cannot start historical DM decryption: private key not available. "
            "Ensure radio firmware has ENABLE_PRIVATE_KEY_EXPORT=1."
        )
        broadcast_error(
            "Cannot decrypt historical DMs",
            "Private key not available. Radio firmware may need ENABLE_PRIVATE_KEY_EXPORT=1.",
        )
        return

    private_key_bytes = get_private_key()
    if private_key_bytes is None:
        return

    try:
        contact_public_key_bytes = bytes.fromhex(contact_public_key_hex)
    except ValueError:
        logger.warning(
            "Cannot start historical DM decryption: invalid contact key %s",
            contact_public_key_hex,
        )
        return

    logger.info("Starting historical DM decryption for contact %s", contact_public_key_hex[:12])
    if background_tasks is None:
        asyncio.create_task(
            run_historical_dm_decryption(
                private_key_bytes,
                contact_public_key_bytes,
                contact_public_key_hex.lower(),
                display_name,
            )
        )
    else:
        background_tasks.add_task(
            run_historical_dm_decryption,
            private_key_bytes,
            contact_public_key_bytes,
            contact_public_key_hex.lower(),
            display_name,
        )


async def process_raw_packet(
    raw_bytes: bytes,
    timestamp: int | None = None,
    snr: float | None = None,
    rssi: int | None = None,
) -> dict:
    """
    Process an incoming raw packet.

    This is the main entry point for all incoming RF packets.

    Note: Packets are deduplicated by payload hash in the database. If we receive
    a duplicate packet (same payload, different path), we still broadcast it to
    the frontend (for the real-time packet feed) but skip decryption processing
    since the original packet was already processed.
    """
    ts = timestamp or int(time.time())
    observation_id = next(_raw_observation_counter)

    packet_id, is_new_packet = await RawPacketRepository.create(raw_bytes, ts)
    raw_hex = raw_bytes.hex()

    # Parse packet to get type
    packet_info = parse_packet(raw_bytes)
    payload_type = packet_info.payload_type if packet_info else None
    payload_type_name = payload_type.name if payload_type else "Unknown"

    if packet_info is None and len(raw_bytes) > 2:
        logger.warning(
            "Failed to parse %d-byte packet (id=%d); stored undecrypted",
            len(raw_bytes),
            packet_id,
        )

    # Log packet arrival at debug level
    path_hex = packet_info.path.hex() if packet_info and packet_info.path else ""
    logger.debug(
        "Packet received: type=%s, is_new=%s, packet_id=%d, path='%s'",
        payload_type_name,
        is_new_packet,
        packet_id,
        path_hex[:8] if path_hex else "(direct)",
    )

    result = {
        "packet_id": packet_id,
        "timestamp": ts,
        "raw_hex": raw_hex,
        "payload_type": payload_type_name,
        "snr": snr,
        "rssi": rssi,
        "decrypted": False,
        "message_id": None,
        "channel_name": None,
        "sender": None,
    }

    # Process packets based on payload type
    # For GROUP_TEXT, we always try to decrypt even for duplicate packets - the message
    # deduplication in create_message_from_decrypted handles adding paths to existing messages.
    # This is more reliable than trying to look up the message via raw packet linking.
    if payload_type == PayloadType.GROUP_TEXT:
        decrypt_result = await _process_group_text(raw_bytes, packet_id, ts, packet_info)
        if decrypt_result:
            result.update(decrypt_result)

    elif payload_type == PayloadType.ADVERT:
        # Process all advert arrivals (even payload-hash duplicates) so the
        # path-freshness logic in _process_advertisement can pick the shortest
        # path heard within the freshness window.
        await _process_advertisement(raw_bytes, ts, packet_info)

    elif payload_type == PayloadType.TEXT_MESSAGE:
        # Try to decrypt direct messages using stored private key and known contacts
        decrypt_result = await _process_direct_message(raw_bytes, packet_id, ts, packet_info)
        if decrypt_result:
            result.update(decrypt_result)

    # Always broadcast raw packet for the packet feed UI (even duplicates)
    # This enables the frontend cracker to see all incoming packets in real-time
    broadcast_payload = RawPacketBroadcast(
        id=packet_id,
        observation_id=observation_id,
        timestamp=ts,
        data=raw_hex,
        payload_type=payload_type_name,
        snr=snr,
        rssi=rssi,
        decrypted=result["decrypted"],
        decrypted_info=RawPacketDecryptedInfo(
            channel_name=result["channel_name"],
            sender=result["sender"],
            channel_key=result.get("channel_key"),
            contact_key=result.get("contact_key"),
        )
        if result["decrypted"]
        else None,
    )
    broadcast_event("raw_packet", broadcast_payload.model_dump())

    return result


async def _process_group_text(
    raw_bytes: bytes,
    packet_id: int,
    timestamp: int,
    packet_info: PacketInfo | None,
) -> dict | None:
    """
    Process a GroupText (channel message) packet.

    Tries all known channel keys to decrypt.
    Creates a message entry if successful (or adds path to existing if duplicate).
    """
    # Try to decrypt with all known channel keys
    channels = await ChannelRepository.get_all()

    for channel in channels:
        # Convert hex key to bytes for decryption
        try:
            channel_key_bytes = bytes.fromhex(channel.key)
        except ValueError:
            continue

        decrypted = try_decrypt_packet_with_channel_key(raw_bytes, channel_key_bytes)
        if not decrypted:
            continue

        # Successfully decrypted!
        logger.debug("Decrypted GroupText for channel %s: %s", channel.name, decrypted.message[:50])

        # Create message (or add path to existing if duplicate)
        # This handles both new messages and echoes of our own outgoing messages
        msg_id = await create_message_from_decrypted(
            packet_id=packet_id,
            channel_key=channel.key,
            channel_name=channel.name,
            sender=decrypted.sender,
            message_text=decrypted.message,
            timestamp=decrypted.timestamp,
            received_at=timestamp,
            path=packet_info.path.hex() if packet_info else None,
        )

        return {
            "decrypted": True,
            "channel_name": channel.name,
            "sender": decrypted.sender,
            "message_id": msg_id,  # None if duplicate, msg_id if new
            "channel_key": channel.key,
        }

    # Couldn't decrypt with any known key
    return None


async def _process_advertisement(
    raw_bytes: bytes,
    timestamp: int,
    packet_info: PacketInfo | None = None,
) -> None:
    """
    Process an advertisement packet.

    Extracts contact info and updates the database/broadcasts to clients.
    For non-repeater contacts, triggers sync of recent contacts to radio for DM ACK support.
    """
    # Parse packet to get path info if not already provided
    if packet_info is None:
        packet_info = parse_packet(raw_bytes)
    if packet_info is None:
        logger.debug("Failed to parse advertisement packet")
        return

    advert = parse_advertisement(packet_info.payload)
    if not advert:
        logger.debug("Failed to parse advertisement payload")
        return

    # Extract path info from packet
    new_path_len = packet_info.path_length
    new_path_hex = packet_info.path.hex() if packet_info.path else ""

    # Try to find existing contact
    existing = await ContactRepository.get_by_key(advert.public_key.lower())

    # Determine which path to use: keep shorter path if heard recently (within 60s)
    # This handles advertisement echoes through different routes
    PATH_FRESHNESS_SECONDS = 60
    use_existing_path = False

    if existing and existing.last_seen:
        path_age = timestamp - existing.last_seen
        existing_path_len = existing.last_path_len if existing.last_path_len >= 0 else float("inf")

        # Keep existing path if it's fresh and shorter (or equal)
        if path_age <= PATH_FRESHNESS_SECONDS and existing_path_len <= new_path_len:
            use_existing_path = True
            logger.debug(
                "Keeping existing shorter path for %s (existing=%d, new=%d, age=%ds)",
                advert.public_key[:12],
                existing_path_len,
                new_path_len,
                path_age,
            )

    if use_existing_path:
        assert existing is not None  # Guaranteed by the conditions that set use_existing_path
        path_len = existing.last_path_len if existing.last_path_len is not None else -1
        path_hex = existing.last_path or ""
    else:
        path_len = new_path_len
        path_hex = new_path_hex

    logger.debug(
        "Parsed advertisement from %s: %s (role=%d, lat=%s, lon=%s, path_len=%d)",
        advert.public_key[:12],
        advert.name,
        advert.device_role,
        advert.lat,
        advert.lon,
        path_len,
    )

    # Use device_role from advertisement for contact type (1=Chat, 2=Repeater, 3=Room, 4=Sensor)
    # Use advert.timestamp for last_advert (sender's timestamp), receive timestamp for last_seen
    contact_type = (
        advert.device_role if advert.device_role > 0 else (existing.type if existing else 0)
    )

    # Keep recent unique advert paths for all contacts.
    await ContactAdvertPathRepository.record_observation(
        public_key=advert.public_key.lower(),
        path_hex=new_path_hex,
        timestamp=timestamp,
        max_paths=10,
    )

    # Record name history
    if advert.name:
        await ContactNameHistoryRepository.record_name(
            public_key=advert.public_key.lower(),
            name=advert.name,
            timestamp=timestamp,
        )

    contact_data = {
        "public_key": advert.public_key.lower(),
        "name": advert.name,
        "type": contact_type,
        "lat": advert.lat,
        "lon": advert.lon,
        "last_advert": advert.timestamp if advert.timestamp > 0 else timestamp,
        "last_seen": timestamp,
        "last_path": path_hex,
        "last_path_len": path_len,
        "first_seen": timestamp,  # COALESCE in upsert preserves existing value
    }

    await ContactRepository.upsert(contact_data)
    claimed = await MessageRepository.claim_prefix_messages(advert.public_key.lower())
    if claimed > 0:
        logger.info(
            "Claimed %d prefix DM message(s) for contact %s",
            claimed,
            advert.public_key[:12],
        )

    # Read back from DB so the broadcast includes all fields (last_contacted,
    # last_read_at, flags, on_radio, etc.) matching the REST Contact shape exactly.
    db_contact = await ContactRepository.get_by_key(advert.public_key.lower())
    if db_contact:
        broadcast_event("contact", db_contact.model_dump())
    else:
        broadcast_event("contact", contact_data)

    # For new contacts, optionally attempt to decrypt any historical DMs we may have stored
    # This is controlled by the auto_decrypt_dm_on_advert setting
    if existing is None:
        from app.repository import AppSettingsRepository

        settings = await AppSettingsRepository.get()
        if settings.auto_decrypt_dm_on_advert:
            await start_historical_dm_decryption(None, advert.public_key.lower(), advert.name)

    # If this is not a repeater, trigger recent contacts sync to radio
    # This ensures we can auto-ACK DMs from recent contacts
    if contact_type != CONTACT_TYPE_REPEATER:
        # Import here to avoid circular import
        from app.radio_sync import sync_recent_contacts_to_radio

        asyncio.create_task(sync_recent_contacts_to_radio())


async def _process_direct_message(
    raw_bytes: bytes,
    packet_id: int,
    timestamp: int,
    packet_info: PacketInfo | None,
) -> dict | None:
    """
    Process a TEXT_MESSAGE (direct message) packet.

    Uses the stored private key and tries to decrypt with known contacts.
    The src_hash (first byte of sender's public key) is used to narrow down
    candidate contacts for decryption.
    """
    if not has_private_key():
        # No private key available - can't decrypt DMs
        return None

    private_key = get_private_key()
    our_public_key = get_public_key()
    if private_key is None or our_public_key is None:
        return None

    # Parse packet to get the payload for src_hash extraction
    if packet_info is None:
        packet_info = parse_packet(raw_bytes)
    if packet_info is None or packet_info.payload is None:
        return None

    # Extract src_hash from payload (second byte: [dest_hash:1][src_hash:1][MAC:2][ciphertext])
    if len(packet_info.payload) < 4:
        return None

    dest_hash = format(packet_info.payload[0], "02x").lower()
    src_hash = format(packet_info.payload[1], "02x").lower()

    # Check if this message involves us (either as sender or recipient)
    our_first_byte = format(our_public_key[0], "02x").lower()

    # Determine direction based on which hash matches us:
    # - dest_hash == us AND src_hash != us -> incoming (addressed to us from someone else)
    # - src_hash == us AND dest_hash != us -> outgoing (we sent to someone else)
    # - Both match us -> ambiguous (our first byte matches contact's), default to incoming
    # - Neither matches us -> not our message
    if dest_hash == our_first_byte and src_hash != our_first_byte:
        is_outgoing = False  # Definitely incoming
    elif src_hash == our_first_byte and dest_hash != our_first_byte:
        is_outgoing = True  # Definitely outgoing
    elif dest_hash == our_first_byte and src_hash == our_first_byte:
        # Ambiguous: our first byte matches contact's first byte (1/256 chance)
        # Default to incoming since dest_hash matching us is more indicative
        is_outgoing = False
        logger.debug("Ambiguous DM direction (first bytes match), defaulting to incoming")
    else:
        # Neither hash matches us - not our message
        return None

    # Find candidate contacts based on the relevant hash
    # For incoming: match src_hash (sender's first byte)
    # For outgoing: match dest_hash (recipient's first byte)
    match_hash = dest_hash if is_outgoing else src_hash

    # Get contacts matching the first byte of public key via targeted SQL query
    candidate_contacts = await ContactRepository.get_by_pubkey_first_byte(match_hash)

    if not candidate_contacts:
        logger.debug(
            "No contacts found matching hash %s for DM decryption",
            match_hash,
        )
        return None

    # Try decrypting with each candidate contact
    for contact in candidate_contacts:
        try:
            contact_public_key = bytes.fromhex(contact.public_key)
        except ValueError:
            continue

        # For incoming messages, pass our_public_key to enable the dest_hash filter
        # For outgoing messages, skip the filter (dest_hash is the recipient, not us)
        result = try_decrypt_dm(
            raw_bytes,
            private_key,
            contact_public_key,
            our_public_key=our_public_key if not is_outgoing else None,
        )

        if result is not None:
            # Successfully decrypted!
            logger.debug(
                "Decrypted DM %s contact %s: %s",
                "to" if is_outgoing else "from",
                contact.name or contact.public_key[:12],
                result.message[:50] if result.message else "",
            )

            # Create message (or add path to existing if duplicate)
            msg_id = await create_dm_message_from_decrypted(
                packet_id=packet_id,
                decrypted=result,
                their_public_key=contact.public_key,
                our_public_key=our_public_key.hex(),
                received_at=timestamp,
                path=packet_info.path.hex() if packet_info.path else None,
                outgoing=is_outgoing,
            )

            return {
                "decrypted": True,
                "contact_name": contact.name,
                "sender": contact.name or contact.public_key[:12],
                "message_id": msg_id,
                "contact_key": contact.public_key,
            }

    # Couldn't decrypt with any known contact
    logger.debug("Could not decrypt DM with any of %d candidate contacts", len(candidate_contacts))
    return None
