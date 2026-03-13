"""
MeshCore packet decoder for historical packet decryption.
Based on https://github.com/michaelhart/meshcore-decoder
"""

import hashlib
import hmac
import logging
from dataclasses import dataclass
from enum import IntEnum

import nacl.bindings
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
class DecryptedDirectMessage:
    """Result of decrypting a TEXT_MESSAGE (direct message)."""

    timestamp: int
    flags: int
    message: str
    dest_hash: str  # First byte of destination pubkey as hex
    src_hash: str  # First byte of sender pubkey as hex


@dataclass
class ParsedAdvertisement:
    """Result of parsing an advertisement packet."""

    public_key: str  # 64-char hex
    timestamp: int  # Unix timestamp from the advertisement
    name: str | None
    lat: float | None
    lon: float | None
    device_role: int  # 1=Chat, 2=Repeater, 3=Room, 4=Sensor


@dataclass
class PacketInfo:
    """Basic packet header info."""

    route_type: RouteType
    payload_type: PayloadType
    payload_version: int
    path_length: int  # Decoded hop count (not the raw wire byte)
    path: bytes  # The routing path bytes (empty if path_length is 0)
    payload: bytes
    path_hash_size: int = 1  # Bytes per hop: 1, 2, or 3


def extract_payload(raw_packet: bytes) -> bytes | None:
    """
    Extract just the payload from a raw packet, skipping header and path.

    Packet structure:
    - Byte 0: header (route_type, payload_type, version)
    - For TRANSPORT routes: bytes 1-4 are transport codes
    - Next byte: path byte (packed as [hash_mode:2][hop_count:6])
    - Next hop_count * hash_size bytes: path data
    - Remaining: payload

    Returns the payload bytes, or None if packet is malformed.
    """
    from app.path_utils import parse_packet_envelope

    envelope = parse_packet_envelope(raw_packet)
    return envelope.payload if envelope is not None else None


def parse_packet(raw_packet: bytes) -> PacketInfo | None:
    """Parse a raw packet and extract basic info."""
    from app.path_utils import parse_packet_envelope

    envelope = parse_packet_envelope(raw_packet)
    if envelope is None:
        return None
    try:
        return PacketInfo(
            route_type=RouteType(envelope.route_type),
            payload_type=PayloadType(envelope.payload_type),
            payload_version=envelope.payload_version,
            path_length=envelope.hop_count,
            path_hash_size=envelope.hash_size,
            path=envelope.path,
            payload=envelope.payload,
        )
    except ValueError:
        return None


def decrypt_group_text(payload: bytes, channel_key: bytes) -> DecryptedGroupText | None:
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
    expected_hash = format(hashlib.sha256(channel_key).digest()[0], "02x")

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

    Advertisement payload structure (101+ bytes):
    - Bytes 0-31 (32 bytes): Public Key (Ed25519)
    - Bytes 32-35 (4 bytes): Timestamp (Unix timestamp, little-endian)
    - Bytes 36-99 (64 bytes): Signature (Ed25519)
    - Byte 100 (1 byte): App Flags
      - Bits 0-3: Device Role (1=Chat, 2=Repeater, 3=Room, 4=Sensor)
      - Bit 4 (0x10): HasLocation
      - Bit 5 (0x20): HasFeature1
      - Bit 6 (0x40): HasFeature2
      - Bit 7 (0x80): HasName
    - If HasLocation: 8 bytes (4 lat + 4 lon as signed int32 LE / 1e6)
    - If HasFeature1: 2 bytes (skipped)
    - If HasFeature2: 2 bytes (skipped)
    - If HasName: remaining bytes = name (UTF-8)
    """
    # Minimum: pubkey(32) + timestamp(4) + sig(64) + flags(1) = 101 bytes
    if len(payload) < 101:
        return None

    # Parse fixed-position fields
    public_key = payload[0:32].hex()
    timestamp = int.from_bytes(payload[32:36], byteorder="little")
    flags = payload[100]

    # Parse flags
    device_role = flags & 0x0F
    has_location = bool(flags & 0x10)
    has_feature1 = bool(flags & 0x20)
    has_feature2 = bool(flags & 0x40)
    has_name = bool(flags & 0x80)

    # Start parsing variable-length app data after flags
    offset = 101
    lat = None
    lon = None
    name = None

    # Parse location if present (8 bytes: 4 lat + 4 lon)
    if has_location:
        if len(payload) < offset + 8:
            return ParsedAdvertisement(
                public_key=public_key,
                timestamp=timestamp,
                name=None,
                lat=None,
                lon=None,
                device_role=device_role,
            )
        lat_raw = int.from_bytes(payload[offset : offset + 4], byteorder="little", signed=True)
        lon_raw = int.from_bytes(payload[offset + 4 : offset + 8], byteorder="little", signed=True)
        lat = lat_raw / 1_000_000
        lon = lon_raw / 1_000_000
        offset += 8

    # Skip feature fields if present
    if has_feature1:
        offset += 2
    if has_feature2:
        offset += 2

    # Parse name if present (remaining bytes)
    if has_name and len(payload) > offset:
        name_bytes = payload[offset:]
        try:
            # Decode name, strip null bytes and control characters
            name = name_bytes.decode("utf-8", errors="ignore")
            # Remove null terminator and anything after
            null_idx = name.find("\x00")
            if null_idx >= 0:
                name = name[:null_idx]
            # Strip control characters and whitespace
            name = "".join(c for c in name if c >= " " or c in "\t").strip()
            if not name or not any(c.isalnum() for c in name):
                name = None
        except Exception:
            name = None

    return ParsedAdvertisement(
        public_key=public_key,
        timestamp=timestamp,
        name=name,
        lat=lat,
        lon=lon,
        device_role=device_role,
    )


# =============================================================================
# Direct Message (TEXT_MESSAGE) Decryption
# =============================================================================


def _clamp_scalar(k: bytes) -> bytes:
    """
    Clamp a 32-byte scalar for X25519.

    This applies the standard X25519 clamping to ensure the scalar
    is in the correct form for elliptic curve operations.

    Note: MeshCore private keys are already clamped (they store the post-SHA-512
    scalar directly rather than a seed). Clamping is idempotent, so this is safe.
    """
    clamped = bytearray(k[:32])
    clamped[0] &= 248
    clamped[31] &= 63
    clamped[31] |= 64
    return bytes(clamped)


def derive_public_key(private_key: bytes) -> bytes:
    """
    Derive the Ed25519 public key from a MeshCore private key.

    **MeshCore Key Format:**
    MeshCore stores a non-standard Ed25519 private key format:
    - First 32 bytes: The scalar (already post-SHA-512 and clamped)
    - Last 32 bytes: The signing prefix (used during signature generation)

    Standard Ed25519 libraries expect a 32-byte seed and derive the scalar via
    SHA-512. Using `SigningKey(private_bytes)` will produce the WRONG public key.

    To derive the correct public key, we use direct scalar × basepoint multiplication
    with the noclamp variant (since the scalar is already clamped).

    Args:
        private_key: 64-byte MeshCore private key (or just the first 32 bytes)

    Returns:
        32-byte Ed25519 public key
    """
    scalar = private_key[:32]
    # Use noclamp because MeshCore stores already-clamped scalars
    return nacl.bindings.crypto_scalarmult_ed25519_base_noclamp(scalar)


def derive_shared_secret(our_private_key: bytes, their_public_key: bytes) -> bytes:
    """
    Derive ECDH shared secret from Ed25519 keys.

    MeshCore uses Ed25519 keys, but ECDH requires X25519. This function:
    1. Clamps our private key scalar for X25519 (idempotent since already clamped)
    2. Converts their Ed25519 public key to X25519
    3. Performs X25519 scalar multiplication to get the shared secret

    **MeshCore Key Format:**
    MeshCore private keys store the scalar directly (not a seed), so the first
    32 bytes are already the post-SHA-512 clamped scalar. See `derive_public_key`
    for details.

    Args:
        our_private_key: 64-byte MeshCore private key (only first 32 bytes used)
        their_public_key: Their 32-byte Ed25519 public key

    Returns:
        32-byte shared secret
    """
    # Clamp the first 32 bytes of our private key (idempotent for MeshCore keys)
    clamped = _clamp_scalar(our_private_key[:32])

    # Convert their Ed25519 public key to X25519
    x25519_pub = nacl.bindings.crypto_sign_ed25519_pk_to_curve25519(their_public_key)

    # Perform X25519 ECDH
    return nacl.bindings.crypto_scalarmult(clamped, x25519_pub)


def decrypt_direct_message(payload: bytes, shared_secret: bytes) -> DecryptedDirectMessage | None:
    """
    Decrypt a TEXT_MESSAGE payload using the ECDH shared secret.

    TEXT_MESSAGE payload structure:
    - dest_hash (1 byte): First byte of destination public key
    - src_hash (1 byte): First byte of sender public key
    - mac (2 bytes): First 2 bytes of HMAC-SHA256(shared_secret, ciphertext)
    - ciphertext (rest): AES-128-ECB encrypted content

    Decrypted content structure:
    - timestamp (4 bytes, little-endian)
    - flags (1 byte)
    - message text (null-padded)

    Args:
        payload: The TEXT_MESSAGE payload bytes
        shared_secret: 32-byte ECDH shared secret

    Returns:
        DecryptedDirectMessage if successful, None otherwise
    """
    if len(payload) < 4:
        return None

    dest_hash = format(payload[0], "02x")
    src_hash = format(payload[1], "02x")
    mac = payload[2:4]
    ciphertext = payload[4:]

    if len(ciphertext) == 0 or len(ciphertext) % 16 != 0:
        # AES requires 16-byte blocks
        return None

    # Verify MAC: HMAC-SHA256(shared_secret, ciphertext)[:2]
    calculated_mac = hmac.new(shared_secret, ciphertext, hashlib.sha256).digest()[:2]
    if calculated_mac != mac:
        return None

    # Decrypt using AES-128-ECB with shared_secret[:16]
    try:
        cipher = AES.new(shared_secret[:16], AES.MODE_ECB)
        decrypted = cipher.decrypt(ciphertext)
    except Exception as e:
        logger.debug("AES decryption failed for DM: %s", e)
        return None

    if len(decrypted) < 5:
        return None

    # Parse decrypted content
    timestamp = int.from_bytes(decrypted[0:4], "little")
    flags = decrypted[4]

    # Extract message text (UTF-8, null-padded)
    message_bytes = decrypted[5:]
    try:
        message_text = message_bytes.decode("utf-8")
        # Truncate at first null terminator (consistent with channel message handling)
        null_idx = message_text.find("\x00")
        if null_idx >= 0:
            message_text = message_text[:null_idx]
    except UnicodeDecodeError:
        return None

    return DecryptedDirectMessage(
        timestamp=timestamp,
        flags=flags,
        message=message_text,
        dest_hash=dest_hash,
        src_hash=src_hash,
    )


def try_decrypt_dm(
    raw_packet: bytes,
    our_private_key: bytes,
    their_public_key: bytes,
    our_public_key: bytes | None = None,
) -> DecryptedDirectMessage | None:
    """
    Try to decrypt a raw packet as a direct message.

    This performs several checks before attempting expensive ECDH:
    1. Packet must be TEXT_MESSAGE type
    2. dest_hash must match first byte of our public key (or their key for outbound)
    3. src_hash must match first byte of their public key (or our key for outbound)

    Args:
        raw_packet: The complete raw packet bytes
        our_private_key: Our 64-byte Ed25519 private key
        their_public_key: Their 32-byte Ed25519 public key
        our_public_key: Our 32-byte Ed25519 public key (optional, for bidirectional check)

    Returns:
        DecryptedDirectMessage if successful, None otherwise
    """
    packet_info = parse_packet(raw_packet)
    if packet_info is None:
        return None

    # Only TEXT_MESSAGE packets can be decrypted as DMs
    if packet_info.payload_type != PayloadType.TEXT_MESSAGE:
        return None

    if len(packet_info.payload) < 4:
        return None

    # Extract dest/src hashes from payload
    dest_hash = packet_info.payload[0]
    src_hash = packet_info.payload[1]

    # Check if this packet is for us (inbound: them -> us)
    their_first_byte = their_public_key[0]
    is_inbound = src_hash == their_first_byte

    # Check if this packet is from us (outbound: us -> them)
    is_outbound = False
    if our_public_key is not None:
        our_first_byte = our_public_key[0]
        is_outbound = src_hash == our_first_byte and dest_hash == their_first_byte

    if not is_inbound and not is_outbound:
        # Packet doesn't match this contact conversation
        return None

    # Derive shared secret and attempt decryption
    try:
        shared_secret = derive_shared_secret(our_private_key, their_public_key)
    except Exception as e:
        logger.debug("Failed to derive shared secret: %s", e)
        return None

    return decrypt_direct_message(packet_info.payload, shared_secret)
