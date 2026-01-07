"""
MeshCore packet decoder for historical packet decryption.
Based on https://github.com/michaelhart/meshcore-decoder
"""

import hmac
import hashlib
import logging
from dataclasses import dataclass
from enum import IntEnum

from Crypto.Cipher import AES

logger = logging.getLogger(__name__)


class PayloadType(IntEnum):
    REQUEST = 0x00
    RESPONSE = 0x01
    TEXT_MESSAGE = 0x02
    ACK = 0x03
    ADVERT = 0x04
    GROUP_TEXT = 0x05
    GROUP_DATA = 0x06
    ANON_REQUEST = 0x07
    PATH = 0x08
    TRACE = 0x09
    MULTIPART = 0x0A
    CONTROL = 0x0B
    RAW_CUSTOM = 0x0F


class RouteType(IntEnum):
    TRANSPORT_FLOOD = 0x00
    FLOOD = 0x01
    DIRECT = 0x02
    TRANSPORT_DIRECT = 0x03


@dataclass
class DecryptedGroupText:
    """Result of decrypting a GroupText (channel) message."""

    timestamp: int
    flags: int
    sender: str | None
    message: str
    channel_hash: str


@dataclass
class ParsedAdvertisement:
    """Result of parsing an advertisement packet."""

    public_key: str  # 64-char hex
    name: str | None
    lat: float | None
    lon: float | None


@dataclass
class PacketInfo:
    """Basic packet header info."""

    route_type: RouteType
    payload_type: PayloadType
    payload_version: int
    path_length: int
    payload: bytes


def calculate_channel_hash(channel_key: bytes) -> str:
    """
    Calculate the channel hash from a 16-byte channel key.
    Returns the first byte of SHA256(key) as hex.
    """
    hash_bytes = hashlib.sha256(channel_key).digest()
    return format(hash_bytes[0], "02x")


def extract_payload(raw_packet: bytes) -> bytes | None:
    """
    Extract just the payload from a raw packet, skipping header and path.

    Packet structure:
    - Byte 0: header (route_type, payload_type, version)
    - For TRANSPORT routes: bytes 1-4 are transport codes
    - Next byte: path_length
    - Next path_length bytes: path data
    - Remaining: payload

    Returns the payload bytes, or None if packet is malformed.
    """
    if len(raw_packet) < 2:
        return None

    try:
        header = raw_packet[0]
        route_type = header & 0x03
        offset = 1

        # Skip transport codes if present (TRANSPORT_FLOOD=0, TRANSPORT_DIRECT=3)
        if route_type in (0x00, 0x03):
            if len(raw_packet) < offset + 4:
                return None
            offset += 4

        # Get path length
        if len(raw_packet) < offset + 1:
            return None
        path_length = raw_packet[offset]
        offset += 1

        # Skip path data
        if len(raw_packet) < offset + path_length:
            return None
        offset += path_length

        # Rest is payload
        return raw_packet[offset:]
    except (ValueError, IndexError):
        return None


def parse_packet(raw_packet: bytes) -> PacketInfo | None:
    """Parse a raw packet and extract basic info."""
    if len(raw_packet) < 2:
        return None

    try:
        header = raw_packet[0]
        route_type = RouteType(header & 0x03)
        payload_type = PayloadType((header >> 2) & 0x0F)
        payload_version = (header >> 6) & 0x03

        offset = 1

        # Skip transport codes if present
        if route_type in (RouteType.TRANSPORT_FLOOD, RouteType.TRANSPORT_DIRECT):
            if len(raw_packet) < offset + 4:
                return None
            offset += 4

        # Get path length
        if len(raw_packet) < offset + 1:
            return None
        path_length = raw_packet[offset]
        offset += 1

        # Skip path data
        if len(raw_packet) < offset + path_length:
            return None
        offset += path_length

        # Rest is payload
        payload = raw_packet[offset:]

        return PacketInfo(
            route_type=route_type,
            payload_type=payload_type,
            payload_version=payload_version,
            path_length=path_length,
            payload=payload,
        )
    except (ValueError, IndexError):
        return None


def decrypt_group_text(
    payload: bytes, channel_key: bytes
) -> DecryptedGroupText | None:
    """
    Decrypt a GroupText payload using the channel key.

    GroupText structure:
    - channel_hash (1 byte): First byte of SHA256 of channel key
    - cipher_mac (2 bytes): First 2 bytes of HMAC-SHA256
    - ciphertext (rest): AES-128 ECB encrypted content

    Decrypted content structure:
    - timestamp (4 bytes, little-endian)
    - flags (1 byte)
    - message text (null-terminated string, format: "sender: message")
    """
    if len(payload) < 3:
        return None

    channel_hash = format(payload[0], "02x")
    cipher_mac = payload[1:3]
    ciphertext = payload[3:]

    if len(ciphertext) == 0 or len(ciphertext) % 16 != 0:
        # AES requires 16-byte blocks
        return None

    # Create the 32-byte channel secret (key + 16 zero bytes)
    channel_secret = channel_key + bytes(16)

    # Verify MAC: HMAC-SHA256 of ciphertext using full 32-byte secret
    calculated_mac = hmac.new(channel_secret, ciphertext, hashlib.sha256).digest()
    if calculated_mac[:2] != cipher_mac:
        return None

    # Decrypt using AES-128 ECB with the 16-byte key
    try:
        cipher = AES.new(channel_key, AES.MODE_ECB)
        decrypted = cipher.decrypt(ciphertext)
    except Exception as e:
        logger.debug("AES decryption failed: %s", e)
        return None

    if len(decrypted) < 5:
        return None

    # Parse decrypted content
    timestamp = int.from_bytes(decrypted[0:4], "little")
    flags = decrypted[4]

    # Extract message text (UTF-8, null-terminated)
    message_bytes = decrypted[5:]
    try:
        message_text = message_bytes.decode("utf-8")
        # Remove null terminator and any padding
        null_idx = message_text.find("\x00")
        if null_idx >= 0:
            message_text = message_text[:null_idx]
    except UnicodeDecodeError:
        return None

    # Parse "sender: message" format
    sender = None
    content = message_text
    colon_idx = message_text.find(": ")
    if 0 < colon_idx < 50:
        potential_sender = message_text[:colon_idx]
        # Check for invalid characters in sender name
        if not any(c in potential_sender for c in ":[]\x00"):
            sender = potential_sender
            content = message_text[colon_idx + 2 :]

    return DecryptedGroupText(
        timestamp=timestamp,
        flags=flags,
        sender=sender,
        message=content,
        channel_hash=channel_hash,
    )


def try_decrypt_packet_with_channel_key(
    raw_packet: bytes, channel_key: bytes
) -> DecryptedGroupText | None:
    """
    Try to decrypt a raw packet using a channel key.
    Returns decrypted content if successful, None otherwise.
    """
    packet_info = parse_packet(raw_packet)
    if packet_info is None:
        return None

    # Only GroupText packets can be decrypted with channel keys
    if packet_info.payload_type != PayloadType.GROUP_TEXT:
        return None

    # Check if channel hash matches
    if len(packet_info.payload) < 1:
        return None

    packet_channel_hash = format(packet_info.payload[0], "02x")
    expected_hash = calculate_channel_hash(channel_key)

    if packet_channel_hash != expected_hash:
        return None

    return decrypt_group_text(packet_info.payload, channel_key)


def get_packet_payload_type(raw_packet: bytes) -> PayloadType | None:
    """Get the payload type of a raw packet without full parsing."""
    if len(raw_packet) < 1:
        return None
    header = raw_packet[0]
    try:
        return PayloadType((header >> 2) & 0x0F)
    except ValueError:
        return None


def parse_advertisement(payload: bytes) -> ParsedAdvertisement | None:
    """
    Parse an advertisement payload.

    Advertisement structure:
    - public_key (32 bytes): Ed25519 public key
    - signature (64 bytes): Ed25519 signature
    - advert_data (variable): Contains name and possibly lat/lon

    The name is typically at the end of the payload as a UTF-8 string.
    """
    # Minimum: 32 (pubkey) + 64 (sig) + at least 1 byte for flags/data
    if len(payload) < 97:
        return None

    public_key = payload[:32].hex()
    # signature = payload[32:96]  # Not currently verified
    advert_data = payload[96:]

    if len(advert_data) == 0:
        return ParsedAdvertisement(
            public_key=public_key,
            name=None,
            lat=None,
            lon=None,
        )

    # Try to extract name from the advert data
    # The structure varies, but the name is typically near the end
    name = None
    lat = None
    lon = None

    # Try to decode the entire advert_data as UTF-8 to find the name
    # Names are typically at the end after any binary data
    try:
        # Find the last valid UTF-8 string
        for start in range(len(advert_data)):
            try:
                text = advert_data[start:].decode("utf-8")
                # Filter out control characters and check if it looks like a name
                null_idx = text.find("\x00")
                if null_idx >= 0:
                    text = text[:null_idx]
                text = text.strip()
                if text and len(text) >= 1 and len(text) <= 40:
                    # Check if it contains printable characters
                    if any(c.isalnum() for c in text):
                        name = text
                        break
            except UnicodeDecodeError:
                continue
    except Exception:
        pass

    return ParsedAdvertisement(
        public_key=public_key,
        name=name,
        lat=lat,
        lon=lon,
    )


def try_parse_advertisement(raw_packet: bytes) -> ParsedAdvertisement | None:
    """
    Try to parse a raw packet as an advertisement.
    Returns parsed advertisement if successful, None otherwise.
    """
    packet_info = parse_packet(raw_packet)
    if packet_info is None:
        return None

    if packet_info.payload_type != PayloadType.ADVERT:
        return None

    return parse_advertisement(packet_info.payload)
