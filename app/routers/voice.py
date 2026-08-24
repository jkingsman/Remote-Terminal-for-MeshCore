from __future__ import annotations

import asyncio
import io
import logging
import secrets
import time
import wave

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.event_handlers import track_pending_ack
from app.repository import ChannelRepository, ContactRepository, MessageRepository, VoiceRepository
from app.services.message_send import (
    send_channel_message_to_channel,
    send_direct_message_to_contact,
)
from app.services.radio_runtime import radio_runtime as radio_manager
from app.services.voice import request_voice_session
from app.voice_codec import Codec2, Codec2Unavailable, codec2_available
from app.voice_protocol import (
    MAX_VOICE_DURATION_MS,
    VoiceEnvelope,
    VoiceMode,
    fragment_codec2,
)
from app.websocket import broadcast_error, broadcast_event

router = APIRouter(prefix="/voice", tags=["voice"])
MAX_PCM_BYTES = MAX_VOICE_DURATION_MS * 8_000 * 2 // 1000
MIN_PCM_BYTES = 8_000 * 2 // 5
VOICE_CACHE_TTL_SECONDS = 86_400
logger = logging.getLogger(__name__)


def _voice_envelope_body(message) -> str:
    """Return the protocol body, excluding channel presentation metadata."""
    if message.type == "CHAN" and message.sender_name:
        sender_prefix = f"{message.sender_name}: "
        if message.text.startswith(sender_prefix):
            return message.text[len(sender_prefix) :]
    return message.text


@router.get("/capabilities")
async def capabilities() -> dict:
    return {
        "codec2_available": codec2_available(),
        "secure_context_required": True,
        "max_duration_ms": MAX_VOICE_DURATION_MS,
        "mode": int(VoiceMode.MODE_1300),
        "mode_label": "1300",
    }


@router.post("/send")
async def send_voice(
    request: Request,
    conversation_type: str = Query(pattern="^(PRIV|CHAN)$"),
    conversation_key: str = Query(min_length=12, max_length=64),
) -> dict:
    await VoiceRepository.enforce_cache_limit()
    radio_manager.require_connected()
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type != "application/octet-stream":
        raise HTTPException(
            status_code=415, detail="voice input must be PCM16 application/octet-stream"
        )
    pcm = await request.body()
    if len(pcm) < MIN_PCM_BYTES:
        raise HTTPException(status_code=422, detail="voice recording is too short")
    if len(pcm) > MAX_PCM_BYTES or len(pcm) % 2:
        raise HTTPException(status_code=413, detail="voice recording exceeds the 10 second limit")

    mode = VoiceMode.MODE_1300
    try:
        encoded = await asyncio.to_thread(_encode, pcm, mode)
    except Codec2Unavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    session_id = secrets.token_hex(4)
    packets = fragment_codec2(session_id, encoded, mode)
    duration_ms = min(MAX_VOICE_DURATION_MS, len(pcm) * 1000 // (8_000 * 2))
    envelope = VoiceEnvelope(session_id, mode, len(packets), duration_ms).encode()

    # Cache before advertising VE3 so a fast peer fetch cannot race the sender.
    await VoiceRepository.upsert_session(
        session_id=session_id,
        message_id=None,
        direction="outgoing",
        conversation_type=conversation_type,
        conversation_key=conversation_key,
        peer_public_key=conversation_key if conversation_type == "PRIV" else None,
        mode=int(mode),
        duration_ms=duration_ms,
        packet_count=len(packets),
        state="complete",
        ttl_seconds=VOICE_CACHE_TTL_SECONDS,
    )
    for packet in packets:
        await VoiceRepository.add_fragment(session_id, packet.index, packet.codec2_data)

    if conversation_type == "PRIV":
        contact = await ContactRepository.get_by_key_or_prefix(conversation_key)
        if contact is None or len(contact.public_key) != 64:
            raise HTTPException(status_code=404, detail="voice destination contact not found")
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
            raise HTTPException(status_code=404, detail="voice destination channel not found")
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

    await VoiceRepository.upsert_session(
        session_id=session_id,
        message_id=message.id,
        direction="outgoing",
        conversation_type=conversation_type,
        conversation_key=normalized_key,
        peer_public_key=peer_key,
        mode=int(mode),
        duration_ms=duration_ms,
        packet_count=len(packets),
        state="complete",
        ttl_seconds=VOICE_CACHE_TTL_SECONDS,
    )
    return {"session_id": session_id, "envelope": envelope, "message": message.model_dump()}


@router.post("/messages/{message_id}/fetch")
async def fetch_voice(message_id: int) -> dict:
    await VoiceRepository.enforce_cache_limit()
    message = await MessageRepository.get_by_id(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="message not found")
    envelope_text = _voice_envelope_body(message)
    logger.debug(
        "Parsing voice envelope for %s message %d: stored=%r body=%r",
        message.type,
        message.id,
        message.text,
        envelope_text,
    )
    envelope = VoiceEnvelope.parse(envelope_text)
    if envelope is None:
        raise HTTPException(status_code=422, detail="message is not a VE3 voice envelope")
    if envelope.duration_ms > MAX_VOICE_DURATION_MS:
        raise HTTPException(status_code=422, detail="voice message exceeds the 10 second limit")
    existing = await VoiceRepository.get(envelope.session_id)
    if existing is not None and existing["message_id"] not in (None, message.id):
        raise HTTPException(status_code=409, detail="voice session ID belongs to another message")
    peer_key = message.conversation_key if message.type == "PRIV" else message.sender_key
    await VoiceRepository.upsert_session(
        session_id=envelope.session_id,
        message_id=message.id,
        direction="outgoing" if message.outgoing else "incoming",
        conversation_type=message.type,
        conversation_key=message.conversation_key,
        peer_public_key=peer_key,
        mode=int(envelope.mode),
        duration_ms=envelope.duration_ms,
        packet_count=envelope.total,
        state="available" if not message.outgoing else "complete",
        ttl_seconds=VOICE_CACHE_TTL_SECONDS,
    )
    session = await VoiceRepository.get(envelope.session_id)
    assert session is not None
    if session["state"] != "complete":
        radio_manager.require_connected()
        try:
            await request_voice_session(radio_manager, session)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _session_payload(session)


@router.get("/sessions/{session_id}")
async def get_voice_session(session_id: str) -> dict:
    session = await VoiceRepository.get(session_id.lower())
    if session is None:
        raise HTTPException(status_code=404, detail="voice session not found")
    return _session_payload(session)


@router.get("/sessions/{session_id}/audio")
async def get_voice_audio(session_id: str) -> Response:
    session = await VoiceRepository.get(session_id.lower())
    if session is None:
        raise HTTPException(status_code=404, detail="voice session not found")
    fragments = session["fragments"]
    if len(fragments) != session["packet_count"]:
        raise HTTPException(status_code=409, detail="voice session is incomplete")
    encoded = b"".join(data for _index, data in fragments)
    try:
        pcm = await asyncio.to_thread(_decode, encoded, VoiceMode(session["mode"]))
    except (Codec2Unavailable, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    pcm = pcm[: session["duration_ms"] * 8_000 * 2 // 1000]
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8_000)
        wav.writeframes(pcm)
    return Response(output.getvalue(), media_type="audio/wav")


def _encode(pcm: bytes, mode: VoiceMode) -> bytes:
    with Codec2(mode) as codec:
        return codec.encode_pcm16le(pcm)


def _decode(encoded: bytes, mode: VoiceMode) -> bytes:
    with Codec2(mode) as codec:
        return codec.decode_pcm16le(encoded)


def _session_payload(session: dict) -> dict:
    received = len(session["fragments"])
    return {
        "session_id": session["session_id"],
        "state": "complete" if received == session["packet_count"] else session["state"],
        "mode": session["mode"],
        "duration_ms": session["duration_ms"],
        "packet_count": session["packet_count"],
        "received_count": received,
        "missing_indices": [
            index
            for index in range(session["packet_count"])
            if index not in {fragment[0] for fragment in session["fragments"]}
        ],
    }
