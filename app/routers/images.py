from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.event_handlers import track_pending_ack
from app.image_protocol import (
    MAX_ENCODED_IMAGE_BYTES,
    ImageEnvelope,
    ImageFormat,
    fragment_image,
)
from app.repository import ChannelRepository, ContactRepository, ImageRepository, MessageRepository
from app.services.image import request_image_session
from app.services.message_send import (
    send_channel_message_to_channel,
    send_direct_message_to_contact,
)
from app.services.radio_runtime import radio_runtime as radio_manager
from app.websocket import broadcast_error, broadcast_event

router = APIRouter(prefix="/images", tags=["images"])
IMAGE_CACHE_TTL_SECONDS = 86_400


def _image_envelope_body(message) -> str:
    """Return the protocol body, excluding channel presentation metadata."""
    if message.type == "CHAN" and message.sender_name:
        sender_prefix = f"{message.sender_name}: "
        if message.text.startswith(sender_prefix):
            return message.text[len(sender_prefix) :]
    return message.text


def _validate_encoded_image(data: bytes, format_id: ImageFormat) -> None:
    if not 1 <= len(data) <= MAX_ENCODED_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="encoded image is too large")
    if format_id == ImageFormat.JPEG:
        valid = len(data) >= 4 and data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9"
    else:
        valid = len(data) >= 16 and data[4:8] == b"ftyp" and (
            b"avif" in data[8:32] or b"avis" in data[8:32]
        )
    if not valid:
        raise HTTPException(status_code=422, detail="encoded image does not match its format")


@router.post("/send")
async def send_image(
    request: Request,
    conversation_type: str = Query(pattern="^(PRIV|CHAN)$"),
    conversation_key: str = Query(min_length=12, max_length=64),
    format_id: int = Query(ge=0, le=1),
    width: int = Query(ge=1, le=256),
    height: int = Query(ge=1, le=256),
) -> dict:
    await ImageRepository.enforce_cache_limit()
    radio_manager.require_connected()
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type != "application/octet-stream":
        raise HTTPException(status_code=415, detail="image input must be application/octet-stream")
    encoded = await request.body()
    image_format = ImageFormat(format_id)
    _validate_encoded_image(encoded, image_format)
    session_id = secrets.token_hex(4)
    packets = fragment_image(session_id, encoded)
    envelope = ImageEnvelope(
        session_id, image_format, len(packets), width, height, len(encoded)
    ).encode()

    await ImageRepository.create_session(
        session_id=session_id,
        message_id=None,
        direction="outgoing",
        conversation_type=conversation_type,
        conversation_key=conversation_key,
        peer_public_key=conversation_key if conversation_type == "PRIV" else None,
        format_id=format_id,
        width=width,
        height=height,
        size_bytes=len(encoded),
        fragment_count=len(packets),
        state="complete",
        ttl_seconds=IMAGE_CACHE_TTL_SECONDS,
    )
    for packet in packets:
        await ImageRepository.add_fragment(session_id, packet.index, packet.data)

    if conversation_type == "PRIV":
        contact = await ContactRepository.get_by_key_or_prefix(conversation_key)
        if contact is None or len(contact.public_key) != 64:
            raise HTTPException(status_code=404, detail="image destination contact not found")
        message = await send_direct_message_to_contact(
            contact=contact,
            text=envelope,
            radio_manager=radio_manager,
            broadcast_fn=broadcast_event,
            track_pending_ack_fn=track_pending_ack,
            now_fn=time.time,
            message_repository=MessageRepository,
            contact_repository=ContactRepository,
        )
        peer_key = contact.public_key
        normalized_key = contact.public_key
    else:
        channel = await ChannelRepository.get_by_key(conversation_key)
        if channel is None:
            raise HTTPException(status_code=404, detail="image destination channel not found")
        message = await send_channel_message_to_channel(
            channel=channel,
            channel_key_upper=channel.key.upper(),
            key_bytes=bytes.fromhex(channel.key),
            text=envelope,
            radio_manager=radio_manager,
            broadcast_fn=broadcast_event,
            error_broadcast_fn=broadcast_error,
            now_fn=time.time,
            temp_radio_slot=0,
            message_repository=MessageRepository,
        )
        peer_key = None
        normalized_key = channel.key

    await ImageRepository.create_session(
        session_id=session_id,
        message_id=message.id,
        direction="outgoing",
        conversation_type=conversation_type,
        conversation_key=normalized_key,
        peer_public_key=peer_key,
        format_id=format_id,
        width=width,
        height=height,
        size_bytes=len(encoded),
        fragment_count=len(packets),
        state="complete",
        ttl_seconds=IMAGE_CACHE_TTL_SECONDS,
    )
    return {"session_id": session_id, "envelope": envelope, "message": message.model_dump()}


@router.post("/messages/{message_id}/fetch")
async def fetch_image(message_id: int) -> dict:
    await ImageRepository.enforce_cache_limit()
    message = await MessageRepository.get_by_id(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="message not found")
    envelope = ImageEnvelope.parse(_image_envelope_body(message))
    if envelope is None:
        raise HTTPException(status_code=422, detail="message is not a valid IE4 image envelope")
    peer_key = message.conversation_key if message.type == "PRIV" else message.sender_key
    try:
        await ImageRepository.create_session(
            session_id=envelope.session_id,
            message_id=message.id,
            direction="outgoing" if message.outgoing else "incoming",
            conversation_type=message.type,
            conversation_key=message.conversation_key,
            peer_public_key=peer_key,
            format_id=int(envelope.format),
            width=envelope.width,
            height=envelope.height,
            size_bytes=envelope.size_bytes,
            fragment_count=envelope.total,
            state="available" if not message.outgoing else "complete",
            ttl_seconds=IMAGE_CACHE_TTL_SECONDS,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session = await ImageRepository.get(envelope.session_id)
    assert session is not None
    if len(session["fragments"]) != session["fragment_count"]:
        radio_manager.require_connected()
        await request_image_session(radio_manager, session)
    return _session_payload(session)


@router.get("/sessions/{session_id}")
async def get_image_session(session_id: str) -> dict:
    session = await ImageRepository.get(session_id.lower())
    if session is None:
        raise HTTPException(status_code=404, detail="image session not found")
    return _session_payload(session)


@router.get("/sessions/{session_id}/content")
async def get_image_content(session_id: str) -> Response:
    session = await ImageRepository.get(session_id.lower())
    if session is None:
        raise HTTPException(status_code=404, detail="image session not found")
    if len(session["fragments"]) != session["fragment_count"]:
        raise HTTPException(status_code=409, detail="image session is incomplete")
    encoded = b"".join(data for _index, data in session["fragments"])
    if len(encoded) != session["size_bytes"]:
        raise HTTPException(status_code=409, detail="reassembled image size is invalid")
    media_type = "image/avif" if session["format"] == 0 else "image/jpeg"
    return Response(encoded, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})


def _session_payload(session: dict) -> dict:
    received = len(session["fragments"])
    return {
        "session_id": session["session_id"],
        "state": "complete" if received == session["fragment_count"] else session["state"],
        "format": session["format"],
        "width": session["width"],
        "height": session["height"],
        "size_bytes": session["size_bytes"],
        "fragment_count": session["fragment_count"],
        "received_count": received,
        "missing_indices": [
            index for index in range(session["fragment_count"])
            if index not in {fragment[0] for fragment in session["fragments"]}
        ],
    }
