import hashlib
import hmac
import struct
from dataclasses import dataclass
from typing import Optional

from .base91 import encode as b91_encode, decode as b91_decode
from .compressor import MeshCompressor

SUBTYPE_ID = 0x02
FORMAT_VERSION = 3
WIRE_VERSION = 0x00
SUBTYPE_VERSION = (SUBTYPE_ID << 4) | WIRE_VERSION
TEXT_PREFIX = 'mcmp3:'
SIGNING_DOMAIN = b'MCOAPP:MCMP:SIGNED:v3'
BINDING_DOMAIN = b'MCOAPP:MCMP:BIND:v3'
SIGNING_BINDING_SIZE = 32
SIGNATURE_SIZE = 64  # Ed25519

_FLAG_REPLY = 1 << 0
_FLAG_SIGNED = 1 << 1
_FLAG_SENDER_NAME = 1 << 2
_KNOWN_FLAGS = _FLAG_REPLY | _FLAG_SIGNED | _FLAG_SENDER_NAME


class _ByteWriter:
    __slots__ = ('_bytes',)

    def __init__(self):
        self._bytes = bytearray()

    def write_byte(self, value: int) -> None:
        if not 0 <= value <= 0xff:
            raise ValueError(f"Byte value out of range: {value}")
        self._bytes.append(value)

    def write_bytes(self, values: bytes) -> None:
        self._bytes.extend(values)

    def write_uint32_le(self, value: int) -> None:
        if not 0 <= value <= 0xffffffff:
            raise ValueError(f"Uint32 value out of range: {value}")
        self._bytes.extend(struct.pack('<I', value))

    def write_varuint(self, value: int) -> None:
        if value < 0:
            raise ValueError("varuint cannot be negative")
        remaining = value
        while True:
            byte = remaining & 0x7f
            remaining >>= 7
            if remaining:
                byte |= 0x80
            self._bytes.append(byte)
            if not remaining:
                break

    def to_bytes(self) -> bytes:
        return bytes(self._bytes)


class _ByteReader:
    __slots__ = ('_data', '_offset')

    def __init__(self, data: bytes):
        self._data = data
        self._offset = 0

    def read_byte(self) -> int:
        if self._offset >= len(self._data):
            raise ValueError("EOF")
        value = self._data[self._offset]
        self._offset += 1
        return value

    def read_bytes(self, length: int) -> bytes:
        if length < 0 or self._offset + length > len(self._data):
            raise ValueError("EOF")
        value = self._data[self._offset:self._offset + length]
        self._offset += length
        return value

    def read_uint32_le(self) -> int:
        return struct.unpack('<I', self.read_bytes(4))[0]

    def read_varuint(self) -> int:
        result = 0
        shift = 0
        while True:
            byte = self.read_byte()
            result |= (byte & 0x7f) << shift
            if not byte & 0x80:
                return result
            shift += 7
            if shift > 28:
                raise ValueError("varuint too long")

    def read_remaining_bytes(self) -> bytes:
        return self.read_bytes(len(self._data) - self._offset)


@dataclass
class EncodedMcmpAppMessage:
    body: bytes
    timestamp: int
    is_signed: bool
    is_reply: bool
    sender_name: Optional[str] = None
    reply_author_name: Optional[str] = None
    reply_timestamp: Optional[int] = None


@dataclass
class DecodedMcmpAppMessage:
    text: str
    timestamp: int
    sender_name: Optional[str] = None
    signature: Optional[bytes] = None
    signature_status: str = "unsigned"  # "none" | "unsigned" | "valid" | "invalid" | ...
    reply_author_name: Optional[str] = None
    reply_timestamp: Optional[int] = None

    @property
    def is_signed(self) -> bool:
        return self.signature is not None

    @property
    def is_reply(self) -> bool:
        return self.reply_author_name is not None and self.reply_timestamp is not None


class McmpAppCodec:
    """MCMP v3 application-layer codec.

    Mirrors ``lib/helpers/mcmp_app_codec.dart`` from the MeshCore Open advanced client.
    """

    SUBTYPE_ID = SUBTYPE_ID
    FORMAT_VERSION = FORMAT_VERSION
    WIRE_VERSION = WIRE_VERSION
    SUBTYPE_VERSION = SUBTYPE_VERSION
    TEXT_PREFIX = TEXT_PREFIX
    SIGNING_DOMAIN = SIGNING_DOMAIN
    BINDING_DOMAIN = BINDING_DOMAIN
    SIGNING_BINDING_SIZE = SIGNING_BINDING_SIZE
    SIGNATURE_SIZE = SIGNATURE_SIZE

    # ------------------------------------------------------------------
    # Flag packing
    # ------------------------------------------------------------------
    @staticmethod
    def pack_flags(*, has_reply: bool, is_signed: bool, has_sender_name: bool) -> int:
        flags = 0
        if has_reply:
            flags |= _FLAG_REPLY
        if is_signed:
            flags |= _FLAG_SIGNED
        if has_sender_name:
            flags |= _FLAG_SENDER_NAME
        return flags

    # ------------------------------------------------------------------
    # Signing bindings
    # ------------------------------------------------------------------
    @staticmethod
    def channel_signing_binding(psk: bytes) -> bytes:
        """Signing binding for channel messages: HMAC-SHA256 of the binding domain keyed with the 16-byte channel PSK."""
        return hmac.new(psk, BINDING_DOMAIN, hashlib.sha256).digest()

    @staticmethod
    def room_signing_binding(room_public_key: bytes) -> bytes:
        """Signing binding for room-server messages: the room's 32-byte public key."""
        if len(room_public_key) != SIGNING_BINDING_SIZE:
            raise ValueError(f"Room binding requires a {SIGNING_BINDING_SIZE}-byte public key")
        return room_public_key

    # ------------------------------------------------------------------
    # Body encode/decode
    # ------------------------------------------------------------------
    @staticmethod
    def encode_body(
        *,
        text: str,
        timestamp: int,
        sender_name: Optional[str] = None,
        signature: Optional[bytes] = None,
        reply_author_name: Optional[str] = None,
        reply_timestamp: Optional[int] = None,
    ) -> EncodedMcmpAppMessage:
        if not 0 <= timestamp <= 0xffffffff:
            raise ValueError("timestamp out of range")
        if (reply_author_name is None) != (reply_timestamp is None):
            raise ValueError("reply_author_name and reply_timestamp must be provided together")
        if reply_timestamp is not None and not 0 <= reply_timestamp <= 0xffffffff:
            raise ValueError("reply_timestamp out of range")
        if signature is not None and len(signature) != SIGNATURE_SIZE:
            raise ValueError(f"MCMP app signatures must be {SIGNATURE_SIZE} bytes")

        flags = McmpAppCodec.pack_flags(
            has_reply=reply_author_name is not None,
            is_signed=signature is not None,
            has_sender_name=sender_name is not None,
        )

        # Always compress using the shared MeshCompressor instance
        compressed = MeshCompressor.instance.compress_to_bytes(text)

        writer = _ByteWriter()
        writer.write_byte(flags)
        writer.write_uint32_le(timestamp)

        if sender_name is not None:
            sender_name_bytes = sender_name.encode('utf-8')
            writer.write_varuint(len(sender_name_bytes))
            writer.write_bytes(sender_name_bytes)

        if signature is not None:
            writer.write_bytes(signature)

        if reply_author_name is not None and reply_timestamp is not None:
            reply_name_bytes = reply_author_name.encode('utf-8')
            writer.write_varuint(len(reply_name_bytes))
            writer.write_bytes(reply_name_bytes)
            writer.write_uint32_le(reply_timestamp)

        writer.write_bytes(compressed)

        return EncodedMcmpAppMessage(
            body=writer.to_bytes(),
            timestamp=timestamp,
            is_signed=signature is not None,
            is_reply=reply_author_name is not None,
            sender_name=sender_name,
            reply_author_name=reply_author_name,
            reply_timestamp=reply_timestamp,
        )

    @staticmethod
    def decode_body(body: bytes) -> DecodedMcmpAppMessage:
        reader = _ByteReader(body)
        flags = reader.read_byte()
        if flags & ~_KNOWN_FLAGS:
            raise ValueError("Unsupported MCMP app flags")

        timestamp = reader.read_uint32_le()

        sender_name: Optional[str] = None
        if flags & _FLAG_SENDER_NAME:
            sender_name_len = reader.read_varuint()
            sender_name = reader.read_bytes(sender_name_len).decode('utf-8')

        signature: Optional[bytes] = None
        if flags & _FLAG_SIGNED:
            signature = reader.read_bytes(SIGNATURE_SIZE)

        reply_author_name: Optional[str] = None
        reply_timestamp: Optional[int] = None
        if flags & _FLAG_REPLY:
            reply_name_len = reader.read_varuint()
            reply_author_name = reader.read_bytes(reply_name_len).decode('utf-8')
            reply_timestamp = reader.read_uint32_le()

        compressed = reader.read_remaining_bytes()
        text = MeshCompressor.instance.decompress_bytes(compressed)

        return DecodedMcmpAppMessage(
            text=text,
            timestamp=timestamp,
            sender_name=sender_name,
            signature=signature,
            signature_status="invalid" if signature is not None else "unsigned",
            reply_author_name=reply_author_name,
            reply_timestamp=reply_timestamp,
        )

    # ------------------------------------------------------------------
    # Text transport helpers
    # ------------------------------------------------------------------
    @staticmethod
    def text_from_body(body: bytes) -> str:
        return TEXT_PREFIX + b91_encode(body)

    @staticmethod
    def body_from_text(text: str) -> bytes:
        trimmed = text.lstrip()
        if not trimmed.startswith(TEXT_PREFIX) or len(trimmed) <= len(TEXT_PREFIX):
            raise ValueError("Missing MCMP app text prefix")
        return b91_decode(trimmed[len(TEXT_PREFIX):])

    @staticmethod
    def is_text_payload(text: str) -> bool:
        trimmed = text.lstrip()
        return trimmed.startswith(TEXT_PREFIX) and len(trimmed) > len(TEXT_PREFIX)

    @staticmethod
    def try_decode_text_payload_message(text: str) -> Optional[DecodedMcmpAppMessage]:
        if not McmpAppCodec.is_text_payload(text):
            return None
        try:
            body = McmpAppCodec.body_from_text(text)
            return McmpAppCodec.decode_body(body)
        except Exception:
            return None

    @staticmethod
    def encode_text_transport(
        *,
        text: str,
        timestamp: int,
        sender_name: Optional[str] = None,
        signature: Optional[bytes] = None,
        reply_author_name: Optional[str] = None,
        reply_timestamp: Optional[int] = None,
    ) -> str:
        """Encode text into the mcmp3: text transport.

        Returns the original text if it is empty/already-encoded, or if encoding fails.
        """
        if text == '' or McmpAppCodec.is_text_payload(text):
            return text
        try:
            encoded = McmpAppCodec.encode_body(
                text=text,
                timestamp=timestamp,
                sender_name=sender_name,
                signature=signature,
                reply_author_name=reply_author_name,
                reply_timestamp=reply_timestamp,
            )
            return McmpAppCodec.text_from_body(encoded.body)
        except Exception:
            return text

    # ------------------------------------------------------------------
    # Canonical signing bytes
    # ------------------------------------------------------------------
    @staticmethod
    def canonical_signing_bytes(
        *,
        context_id: int,
        binding: bytes,
        sender_name: str,
        timestamp: int,
        flags: int,
        text: str,
        reply_author_name: Optional[str] = None,
        reply_timestamp: Optional[int] = None,
    ) -> bytes:
        """Build canonical bytes covered by the Ed25519 signature."""
        if len(binding) != SIGNING_BINDING_SIZE:
            raise ValueError(f"Signing binding must be {SIGNING_BINDING_SIZE} bytes")
        if (reply_author_name is None) != (reply_timestamp is None):
            raise ValueError("reply_author_name and reply_timestamp must be provided together")
        if bool(flags & _FLAG_REPLY) != (reply_author_name is not None):
            raise ValueError("flags reply bit conflicts with reply arguments")
        if flags & ~_KNOWN_FLAGS:
            raise ValueError("Unknown flag bits")

        sender_name_bytes = sender_name.encode('utf-8')
        text_bytes = text.encode('utf-8')

        writer = _ByteWriter()
        writer.write_bytes(SIGNING_DOMAIN)
        writer.write_byte(context_id)
        writer.write_bytes(binding)
        writer.write_varuint(len(sender_name_bytes))
        writer.write_bytes(sender_name_bytes)
        writer.write_uint32_le(timestamp)
        writer.write_byte(flags)
        if reply_author_name is not None and reply_timestamp is not None:
            reply_name_bytes = reply_author_name.encode('utf-8')
            writer.write_varuint(len(reply_name_bytes))
            writer.write_bytes(reply_name_bytes)
            writer.write_uint32_le(reply_timestamp)
        writer.write_bytes(text_bytes)

        return writer.to_bytes()


# Convenience module-level aliases so existing imports can use either style.
pack_flags = McmpAppCodec.pack_flags
channel_signing_binding = McmpAppCodec.channel_signing_binding
room_signing_binding = McmpAppCodec.room_signing_binding
encode_body = McmpAppCodec.encode_body
decode_body = McmpAppCodec.decode_body
text_from_body = McmpAppCodec.text_from_body
body_from_text = McmpAppCodec.body_from_text
is_text_payload = McmpAppCodec.is_text_payload
try_decode_text_payload_message = McmpAppCodec.try_decode_text_payload_message
encode_text_transport = McmpAppCodec.encode_text_transport
canonical_signing_bytes = McmpAppCodec.canonical_signing_bytes