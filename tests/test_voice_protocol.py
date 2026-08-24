import struct
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.voice import _voice_envelope_body, fetch_voice
from app.services.voice import _raw_frame_for_contact, _raw_route_for_contact, request_voice_session
from app.voice_codec import Codec2, VoiceMode, codec2_available
from app.voice_protocol import (
    VoiceEnvelope,
    VoiceFetchRequest,
    VoicePacket,
    encode_fragment_ack,
    fragment_codec2,
    parse_fragment_ack,
)


def test_meshcore_sar_ve3_compatibility_vector():
    parsed = VoiceEnvelope.parse("VE3:jbxb73:3:c:a")
    assert parsed == VoiceEnvelope("45abcdef", VoiceMode.MODE_1300, 12, 10_000)
    assert parsed.encode() == "VE3:jbxb73:3:c:a"


def test_private_voice_body_is_passed_to_parser_unchanged():
    message = SimpleNamespace(
        type="PRIV",
        text="VE3:jbxb73:3:c:a",
        sender_name=None,
    )

    body = _voice_envelope_body(message)

    assert body == "VE3:jbxb73:3:c:a"
    assert VoiceEnvelope.parse(body) is not None


def test_channel_voice_body_excludes_exact_sender_presentation_prefix():
    message = SimpleNamespace(
        type="CHAN",
        text="Alice: VE3:jbxb73:3:c:a",
        sender_name="Alice",
    )

    body = _voice_envelope_body(message)

    assert body == "VE3:jbxb73:3:c:a"
    assert VoiceEnvelope.parse(body) is not None


@pytest.mark.parametrize(
    ("text", "sender_name"),
    [
        ("Alice: ordinary channel text", "Alice"),
        ("Alice: VE3:!:3:c:a", "Alice"),
        ("Mallory: VE3:jbxb73:3:c:a", "Alice"),
    ],
)
def test_channel_body_extraction_does_not_turn_non_voice_or_mismatched_metadata_into_voice(
    text, sender_name
):
    message = SimpleNamespace(type="CHAN", text=text, sender_name=sender_name)

    assert VoiceEnvelope.parse(_voice_envelope_body(message)) is None


@pytest.mark.parametrize(
    "invalid",
    ["VE2:jpia9:3:c:a", "VE3:!:3:c:a", "VE3:jpia9:z:c:a", "VE3:jpia9:3:0:a"],
)
def test_ve3_rejects_malformed_or_unbounded_values(invalid):
    assert VoiceEnvelope.parse(invalid) is None


def test_voice_packet_binary_compatibility_vector():
    packet = VoicePacket("00112233", 7, bytes.fromhex("aabbcc"))
    wire = bytes.fromhex("560011223307aabbcc")
    assert packet.encode() == wire
    assert VoicePacket.parse(wire) == packet
    assert VoicePacket.parse(wire[:6]) is None
    assert VoicePacket.parse(b"X" + wire[1:]) is None


def test_fetch_request_binary_round_trip_and_duplicate_rejection():
    request = VoiceFetchRequest("00112233", "aabbccddeeff", (1, 3, 4))
    wire = bytes.fromhex("720011223301aabbccddeeff03010304")
    assert request.encode() == wire
    assert VoiceFetchRequest.parse(wire) == request
    assert VoiceFetchRequest.parse(wire[:-1]) is None
    assert VoiceFetchRequest.parse(wire[:-3] + bytes([2, 1, 1])) is None


def test_fragment_ack_compatibility_vector():
    wire = bytes.fromhex("760011223307")
    assert encode_fragment_ack("00112233", 7) == wire
    assert parse_fragment_ack(wire) == ("00112233", 7)


def test_mode1300_fragmentation_matches_sar_packet_duration():
    encoded = bytes(range(175))
    packets = fragment_codec2("00112233", encoded, VoiceMode.MODE_1300)
    assert [len(packet.codec2_data) for packet in packets] == [154, 21]
    assert b"".join(packet.codec2_data for packet in packets) == encoded


def test_raw_command_route_framing_matches_legacy_companion_format():
    class Contact:
        def effective_route_tuple(self):
            return "a1b2", 2, 0

    assert _raw_frame_for_contact(Contact(), b"voice") == b"\x02\xa1\xb2voice"


@pytest.mark.parametrize(
    ("route", "packed_path_len"),
    [
        (("a1b2c3d4", 2, 1), 0x42),
        (("010203040506", 2, 2), 0x82),
    ],
)
def test_raw_command_route_framing_packs_meshcore_hash_mode(route, packed_path_len):
    class Contact:
        def effective_route_tuple(self):
            return route

    frame = _raw_frame_for_contact(Contact(), b"media")

    assert frame == bytes([packed_path_len]) + bytes.fromhex(route[0]) + b"media"


async def test_raw_media_uses_latest_direct_advert_as_zero_hop_fallback(monkeypatch):
    contact = SimpleNamespace(
        public_key="ab" * 32,
        effective_route_tuple=lambda: ("", -1, -1),
    )
    direct_advert = SimpleNamespace(path="", path_len=0)

    async def get_recent(public_key, limit):
        assert (public_key, limit) == (contact.public_key, 1)
        return [direct_advert]

    monkeypatch.setattr(
        "app.services.voice.ContactAdvertPathRepository.get_recent_for_contact", get_recent
    )

    assert await _raw_route_for_contact(contact) == ("", 0, 0)


async def test_raw_media_does_not_convert_relayed_advert_to_zero_hop(monkeypatch):
    contact = SimpleNamespace(
        public_key="ab" * 32,
        effective_route_tuple=lambda: ("", -1, -1),
    )
    relayed_advert = SimpleNamespace(path="12", path_len=1)

    async def get_recent(_public_key, limit):
        assert limit == 1
        return [relayed_advert]

    monkeypatch.setattr(
        "app.services.voice.ContactAdvertPathRepository.get_recent_for_contact", get_recent
    )

    assert await _raw_route_for_contact(contact) == ("", -1, -1)


@pytest.mark.parametrize(
    "route",
    [("", -1, -1), ("01020304", 4, 0), ("0102", 1, 0), ("0102", 1, 3)],
)
def test_raw_command_rejects_flood_long_and_inconsistent_routes(route):
    class Contact:
        def effective_route_tuple(self):
            return route

    with pytest.raises(ValueError):
        _raw_frame_for_contact(Contact(), b"voice")


async def test_channel_voice_fetch_targets_resolved_original_sender(monkeypatch):
    sender_key = "ab" * 32
    sender = SimpleNamespace(public_key=sender_key)
    session = {
        "session_id": "00112233",
        "peer_public_key": sender_key,
        "packet_count": 2,
        "fragments": [],
    }
    sent = []

    async def get_by_key(public_key):
        assert public_key == sender_key
        return sender

    async def capture_direct_raw(radio_manager, contact, payload):
        sent.append((radio_manager, contact, VoiceFetchRequest.parse(payload)))

    monkeypatch.setattr("app.services.voice.ContactRepository.get_by_key", get_by_key)
    monkeypatch.setattr("app.services.voice.get_public_key", lambda: bytes.fromhex("cd" * 32))
    monkeypatch.setattr("app.services.voice.send_raw_to_contact", capture_direct_raw)

    radio_manager = object()
    await request_voice_session(radio_manager, session)

    assert sent == [
        (
            radio_manager,
            sender,
            VoiceFetchRequest("00112233", "cd" * 6, ()),
        )
    ]


async def test_channel_fetch_uses_sender_key_and_parses_only_message_body(monkeypatch):
    sender_key = "ab" * 32
    message = SimpleNamespace(
        id=42,
        type="CHAN",
        text="Alice: VE3:jbxb73:3:c:a",
        sender_name="Alice",
        sender_key=sender_key,
        conversation_key="ef" * 16,
        outgoing=False,
    )
    captured = {}
    complete_session = {
        "session_id": "45abcdef",
        "state": "complete",
        "mode": 3,
        "duration_ms": 10_000,
        "packet_count": 12,
        "fragments": [(index, b"data") for index in range(12)],
    }

    async def get_message(message_id):
        assert message_id == message.id
        return message

    async def get_session(session_id):
        assert session_id == "45abcdef"
        return None if "peer_public_key" not in captured else complete_session

    async def upsert_session(**values):
        captured.update(values)

    monkeypatch.setattr("app.routers.voice.VoiceRepository.enforce_cache_limit", _async_noop)
    monkeypatch.setattr("app.routers.voice.MessageRepository.get_by_id", get_message)
    monkeypatch.setattr("app.routers.voice.VoiceRepository.get", get_session)
    monkeypatch.setattr("app.routers.voice.VoiceRepository.upsert_session", upsert_session)

    result = await fetch_voice(message.id)

    assert captured["peer_public_key"] == sender_key
    assert captured["conversation_type"] == "CHAN"
    assert result["session_id"] == "45abcdef"


async def test_voice_fetch_reports_missing_raw_route_without_internal_server_error(monkeypatch):
    message = SimpleNamespace(
        id=43,
        type="CHAN",
        text="Alice: VE3:jbxb73:3:c:a",
        sender_name="Alice",
        sender_key="ab" * 32,
        conversation_key="ef" * 16,
        outgoing=False,
    )
    session = {
        "session_id": "45abcdef",
        "message_id": message.id,
        "state": "available",
        "mode": 3,
        "duration_ms": 10_000,
        "packet_count": 12,
        "fragments": [],
    }

    async def get_message(_message_id):
        return message

    async def get_session(_session_id):
        return session

    async def unavailable_route(_radio_manager, _session):
        raise ValueError("voice transfer requires a direct or learned route")

    monkeypatch.setattr("app.routers.voice.VoiceRepository.enforce_cache_limit", _async_noop)
    monkeypatch.setattr("app.routers.voice.VoiceRepository.upsert_session", _async_noop)
    monkeypatch.setattr("app.routers.voice.MessageRepository.get_by_id", get_message)
    monkeypatch.setattr("app.routers.voice.VoiceRepository.get", get_session)
    monkeypatch.setattr("app.routers.voice.radio_manager.require_connected", lambda: None)
    monkeypatch.setattr("app.routers.voice.request_voice_session", unavailable_route)

    with pytest.raises(HTTPException) as exc_info:
        await fetch_voice(message.id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "voice transfer requires a direct or learned route"


async def _async_noop(*_args, **_kwargs):
    return None


@pytest.mark.skipif(not codec2_available(), reason="system libcodec2 is not installed")
def test_codec2_mode1300_encode_decode_smoke():
    pcm = b"".join(struct.pack("<h", (index % 200) * 100 - 10_000) for index in range(8_000))
    with Codec2(VoiceMode.MODE_1300) as codec:
        encoded = codec.encode_pcm16le(pcm)
        decoded = codec.decode_pcm16le(encoded)
    assert len(encoded) == 175
    assert len(decoded) == len(pcm)
    assert decoded != bytes(len(decoded))
