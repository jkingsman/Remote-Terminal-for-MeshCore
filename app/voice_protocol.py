"""MeshCore SAR-compatible VE3 voice protocol primitives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

MAX_VOICE_DURATION_MS = 10_000
MAX_VOICE_PACKETS = 255
MAX_VOICE_PACKET_BYTES = 180
VOICE_PACKET_MAGIC = 0x56
VOICE_FETCH_MAGIC = 0x72
VOICE_ACK_MAGIC = 0x76


class VoiceMode(IntEnum):
    MODE_700C = 0
    MODE_1200 = 1
    MODE_2400 = 2
    MODE_1300 = 3
    MODE_1400 = 4
    MODE_1600 = 5
    MODE_3200 = 6


MODE_LABELS = {
    VoiceMode.MODE_700C: "700C",
    VoiceMode.MODE_1200: "1200",
    VoiceMode.MODE_2400: "2400",
    VoiceMode.MODE_1300: "1300",
    VoiceMode.MODE_1400: "1400",
    VoiceMode.MODE_1600: "1600",
    VoiceMode.MODE_3200: "3200",
}
MODE_BYTES_PER_SECOND = {
    VoiceMode.MODE_700C: 100,
    VoiceMode.MODE_1200: 150,
    VoiceMode.MODE_2400: 300,
    VoiceMode.MODE_1300: 175,
    VoiceMode.MODE_1400: 175,
    VoiceMode.MODE_1600: 200,
    VoiceMode.MODE_3200: 400,
}
MODE_PACKET_DURATION_MS = {
    VoiceMode.MODE_700C: 1600,
    VoiceMode.MODE_1200: 1040,
    VoiceMode.MODE_2400: 520,
    VoiceMode.MODE_1300: 880,
    VoiceMode.MODE_1400: 880,
    VoiceMode.MODE_1600: 800,
    VoiceMode.MODE_3200: 400,
}


def _base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    out = ""
    while value:
        value, digit = divmod(value, 36)
        out = alphabet[digit] + out
    return out


@dataclass(frozen=True)
class VoiceEnvelope:
    session_id: str
    mode: VoiceMode
    total: int
    duration_ms: int

    def encode(self) -> str:
        sid = _validate_session_id(self.session_id)
        if not 1 <= self.total <= MAX_VOICE_PACKETS:
            raise ValueError("voice packet count must be 1..255")
        duration_s = min(600, max(0, (self.duration_ms + 999) // 1000))
        return f"VE3:{_base36(int(sid, 16))}:{_base36(int(self.mode))}:{_base36(self.total)}:{_base36(duration_s)}"

    @classmethod
    def parse(cls, text: str) -> VoiceEnvelope | None:
        if not text.startswith("VE3:"):
            return None
        parts = text[4:].split(":")
        if len(parts) != 4 or not re.fullmatch(r"[0-9a-z]{1,7}", parts[0]):
            return None
        try:
            sid_value, mode_value, total, duration_s = (int(part, 36) for part in parts)
            mode = VoiceMode(mode_value)
        except ValueError:
            return None
        if sid_value > 0xFFFFFFFF or not 1 <= total <= MAX_VOICE_PACKETS:
            return None
        if not 0 <= duration_s <= 600:
            return None
        return cls(f"{sid_value:08x}", mode, total, duration_s * 1000)


@dataclass(frozen=True)
class VoicePacket:
    session_id: str
    index: int
    codec2_data: bytes

    def encode(self) -> bytes:
        sid = bytes.fromhex(_validate_session_id(self.session_id))
        if not 0 <= self.index < MAX_VOICE_PACKETS:
            raise ValueError("voice packet index must be 0..254")
        if not self.codec2_data or len(self.codec2_data) > MAX_VOICE_PACKET_BYTES - 6:
            raise ValueError("invalid Codec2 fragment size")
        return bytes([VOICE_PACKET_MAGIC]) + sid + bytes([self.index]) + self.codec2_data

    @classmethod
    def parse(cls, payload: bytes) -> VoicePacket | None:
        if not 7 <= len(payload) <= MAX_VOICE_PACKET_BYTES or payload[0] != VOICE_PACKET_MAGIC:
            return None
        return cls(payload[1:5].hex(), payload[5], payload[6:])


@dataclass(frozen=True)
class VoiceFetchRequest:
    session_id: str
    requester_key6: str
    missing_indices: tuple[int, ...] = ()

    def encode(self) -> bytes:
        sid = bytes.fromhex(_validate_session_id(self.session_id))
        if not re.fullmatch(r"[0-9a-fA-F]{12}", self.requester_key6):
            raise ValueError("requester key must be 12 hex characters")
        missing = tuple(sorted(set(self.missing_indices)))
        if len(missing) > MAX_VOICE_PACKETS or any(not 0 <= i < MAX_VOICE_PACKETS for i in missing):
            raise ValueError("invalid missing voice indices")
        flags = 1 if missing else 0
        return (
            bytes([VOICE_FETCH_MAGIC])
            + sid
            + bytes([flags])
            + bytes.fromhex(self.requester_key6)
            + bytes([len(missing)])
            + bytes(missing)
        )

    @classmethod
    def parse(cls, payload: bytes) -> VoiceFetchRequest | None:
        if len(payload) < 13 or payload[0] != VOICE_FETCH_MAGIC:
            return None
        count = payload[12]
        if len(payload) != 13 + count:
            return None
        flags = payload[5]
        if flags & ~1:
            return None
        missing = tuple(payload[13:]) if flags & 1 else ()
        if len(set(missing)) != len(missing):
            return None
        return cls(payload[1:5].hex(), payload[6:12].hex(), missing)


def encode_fragment_ack(session_id: str, index: int) -> bytes:
    if not 0 <= index < MAX_VOICE_PACKETS:
        raise ValueError("voice packet index must be 0..254")
    return (
        bytes([VOICE_ACK_MAGIC]) + bytes.fromhex(_validate_session_id(session_id)) + bytes([index])
    )


def parse_fragment_ack(payload: bytes) -> tuple[str, int] | None:
    if len(payload) != 6 or payload[0] != VOICE_ACK_MAGIC:
        return None
    return payload[1:5].hex(), payload[5]


def fragment_codec2(session_id: str, encoded: bytes, mode: VoiceMode) -> list[VoicePacket]:
    bytes_per_packet = MODE_BYTES_PER_SECOND[mode] * MODE_PACKET_DURATION_MS[mode] // 1000
    packets = [
        VoicePacket(session_id, index, encoded[offset : offset + bytes_per_packet])
        for index, offset in enumerate(range(0, len(encoded), bytes_per_packet))
    ]
    if not packets or len(packets) > MAX_VOICE_PACKETS:
        raise ValueError("encoded voice packet count is out of bounds")
    return packets


def _validate_session_id(session_id: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{8}", session_id):
        raise ValueError("voice session id must be 8 hex characters")
    return session_id.lower()
