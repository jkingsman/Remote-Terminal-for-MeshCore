"""
Centralized packet processing for MeshCore messages.

This module handles:
- Storing raw packets
- Decrypting channel messages (GroupText) with stored channel keys
- Decrypting direct messages with stored contact keys (if private key available)
- Creating message entries for successfully decrypted packets
- Broadcasting updates via WebSocket
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
    try_decrypt_path,
    verify_advert_signature,
)
from app.keystore import get_private_key, get_public_key, has_private_key
from app.mcmp import McmpAppCodec
from app.models import (
    Contact,
    ContactUpsert,
    RawPacketBroadcast,
    RawPacketDecryptedInfo,
)
from app.path_utils import calculate_packet_hash
from app.region_resolver import resolve_region
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactAdvertPathRepository,
    ContactRepository,
    MessageRepository,
    RawPacketRepository,
)
from app.services.contact_reconciliation import (
    promote_prefix_contacts_for_contact,
    record_contact_name_and_reconcile,
)
from app.services.dm_ack_apply import apply_dm_ack_code
from app.services.messages import (
    create_dm_message_from_decrypted as _create_dm_message_from_decrypted,
)
from app.services.messages import (
    create_message_from_decrypted as _create_message_from_decrypted,
)
from app.websocket import broadcast_error, broadcast_event

logger = logging.getLogger(__name__)

_raw_observation_counter = count(1)


async def create_message_from_decrypted(
    packet_id: int,
    channel_key: str,
    sender: str | None,
    message_text: str,
    timestamp: int,
    received_at: int | None = None,
    path: str | None = None,
    path_len: int | None = None,
    rssi: int | None = None,
    snr: float | None = None,
    channel_name: str | None = None,
    realtime: bool = True,
    packet_hash: str | None = None,
    transport_code: int | None = None,
    region: str | None = None,
) -> int | None:
    """Store a decrypted channel message via the shared message service."""
    return await _create_message_from_decrypted(
        packet_id=packet_id,
        channel_key=channel_key,
        sender=sender,
        message_text=message_text,
        timestamp=timestamp,
        received_at=received_at,
        path=path,
        path_len=path_len,
        rssi=rssi,
        snr=snr,
        channel_name=channel_name,
        realtime=realtime,
        broadcast_fn=broadcast_event,
        packet_hash=packet_hash,
        transport_code=transport_code,
        region=region,
    )


async def create_dm_message_from_decrypted(
    packet_id: int,
    decrypted: DecryptedDirectMessage,
    their_public_key: str,
    our_public_key: str | None,
    received_at: int | None = None,
    path: str | None = None,
    path_len: int | None = None,
    rssi: int | None = None,
    snr: float | None = None,
    outgoing: bool = False,
    realtime: bool = True,
    packet_hash: str | None = None,
    transport_code: int | None = None,
    region: str | None = None,
) -> int | None:
    """Store a decrypted direct message via the shared message service."""
    return await _create_dm_message_from_decrypted(
        packet_id=packet_id,
        decrypted=decrypted,
        their_public_key=their_public_key,
        our_public_key=our_public_key,
        received_at=received_at,
        path=path,
        path_len=path_len,
        rssi=rssi,
        snr=snr,
        outgoing=outgoing,
        realtime=realtime,
        broadcast_fn=broadcast_event,
        packet_hash=packet_hash,
        transport_code=transport_code,
        region=region,
    )


async def run_historical_dm_decryption(
    private_key_bytes: bytes,
    contact_public_key_bytes: bytes,
    contact_public_key_hex: str,
    display_name: str | None = None,
) -> None:
    """Background task to decrypt historical DM packets with contact's key."""
    from app.websocket import broadcast_success

    total = 0
    decrypted_count = 0

    logger.info("Starting historical DM decryption scan for undecrypted TEXT_MESSAGE packets")

    # Derive our public key from the private key
    our_public_key_bytes = derive_public_key(private_key_bytes)

    async for (
        packet_id,
        packet_data,
        packet_timestamp,
    ) in RawPacketRepository.stream_undecrypted_text_messages():
        total += 1
        result = try_decrypt_dm(
            packet_data,
            private_key_bytes,
            contact_public_key_bytes,
            our_public_key=None,
        )

        if result is not None:
            src_hash = result.src_hash.lower()
            dest_hash = result.dest_hash.lower()
            our_first_byte = format(our_public_key_bytes[0], "02x").lower()

            if src_hash == our_first_byte and dest_hash != our_first_byte:
                outgoing = True
            else:
                outgoing = False

            packet_info = parse_packet(packet_data)
            path_hex = packet_info.path.hex() if packet_info else None
            path_len = packet_info.path_length if packet_info else None

            msg_id = await create_dm_message_from_decrypted(
                packet_id=packet_id,
                decrypted=result,
                their_public_key=contact_public_key_hex,
                our_public_key=our_public_key_bytes.hex(),
                received_at=packet_timestamp,
                path=path_hex,
                path_len=path_len,
                outgoing=outgoing,
                realtime=False,
            )

            if msg_id is not None:
                decrypted_count += 1

    if total == 0:
        logger.info("No undecrypted TEXT_MESSAGE packets to process")
        return

    logger.info(
        "Historical DM decryption complete: %d/%d packets decrypted",
        decrypted_count,
        total,
    )

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
    """
    ts = timestamp or int(time.time())
    observation_id = next(_raw_observation_counter)

    packet_id, is_new_packet = await RawPacketRepository.create(raw_bytes, ts)
    raw_hex = raw_bytes.hex()

    packet_info = parse_packet(raw_bytes)
    payload_type = packet_info.payload_type if packet_info else None
    payload_type_name = payload_type.name if payload_type else "Unknown"

    if packet_info is None and len(raw_bytes) > 2:
        logger.warning(
            "Failed to parse %d-byte packet (id=%d); stored undecrypted",
            len(raw_bytes),
            packet_id,
        )

    path_hex = packet_info.path.hex() if packet_info and packet_info.path else ""
    route_type_name = (
        getattr(packet_info.route_type, "name", packet_info.route_type)
        if packet_info
        else "Unknown"
    )
    logger.debug(
        "Packet received: type=%s, route=%s, hops=%s, is_new=%s, packet_id=%d, path='%s'",
        payload_type_name,
        route_type_name,
        packet_info.path_length if packet_info else "?",
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

    pkt_hash = calculate_packet_hash(raw_bytes)

    transport_code: int | None = None
    region: str | None = None
    if packet_info is not None and packet_info.transport_codes is not None:
        transport_code = packet_info.transport_codes[0]
        try:
            settings = await AppSettingsRepository.get()
            region = resolve_region(
                int(packet_info.payload_type),
                packet_info.payload,
                transport_code,
                settings.known_regions,
            )
        except Exception:
            logger.debug("Region resolution failed for packet %d", packet_id, exc_info=True)

    if payload_type == PayloadType.GROUP_TEXT:
        decrypt_result = await _process_group_text(
            raw_bytes,
            packet_id,
            ts,
            packet_info,
            rssi=rssi,
            snr=snr,
            packet_hash=pkt_hash,
            transport_code=transport_code,
            region=region,
        )
        if decrypt_result:
            result.update(decrypt_result)

    elif payload_type == PayloadType.ADVERT:
        await _process_advertisement(raw_bytes, ts, packet_info)

    elif payload_type == PayloadType.TEXT_MESSAGE:
        decrypt_result = await _process_direct_message(
            raw_bytes,
            packet_id,
            ts,
            packet_info,
            rssi=rssi,
            snr=snr,
            packet_hash=pkt_hash,
            transport_code=transport_code,
            region=region,
        )
        if decrypt_result:
            result.update(decrypt_result)

    elif payload_type == PayloadType.PATH:
        await _process_path_packet(raw_bytes, ts, packet_info)

    elif payload_type == PayloadType.ACK:
        if packet_info is not None and len(packet_info.payload) >= 4:
            ack_code = packet_info.payload[:4].hex()
            matched = await apply_dm_ack_code(ack_code, broadcast_fn=broadcast_event)
            if matched:
                logger.info("Applied standalone ACK %s from raw packet", ack_code)
            else:
                logger.debug("Buffered/ignored standalone ACK %s from raw packet", ack_code)

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
            sender_timestamp=result.get("sender_timestamp"),
            message=result.get("message"),
        )
        if result["decrypted"]
        else None,
        transport_code=transport_code,
        region=region,
    )
    broadcast_event("raw_packet", broadcast_payload.model_dump())

    return result


async def _process_group_text(
    raw_bytes: bytes,
    packet_id: int,
    timestamp: int,
    packet_info: PacketInfo | None,
    rssi: int | None = None,
    snr: float | None = None,
    packet_hash: str | None = None,
    transport_code: int | None = None,
    region: str | None = None,
) -> dict | None:
    """
    Process a GroupText (channel message) packet.

    Tries all known channel keys to decrypt.
    Creates a message entry if successful (or adds path to existing if duplicate).
    """
    channels = await ChannelRepository.get_all()

    for channel in channels:
        try:
            channel_key_bytes = bytes.fromhex(channel.key)
        except ValueError:
            continue

        decrypted = try_decrypt_packet_with_channel_key(raw_bytes, channel_key_bytes)
        if not decrypted:
            continue

        logger.debug("Decrypted GroupText for channel %s: %s", channel.name, decrypted.message[:50])

        # Decode MCMP v3 payload if present
        message_text = decrypted.message
        if message_text.lstrip().startswith("mcmp3:"):
            decoded_msg = McmpAppCodec.try_decode_text_payload_message(message_text)
            if decoded_msg is not None:
                message_text = decoded_msg.text
                logger.debug(
                    "Decoded MCMP v3 channel message (signature_status=%s)",
                    decoded_msg.signature_status,
                )
            else:
                logger.warning("Failed to decode MCMP v3 payload, keeping raw text")

        msg_id = await create_message_from_decrypted(
            packet_id=packet_id,
            channel_key=channel.key,
            channel_name=channel.name,
            sender=decrypted.sender,
            message_text=message_text,
            timestamp=decrypted.timestamp,
            received_at=timestamp,
            path=packet_info.path.hex() if packet_info else None,
            path_len=packet_info.path_length if packet_info else None,
            rssi=rssi,
            snr=snr,
            packet_hash=packet_hash,
            transport_code=transport_code,
            region=region,
        )

        return {
            "decrypted": True,
            "channel_name": channel.name,
            "sender": decrypted.sender,
            "message_id": msg_id,
            "channel_key": channel.key,
            "sender_timestamp": decrypted.timestamp,
            "message": message_text,
        }

    return None


async def _process_advertisement(
    raw_bytes: bytes,
    timestamp: int,
    packet_info: PacketInfo | None = None,
) -> None:
    """Process an advertisement packet."""
    if packet_info is None:
        packet_info = parse_packet(raw_bytes)
    if packet_info is None:
        logger.debug("Failed to parse advertisement packet")
        return

    advert = parse_advertisement(packet_info.payload, raw_packet=raw_bytes)
    if not advert:
        logger.debug("Failed to parse advertisement payload")
        return

    if not verify_advert_signature(packet_info.payload):
        logger.warning(
            "Dropping advertisement with invalid signature from %s (packet %s)",
            advert.public_key[:12],
            raw_bytes.hex().upper(),
        )
        return

    new_path_len = packet_info.path_length
    new_path_hex = packet_info.path.hex() if packet_info.path else ""

    existing = await ContactRepository.get_by_key(advert.public_key.lower())

    logger.debug(
        "Parsed advertisement from %s: %s (role=%d, lat=%s, lon=%s, advert_path_len=%d)",
        advert.public_key[:12],
        advert.name,
        advert.device_role,
        advert.lat,
        advert.lon,
        new_path_len,
    )

    contact_type = (
        advert.device_role if advert.device_role > 0 else (existing.type if existing else 0)
    )

    if existing is None and contact_type > 0:
        from app.repository import AppSettingsRepository

        settings = await AppSettingsRepository.get()
        if contact_type in settings.discovery_blocked_types:
            logger.debug(
                "Skipping new contact %s: type %d is in discovery_blocked_types",
                advert.public_key[:12],
                contact_type,
            )
            return

    contact_upsert = ContactUpsert(
        public_key=advert.public_key.lower(),
        name=advert.name,
        type=contact_type,
        lat=advert.lat,
        lon=advert.lon,
        last_advert=timestamp,
        last_seen=timestamp,
        first_seen=timestamp,
    )

    await ContactRepository.upsert(contact_upsert)

    await ContactAdvertPathRepository.record_observation(
        public_key=advert.public_key.lower(),
        path_hex=new_path_hex,
        timestamp=timestamp,
        max_paths=10,
        hop_count=new_path_len,
    )
    promoted_keys = await promote_prefix_contacts_for_contact(
        public_key=advert.public_key,
        log=logger,
    )
    await record_contact_name_and_reconcile(
        public_key=advert.public_key,
        contact_name=advert.name,
        timestamp=timestamp,
        log=logger,
    )

    db_contact = await ContactRepository.get_by_key(advert.public_key.lower())
    if db_contact:
        broadcast_event("contact", db_contact.model_dump())
        for old_key in promoted_keys:
            broadcast_event(
                "contact_resolved",
                {
                    "previous_public_key": old_key,
                    "contact": db_contact.model_dump(),
                },
            )
    else:
        broadcast_event(
            "contact",
            Contact(**contact_upsert.model_dump(exclude_none=True)).model_dump(),
        )

    if existing is None:
        from app.repository import AppSettingsRepository

        settings = await AppSettingsRepository.get()
        if settings.auto_decrypt_dm_on_advert:
            await start_historical_dm_decryption(None, advert.public_key.lower(), advert.name)


async def _process_direct_message(
    raw_bytes: bytes,
    packet_id: int,
    timestamp: int,
    packet_info: PacketInfo | None,
    rssi: int | None = None,
    snr: float | None = None,
    packet_hash: str | None = None,
    transport_code: int | None = None,
    region: str | None = None,
) -> dict | None:
    """
    Process a TEXT_MESSAGE (direct message) packet.
    """
    if not has_private_key():
        return None

    private_key = get_private_key()
    our_public_key = get_public_key()
    if private_key is None or our_public_key is None:
        return None

    if packet_info is None:
        packet_info = parse_packet(raw_bytes)
    if packet_info is None or packet_info.payload is None:
        return None

    if len(packet_info.payload) < 4:
        return None

    dest_hash = format(packet_info.payload[0], "02x").lower()
    src_hash = format(packet_info.payload[1], "02x").lower()

    our_first_byte = format(our_public_key[0], "02x").lower()

    if dest_hash == our_first_byte and src_hash != our_first_byte:
        is_outgoing = False
    elif src_hash == our_first_byte and dest_hash != our_first_byte:
        is_outgoing = True
    elif dest_hash == our_first_byte and src_hash == our_first_byte:
        is_outgoing = False
        logger.debug("Ambiguous DM direction (first bytes match), defaulting to incoming")
    else:
        return None

    match_hash = dest_hash if is_outgoing else src_hash

    candidate_contacts = await ContactRepository.get_by_pubkey_first_byte(match_hash)

    if not candidate_contacts:
        logger.debug("No contacts found matching hash %s for DM decryption", match_hash)
        return None

    for contact in candidate_contacts:
        try:
            contact_public_key = bytes.fromhex(contact.public_key)
        except ValueError:
            continue

        result = try_decrypt_dm(
            raw_bytes,
            private_key,
            contact_public_key,
            our_public_key=our_public_key if not is_outgoing else None,
        )

        if result is not None:
            effective_outgoing = is_outgoing
            if not is_outgoing and dest_hash == src_hash:
                existing_outgoing = await MessageRepository.get_by_content(
                    msg_type="PRIV",
                    conversation_key=contact.public_key.lower(),
                    text=result.message,
                    sender_timestamp=result.timestamp,
                    outgoing=True,
                )
                if existing_outgoing is not None:
                    effective_outgoing = True
                    logger.debug(
                        "Ambiguous DM resolved as outgoing echo (matched existing sent msg %d)",
                        existing_outgoing.id,
                    )

            # Decode MCMP v3 payload if present (no signature verification for DMs)
            message_text = result.message
            if message_text.lstrip().startswith("mcmp3:"):
                decoded_msg = McmpAppCodec.try_decode_text_payload_message(message_text)
                if decoded_msg is not None:
                    message_text = decoded_msg.text
                    logger.debug("Decoded MCMP v3 direct message")
                else:
                    logger.warning("Failed to decode MCMP v3 DM payload, keeping raw text")

            logger.debug(
                "Decrypted DM %s contact %s: %s",
                "to" if effective_outgoing else "from",
                contact.name or contact.public_key[:12],
                message_text[:50] if message_text else "",
            )

            msg_id = await create_dm_message_from_decrypted(
                packet_id=packet_id,
                decrypted=result,
                their_public_key=contact.public_key,
                our_public_key=our_public_key.hex(),
                received_at=timestamp,
                path=packet_info.path.hex() if packet_info else None,
                path_len=packet_info.path_length if packet_info else None,
                rssi=rssi,
                snr=snr,
                outgoing=effective_outgoing,
                packet_hash=packet_hash,
                transport_code=transport_code,
                region=region,
            )

            return {
                "decrypted": True,
                "contact_name": contact.name,
                "sender": contact.name or contact.public_key[:12],
                "message_id": msg_id,
                "contact_key": contact.public_key,
                "sender_timestamp": result.timestamp,
                "message": message_text,
            }

    logger.debug("Could not decrypt DM with any of %d candidate contacts", len(candidate_contacts))
    return None


async def _process_path_packet(
    raw_bytes: bytes,
    timestamp: int,
    packet_info: PacketInfo | None,
) -> None:
    """Process a PATH packet and update the learned direct route."""
    if not has_private_key():
        return

    private_key = get_private_key()
    our_public_key = get_public_key()
    if private_key is None or our_public_key is None:
        return

    if packet_info is None:
        packet_info = parse_packet(raw_bytes)
    if packet_info is None or packet_info.payload is None or len(packet_info.payload) < 4:
        return

    dest_hash = format(packet_info.payload[0], "02x").lower()
    src_hash = format(packet_info.payload[1], "02x").lower()
    our_first_byte = format(our_public_key[0], "02x").lower()
    if dest_hash != our_first_byte:
        return

    candidate_contacts = await ContactRepository.get_by_pubkey_first_byte(src_hash)
    if not candidate_contacts:
        logger.debug("No contacts found matching hash %s for PATH decryption", src_hash)
        return

    for contact in candidate_contacts:
        if len(contact.public_key) != 64:
            continue
        try:
            contact_public_key = bytes.fromhex(contact.public_key)
        except ValueError:
            continue

        result = try_decrypt_path(
            raw_packet=raw_bytes,
            our_private_key=private_key,
            their_public_key=contact_public_key,
            our_public_key=our_public_key,
        )
        if result is None:
            continue

        await ContactRepository.update_direct_path(
            contact.public_key,
            result.returned_path.hex(),
            result.returned_path_len,
            result.returned_path_hash_mode,
            updated_at=timestamp,
        )

        if result.extra_type == PayloadType.ACK and len(result.extra) >= 4:
            ack_code = result.extra[:4].hex()
            matched = await apply_dm_ack_code(ack_code, broadcast_fn=broadcast_event)
            if matched:
                logger.info(
                    "Applied bundled PATH ACK for %s via contact %s",
                    ack_code,
                    contact.public_key[:12],
                )
            else:
                logger.debug(
                    "Buffered bundled PATH ACK %s via contact %s",
                    ack_code,
                    contact.public_key[:12],
                )
        elif result.extra_type == PayloadType.RESPONSE and len(result.extra) > 0:
            logger.debug(
                "Observed bundled PATH RESPONSE from %s (%d bytes)",
                contact.public_key[:12],
                len(result.extra),
            )

        refreshed_contact = await ContactRepository.get_by_key(contact.public_key)
        if refreshed_contact is not None:
            broadcast_event("contact", refreshed_contact.model_dump())
        return

    logger.debug(
        "Could not decrypt PATH packet with any of %d candidate contacts", len(candidate_contacts)
    )