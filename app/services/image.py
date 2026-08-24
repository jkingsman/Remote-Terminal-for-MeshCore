"""Bounded MeshCore SAR-compatible image session transport."""

from __future__ import annotations

import asyncio
import logging

from app.image_protocol import MAX_IMAGE_FRAGMENTS, ImageFetchRequest, ImagePacket
from app.keystore import get_public_key
from app.repository import ContactRepository, ImageRepository
from app.services.voice import RAW_MEDIA_FRAGMENT_DELAY_SECONDS, send_raw_to_contact
from app.websocket import broadcast_event

logger = logging.getLogger(__name__)
MAX_MISSING_INDICES_PER_REQUEST = 140


async def request_image_session(radio_manager, session: dict) -> None:
    peer_key = session.get("peer_public_key")
    if not peer_key:
        raise ValueError("image sender identity is unavailable")
    contact = await ContactRepository.get_by_key_or_prefix(peer_key)
    if contact is None:
        raise ValueError("image sender is not a known contact")
    public_key = get_public_key()
    if public_key is None or len(public_key) < 6:
        raise RuntimeError("local radio public key is unavailable")
    have = {index for index, _data in session["fragments"]}
    missing = tuple(index for index in range(session["fragment_count"]) if index not in have)
    requests = (
        [()]
        if not have
        else [
            missing[offset : offset + MAX_MISSING_INDICES_PER_REQUEST]
            for offset in range(0, len(missing), MAX_MISSING_INDICES_PER_REQUEST)
        ]
    )
    for indices in requests:
        request = ImageFetchRequest(
            session_id=session["session_id"],
            requester_key6=public_key[:6].hex(),
            missing_indices=indices,
        )
        await send_raw_to_contact(radio_manager, contact, request.encode())


async def handle_raw_image_payload(payload: bytes, radio_manager) -> bool:
    packet = ImagePacket.parse(payload)
    if packet is not None:
        session = await ImageRepository.get(packet.session_id)
        if session is None:
            logger.debug("Ignoring image fragment for unknown session %s", packet.session_id)
            return True
        try:
            inserted = await ImageRepository.add_fragment(
                packet.session_id, packet.index, packet.data
            )
        except ValueError as exc:
            logger.warning("Ignoring malformed image fragment: %s", exc)
            return True
        if inserted:
            session = await ImageRepository.get(packet.session_id)
            assert session is not None
            broadcast_event(
                "image_session",
                {
                    "session_id": packet.session_id,
                    "received": len(session["fragments"]),
                    "total": session["fragment_count"],
                },
            )
        return True

    request = ImageFetchRequest.parse(payload)
    if request is None:
        return False
    contact = await ContactRepository.get_by_key_or_prefix(request.requester_key6)
    if contact is None:
        logger.warning("Image fetch requester %s is unknown", request.requester_key6)
        return True
    session = await ImageRepository.get(request.session_id)
    if session is None:
        return True
    wanted = set(request.missing_indices) if request.missing_indices else None
    sent_count = 0
    for index, data in session["fragments"][:MAX_IMAGE_FRAGMENTS]:
        if wanted is not None and index not in wanted:
            continue
        if sent_count:
            await asyncio.sleep(RAW_MEDIA_FRAGMENT_DELAY_SECONDS)
        await send_raw_to_contact(
            radio_manager, contact, ImagePacket(request.session_id, index, data).encode()
        )
        sent_count += 1
    return True
