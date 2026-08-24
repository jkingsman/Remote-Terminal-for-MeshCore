"""MeshCore SAR-compatible IE4 image protocol primitives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

MAX_IMAGE_FRAGMENTS = 255
MAX_IMAGE_FRAGMENT_BYTES = 152
MAX_ENCODED_IMAGE_BYTES = MAX_IMAGE_FRAGMENTS * MAX_IMAGE_FRAGMENT_BYTES
IMAGE_PACKET_MAGIC = 0x49
IMAGE_FETCH_MAGIC = 0x69


class ImageFormat(IntEnum):
    AVIF = 0
    JPEG = 1


def _base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    out = ""
    while value:
        value, digit = divmod(value, 36)
        out = alphabet[digit] + out
    return out


def _session_id(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{8}", value):
        raise ValueError("image session ID must be 8 hex characters")
    return value.lower()


@dataclass(frozen=True)
class ImageEnvelope:
    session_id: str
    format: ImageFormat
    total: int
    width: int
    height: int
    size_bytes: int

    def validate(self) -> None:
        _session_id(self.session_id)
        if not 1 <= self.total <= MAX_IMAGE_FRAGMENTS:
            raise ValueError("image fragment count must be 1..255")
        if not 1 <= self.width <= 256 or not 1 <= self.height <= 256:
            raise ValueError("image dimensions must be 1..256 pixels")
        if not 1 <= self.size_bytes <= MAX_ENCODED_IMAGE_BYTES:
            raise ValueError("encoded image size is out of bounds")
        expected_total = (self.size_bytes + MAX_IMAGE_FRAGMENT_BYTES - 1) // MAX_IMAGE_FRAGMENT_BYTES
        if self.total != expected_total:
            raise ValueError("image size and fragment count do not agree")

    def encode(self) -> str:
        self.validate()
        return (
            f"IE4:{_base36(int(self.session_id, 16))}:{_base36(int(self.format))}:"
            f"{_base36(self.total)}:{_base36(self.width)}:{_base36(self.height)}:"
            f"{_base36(self.size_bytes)}"
        )

    @classmethod
    def parse(cls, text: str) -> ImageEnvelope | None:
        if not text.startswith("IE4:"):
            return None
        parts = text[4:].split(":")
        if len(parts) != 6 or not re.fullmatch(r"[0-9a-z]{1,7}", parts[0]):
            return None
        try:
            sid, format_id, total, width, height, size_bytes = (
                int(part, 36) for part in parts
            )
            envelope = cls(
                f"{sid:08x}", ImageFormat(format_id), total, width, height, size_bytes
            )
            if sid > 0xFFFFFFFF:
                return None
            envelope.validate()
        except ValueError:
            return None
        return envelope


@dataclass(frozen=True)
class ImagePacket:
    session_id: str
    index: int
    data: bytes

    def encode(self) -> bytes:
        sid = bytes.fromhex(_session_id(self.session_id))
        if not 0 <= self.index < MAX_IMAGE_FRAGMENTS:
            raise ValueError("image fragment index must be 0..254")
        if not 1 <= len(self.data) <= MAX_IMAGE_FRAGMENT_BYTES:
            raise ValueError("image fragment payload must be 1..152 bytes")
        return bytes([IMAGE_PACKET_MAGIC]) + sid + bytes([self.index]) + self.data

    @classmethod
    def parse(cls, payload: bytes) -> ImagePacket | None:
        if not 7 <= len(payload) <= 6 + MAX_IMAGE_FRAGMENT_BYTES:
            return None
        if payload[0] != IMAGE_PACKET_MAGIC:
            return None
        return cls(payload[1:5].hex(), payload[5], payload[6:])


@dataclass(frozen=True)
class ImageFetchRequest:
    session_id: str
    requester_key6: str
    missing_indices: tuple[int, ...] = ()

    def encode(self) -> bytes:
        sid = bytes.fromhex(_session_id(self.session_id))
        if not re.fullmatch(r"[0-9a-fA-F]{12}", self.requester_key6):
            raise ValueError("requester key must be 12 hex characters")
        missing = tuple(sorted(set(self.missing_indices)))
        if len(missing) > MAX_IMAGE_FRAGMENTS or any(
            not 0 <= index < MAX_IMAGE_FRAGMENTS for index in missing
        ):
            raise ValueError("invalid missing image indices")
        return (
            bytes([IMAGE_FETCH_MAGIC])
            + sid
            + bytes([1 if missing else 0])
            + bytes.fromhex(self.requester_key6)
            + bytes([len(missing)])
            + bytes(missing)
        )

    @classmethod
    def parse(cls, payload: bytes) -> ImageFetchRequest | None:
        if len(payload) < 13 or payload[0] != IMAGE_FETCH_MAGIC:
            return None
        count = payload[12]
        if len(payload) != 13 + count or payload[5] & ~1:
            return None
        if not payload[5] and count:
            return None
        missing = tuple(payload[13:]) if payload[5] & 1 else ()
        if len(set(missing)) != len(missing):
            return None
        return cls(payload[1:5].hex(), payload[6:12].hex(), missing)


def fragment_image(session_id: str, encoded: bytes) -> list[ImagePacket]:
    if not 1 <= len(encoded) <= MAX_ENCODED_IMAGE_BYTES:
        raise ValueError("encoded image size is out of bounds")
    packets = [
        ImagePacket(session_id, index, encoded[offset : offset + MAX_IMAGE_FRAGMENT_BYTES])
        for index, offset in enumerate(range(0, len(encoded), MAX_IMAGE_FRAGMENT_BYTES))
    ]
    if len(packets) > MAX_IMAGE_FRAGMENTS:
        raise ValueError("encoded image requires more than 255 fragments")
    return packets


def reassemble_image(packets: list[ImagePacket], total: int) -> bytes | None:
    by_index = {packet.index: packet for packet in packets}
    if len(by_index) != total or any(index not in by_index for index in range(total)):
        return None
    return b"".join(by_index[index].data for index in range(total))
