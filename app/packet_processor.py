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

from app.decoder import (
    PayloadType,
    parse_packet,
    try_decrypt_packet_with_channel_key,
    try_parse_advertisement,
)
from app.models import CONTACT_TYPE_REPEATER, RawPacketBroadcast, RawPacketDecryptedInfo
from app.repository import ChannelRepository, ContactRepository, MessageRepository, RawPacketRepository
from app.websocket import broadcast_event

logger = logging.getLogger(__name__)


# Pending repeats for outgoing message ACK detection
# Key: (channel_key, text_hash, timestamp) -> message_id
_pending_repeats: dict[tuple[str, str, int], int] = {}
_pending_repeat_expiry: dict[tuple[str, str, int], float] = {}
REPEAT_EXPIRY_SECONDS = 30


async def create_message_from_decrypted(
    packet_id: int,
    channel_key: str,
    sender: str | None,
    message_text: str,
    timestamp: int,
    received_at: int | None = None,
) -> int | None:
    """Create a message record from decrypted channel packet content.

    This is the shared logic for storing decrypted channel messages,
    used by both real-time packet processing and historical decryption.

    Returns the message ID if created, None if duplicate.
    """
    import time as time_module
    received = received_at or int(time_module.time())

    # Format the message text
    text = f"{sender}: {message_text}" if sender else message_text

    # Try to create message - INSERT OR IGNORE handles duplicates atomically
    msg_id = await MessageRepository.create(
        msg_type="CHAN",
        text=text,
        conversation_key=channel_key.upper(),
        sender_timestamp=timestamp,
        received_at=received,
    )

    if msg_id is None:
        # Duplicate detected - find existing message ID for packet linkage
        existing_id = await MessageRepository.find_duplicate(
            conversation_key=channel_key.upper(),
            text=text,
            sender_timestamp=timestamp,
        )
        if existing_id:
            await RawPacketRepository.mark_decrypted(packet_id, existing_id)
        return None

    # Mark the raw packet as decrypted
    await RawPacketRepository.mark_decrypted(packet_id, msg_id)

    # Broadcast new message to connected clients (for historical decryption visibility)
    broadcast_event("message", {
        "id": msg_id,
        "type": "CHAN",
        "conversation_key": channel_key.upper(),
        "text": text,
        "sender_timestamp": timestamp,
        "received_at": received,
        "path_len": None,
        "txt_type": 0,
        "signature": None,
        "outgoing": False,
        "acked": 0,
    })

    return msg_id


def track_pending_repeat(channel_key: str, text: str, timestamp: int, message_id: int) -> None:
    """Track an outgoing channel message for repeat detection."""
    text_hash = str(hash(text))
    key = (channel_key.upper(), text_hash, timestamp)
    _pending_repeats[key] = message_id
    _pending_repeat_expiry[key] = time.time() + REPEAT_EXPIRY_SECONDS
    logger.debug("Tracking repeat for channel %s, message %d", channel_key[:8], message_id)


def _cleanup_expired_repeats() -> None:
    """Remove expired pending repeats."""
    now = time.time()
    expired = [k for k, exp in _pending_repeat_expiry.items() if exp < now]
    for k in expired:
        _pending_repeats.pop(k, None)
        _pending_repeat_expiry.pop(k, None)


async def process_raw_packet(
    raw_bytes: bytes,
    timestamp: int | None = None,
    snr: float | None = None,
    rssi: int | None = None,
) -> dict:
    """
    Process an incoming raw packet.

    This is the main entry point for all incoming RF packets.
    """
    ts = timestamp or int(time.time())

    packet_id = await RawPacketRepository.create(raw_bytes, ts)

    # If packet_id is None, this is a duplicate packet (same data already exists)
    # Skip processing since we've already handled this exact packet
    if packet_id is None:
        logger.debug("Duplicate raw packet detected, skipping")
        return {
            "packet_id": None,
            "timestamp": ts,
            "raw_hex": raw_bytes.hex(),
            "payload_type": "Duplicate",
            "snr": snr,
            "rssi": rssi,
            "decrypted": False,
            "message_id": None,
            "channel_name": None,
            "sender": None,
        }

    raw_hex = raw_bytes.hex()

    # Parse packet to get type
    packet_info = parse_packet(raw_bytes)
    payload_type = packet_info.payload_type if packet_info else None
    payload_type_name = payload_type.name if payload_type else "Unknown"

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

    # Try to decrypt/parse based on payload type
    if payload_type == PayloadType.GROUP_TEXT:
        decrypt_result = await _process_group_text(raw_bytes, packet_id, ts, packet_info)
        if decrypt_result:
            result.update(decrypt_result)

    elif payload_type == PayloadType.ADVERT:
        await _process_advertisement(raw_bytes, ts)

    # TODO: Add TEXT_MESSAGE (direct message) decryption when private key is available
    # elif payload_type == PayloadType.TEXT_MESSAGE:
    #     decrypt_result = await _process_direct_message(raw_bytes, packet_id, ts, packet_info)
    #     if decrypt_result:
    #         result.update(decrypt_result)

    # Broadcast raw packet for the packet feed UI
    broadcast_payload = RawPacketBroadcast(
        id=packet_id,
        timestamp=ts,
        data=raw_hex,
        payload_type=payload_type_name,
        snr=snr,
        rssi=rssi,
        decrypted=result["decrypted"],
        decrypted_info=RawPacketDecryptedInfo(
            channel_name=result["channel_name"],
            sender=result["sender"],
        ) if result["decrypted"] else None,
    )
    broadcast_event("raw_packet", broadcast_payload.model_dump())

    return result


async def _process_group_text(
    raw_bytes: bytes,
    packet_id: int,
    timestamp: int,
    packet_info,
) -> dict | None:
    """
    Process a GroupText (channel message) packet.

    Tries all known channel keys to decrypt.
    Creates a message entry if successful.
    Handles repeat detection for outgoing message ACKs.
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
        logger.debug(
            "Decrypted GroupText for channel %s: %s",
            channel.name, decrypted.message[:50]
        )

        # Check for repeat detection (our own message echoed back)
        is_repeat = False
        _cleanup_expired_repeats()
        text_hash = str(hash(decrypted.message))

        for ts_offset in range(-5, 6):
            key = (channel.key, text_hash, decrypted.timestamp + ts_offset)
            if key in _pending_repeats:
                message_id = _pending_repeats[key]
                # Don't pop - let it expire naturally so subsequent repeats via
                # different radio paths are also caught as duplicates
                logger.info("Repeat detected for channel message %d", message_id)
                ack_count = await MessageRepository.increment_ack_count(message_id)
                broadcast_event("message_acked", {"message_id": message_id, "ack_count": ack_count})
                is_repeat = True
                break

        if is_repeat:
            # Mark packet as decrypted but don't create new message
            await RawPacketRepository.mark_decrypted(packet_id, message_id)
            return {
                "decrypted": True,
                "channel_name": channel.name,
                "sender": decrypted.sender,
                "message_id": message_id,
            }

        # Format the message text
        if decrypted.sender:
            text = f"{decrypted.sender}: {decrypted.message}"
        else:
            text = decrypted.message

        # Try to create message - INSERT OR IGNORE handles duplicates atomically
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text=text,
            conversation_key=channel.key,
            sender_timestamp=decrypted.timestamp,
            received_at=timestamp,
        )

        if msg_id is None:
            # Duplicate detected by database constraint (same message via different RF path)
            # Find existing message ID for packet linkage
            existing_id = await MessageRepository.find_duplicate(
                conversation_key=channel.key,
                text=text,
                sender_timestamp=decrypted.timestamp,
            )
            logger.debug(
                "Duplicate message detected for channel %s (existing id=%s)",
                channel.name, existing_id
            )
            if existing_id:
                await RawPacketRepository.mark_decrypted(packet_id, existing_id)
            return {
                "decrypted": True,
                "channel_name": channel.name,
                "sender": decrypted.sender,
                "message_id": existing_id,
            }

        logger.info("Stored channel message %d for %s", msg_id, channel.name)

        # Broadcast new message (only for genuinely new messages)
        broadcast_event("message", {
            "id": msg_id,
            "type": "CHAN",
            "conversation_key": channel.key,
            "text": text,
            "sender_timestamp": decrypted.timestamp,
            "received_at": timestamp,
            "path_len": packet_info.path_length if packet_info else None,
            "txt_type": 0,
            "signature": None,
            "outgoing": False,
            "acked": 0,
        })

        # Mark the raw packet as decrypted
        await RawPacketRepository.mark_decrypted(packet_id, msg_id)

        return {
            "decrypted": True,
            "channel_name": channel.name,
            "sender": decrypted.sender,
            "message_id": msg_id,
        }

    # Couldn't decrypt with any known key
    return None


async def _process_advertisement(
    raw_bytes: bytes,
    timestamp: int,
) -> None:
    """
    Process an advertisement packet.

    Extracts contact info and updates the database/broadcasts to clients.
    For non-repeater contacts, triggers sync of recent contacts to radio for DM ACK support.
    """
    advert = try_parse_advertisement(raw_bytes)
    if not advert:
        logger.debug("Failed to parse advertisement packet")
        return

    logger.debug("Parsed advertisement from %s: %s", advert.public_key[:12], advert.name)

    # Try to find existing contact
    existing = await ContactRepository.get_by_key(advert.public_key)

    contact_data = {
        "public_key": advert.public_key,
        "name": advert.name,
        "lat": advert.lat,
        "lon": advert.lon,
        "last_advert": timestamp,
        "last_seen": timestamp,
    }

    await ContactRepository.upsert(contact_data)

    # Broadcast contact update to connected clients
    contact_type = existing.type if existing else 0
    broadcast_event("contact", {
        "public_key": advert.public_key,
        "name": advert.name,
        "type": contact_type,
        "flags": existing.flags if existing else 0,
        "last_path": existing.last_path if existing else None,
        "last_path_len": existing.last_path_len if existing else -1,
        "last_advert": timestamp,
        "lat": advert.lat,
        "lon": advert.lon,
        "last_seen": timestamp,
        "on_radio": existing.on_radio if existing else False,
    })

    # If this is not a repeater, trigger recent contacts sync to radio
    # This ensures we can auto-ACK DMs from recent contacts
    if contact_type != CONTACT_TYPE_REPEATER:
        # Import here to avoid circular import
        from app.radio_sync import sync_recent_contacts_to_radio
        asyncio.create_task(sync_recent_contacts_to_radio())
