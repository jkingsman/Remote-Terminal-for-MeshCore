"""Tests for the packet decoder module.

These tests verify the cryptographic operations for MeshCore packet decryption,
which is critical for correctly interpreting mesh network messages.
"""

import hashlib
import hmac

from Crypto.Cipher import AES

from app.decoder import (
    DecryptedDirectMessage,
    PayloadType,
    RouteType,
    _clamp_scalar,
    decrypt_direct_message,
    decrypt_group_text,
    derive_public_key,
    derive_shared_secret,
    extract_payload,
    parse_packet,
    try_decrypt_dm,
    try_decrypt_packet_with_channel_key,
)


class TestChannelKeyDerivation:
    """Test channel key derivation from hashtag names."""

    def test_hashtag_key_derivation(self):
        """Hashtag channel keys are derived as SHA256(name)[:16]."""
        channel_name = "#test"
        expected_key = hashlib.sha256(channel_name.encode("utf-8")).digest()[:16]

        assert len(expected_key) == 16


class TestPacketParsing:
    """Test raw packet header parsing."""

    def test_parse_flood_packet(self):
        """Parse a FLOOD route type GROUP_TEXT packet."""
        # Header: route_type=FLOOD(1), payload_type=GROUP_TEXT(5), version=0
        # Header byte = (0 << 6) | (5 << 2) | 1 = 0x15
        # Path length = 0
        header = bytes([0x15, 0x00]) + b"payload_data"

        result = parse_packet(header)

        assert result is not None
        assert result.route_type == RouteType.FLOOD
        assert result.payload_type == PayloadType.GROUP_TEXT
        assert result.path_length == 0
        assert result.payload == b"payload_data"

    def test_parse_direct_packet_with_path(self):
        """Parse a DIRECT route type packet with path data."""
        # Header: route_type=DIRECT(2), payload_type=TEXT_MESSAGE(2), version=0
        # Header byte = (0 << 6) | (2 << 2) | 2 = 0x0A
        # Path length = 3, path = [0x01, 0x02, 0x03]
        header = bytes([0x0A, 0x03, 0x01, 0x02, 0x03]) + b"msg"

        result = parse_packet(header)

        assert result is not None
        assert result.route_type == RouteType.DIRECT
        assert result.payload_type == PayloadType.TEXT_MESSAGE
        assert result.path_length == 3
        assert result.payload == b"msg"

    def test_parse_transport_flood_skips_transport_code(self):
        """TRANSPORT_FLOOD packets have 4-byte transport code to skip."""
        # Header: route_type=TRANSPORT_FLOOD(0), payload_type=GROUP_TEXT(5)
        # Header byte = (0 << 6) | (5 << 2) | 0 = 0x14
        # Transport code (4 bytes) + path_length + payload
        header = bytes([0x14, 0xAA, 0xBB, 0xCC, 0xDD, 0x00]) + b"data"

        result = parse_packet(header)

        assert result is not None
        assert result.route_type == RouteType.TRANSPORT_FLOOD
        assert result.payload_type == PayloadType.GROUP_TEXT
        assert result.payload == b"data"

    def test_parse_empty_packet_returns_none(self):
        """Empty packets return None."""
        assert parse_packet(b"") is None
        assert parse_packet(b"\x00") is None

    def test_parse_truncated_packet_returns_none(self):
        """Truncated packets return None."""
        # Packet claiming path_length=10 but no path data
        header = bytes([0x15, 0x0A])

        assert parse_packet(header) is None

    def test_parse_packet_with_no_payload_returns_none(self):
        """Firmware rejects packets that end exactly after the path."""
        packet = bytes([0x15, 0x02, 0xAA, 0xBB])
        assert parse_packet(packet) is None


class TestMultiBytePathParsing:
    """Test packet parsing with multi-byte hop path encoding."""

    def test_parse_two_byte_hops(self):
        """Parse packet with 2-byte hops: path_byte=0x42 → 2 hops × 2 bytes."""
        # Header: FLOOD GROUP_TEXT = 0x15
        # Path byte: 0x42 = mode 1 (2-byte), 2 hops → 4 bytes of path
        path_data = bytes([0xAA, 0xBB, 0xCC, 0xDD])
        packet = bytes([0x15, 0x42]) + path_data + b"payload"

        result = parse_packet(packet)

        assert result is not None
        assert result.path_length == 2  # hop count
        assert result.path_hash_size == 2
        assert result.path == path_data
        assert result.payload == b"payload"

    def test_parse_three_byte_hops(self):
        """Parse packet with 3-byte hops: path_byte=0x81 → 1 hop × 3 bytes."""
        path_data = bytes([0x11, 0x22, 0x33])
        packet = bytes([0x15, 0x81]) + path_data + b"pay"

        result = parse_packet(packet)

        assert result is not None
        assert result.path_length == 1
        assert result.path_hash_size == 3
        assert result.path == path_data
        assert result.payload == b"pay"

    def test_parse_reserved_mode3_returns_none(self):
        """Reserved mode 3 (upper bits = 0b11) should be rejected."""
        # path_byte = 0xC1 → mode 3, 1 hop
        packet = bytes([0x15, 0xC1]) + bytes(10)

        result = parse_packet(packet)
        assert result is None

    def test_parse_oversize_path_len_returns_none(self):
        """Oversized-but-well-formed path bytes are invalid per firmware."""
        packet = bytes([0x15, 0xBF]) + bytes(189) + b"payload"
        assert parse_packet(packet) is None

    def test_parse_two_byte_hops_truncated_returns_none(self):
        """Truncated path data for multi-byte hops returns None."""
        # path_byte = 0x42 → 2 hops × 2 bytes = 4 bytes needed, only 2 provided
        packet = bytes([0x15, 0x42, 0xAA, 0xBB])

        result = parse_packet(packet)
        assert result is None

    def test_backward_compat_one_byte_hops(self):
        """Old-style 1-byte hop packets still parse correctly with hash_size=1."""
        packet = bytes([0x0A, 0x03, 0x01, 0x02, 0x03]) + b"msg"

        result = parse_packet(packet)

        assert result is not None
        assert result.path_length == 3
        assert result.path_hash_size == 1
        assert result.path == bytes([0x01, 0x02, 0x03])

    def test_extract_payload_two_byte_hops(self):
        """extract_payload correctly skips multi-byte path data."""
        path_data = bytes([0xAA, 0xBB, 0xCC, 0xDD])
        packet = bytes([0x15, 0x42]) + path_data + b"the_payload"

        result = extract_payload(packet)
        assert result == b"the_payload"

    def test_extract_payload_reserved_mode_returns_none(self):
        """extract_payload rejects reserved mode 3."""
        packet = bytes([0x15, 0xC1]) + bytes(10)

        result = extract_payload(packet)
        assert result is None

    def test_extract_payload_no_payload_returns_none(self):
        """extract_payload matches firmware and rejects payload-less packets."""
        packet = bytes([0x15, 0x02, 0xAA, 0xBB])
        assert extract_payload(packet) is None

    def test_parse_direct_two_byte_hops_with_transport(self):
        """TRANSPORT_DIRECT with 2-byte hops parses correctly."""
        # Header: TRANSPORT_DIRECT = 0x03, GROUP_TEXT = 5 → (5<<2)|3 = 0x17
        transport_code = bytes([0x11, 0x22, 0x33, 0x44])
        # path_byte = 0x41 → mode 1, 1 hop → 2 bytes of path
        path_data = bytes([0xAB, 0xCD])
        packet = bytes([0x17]) + transport_code + bytes([0x41]) + path_data + b"data"

        result = parse_packet(packet)

        assert result is not None
        assert result.route_type == RouteType.TRANSPORT_DIRECT
        assert result.path_length == 1
        assert result.path_hash_size == 2
        assert result.path == path_data
        assert result.payload == b"data"

    def test_zero_hops_mode1(self):
        """Zero hops with mode 1 (2-byte) → direct path, no path bytes."""
        # path_byte = 0x40 → mode 1, 0 hops
        packet = bytes([0x15, 0x40]) + b"payload"

        result = parse_packet(packet)

        assert result is not None
        assert result.path_length == 0
        assert result.path_hash_size == 2
        assert result.path == b""
        assert result.payload == b"payload"


class TestGroupTextDecryption:
    """Test GROUP_TEXT (channel message) decryption."""

    def _create_encrypted_payload(
        self, channel_key: bytes, timestamp: int, flags: int, message: str
    ) -> bytes:
        """Helper to create a valid encrypted GROUP_TEXT payload."""
        # Build plaintext: timestamp(4) + flags(1) + message + null terminator
        plaintext = (
            timestamp.to_bytes(4, "little") + bytes([flags]) + message.encode("utf-8") + b"\x00"
        )

        # Pad to 16-byte boundary
        pad_len = (16 - len(plaintext) % 16) % 16
        if pad_len == 0:
            pad_len = 16
        plaintext += bytes(pad_len)

        # Encrypt with AES-128 ECB
        cipher = AES.new(channel_key, AES.MODE_ECB)
        ciphertext = cipher.encrypt(plaintext)

        # Calculate MAC: HMAC-SHA256(channel_secret, ciphertext)[:2]
        channel_secret = channel_key + bytes(16)
        mac = hmac.new(channel_secret, ciphertext, hashlib.sha256).digest()[:2]

        # Build payload: channel_hash(1) + mac(2) + ciphertext
        channel_hash = hashlib.sha256(channel_key).digest()[0:1]

        return channel_hash + mac + ciphertext

    def test_decrypt_valid_message(self):
        """Decrypt a valid GROUP_TEXT message."""
        channel_key = hashlib.sha256(b"#testchannel").digest()[:16]
        timestamp = 1700000000
        message = "TestUser: Hello world"

        payload = self._create_encrypted_payload(channel_key, timestamp, 0, message)

        result = decrypt_group_text(payload, channel_key)

        assert result is not None
        assert result.timestamp == timestamp
        assert result.sender == "TestUser"
        assert result.message == "Hello world"

    def test_decrypt_message_without_sender_prefix(self):
        """Messages without 'sender: ' format have no parsed sender."""
        channel_key = hashlib.sha256(b"#test").digest()[:16]
        message = "Just a plain message"

        payload = self._create_encrypted_payload(channel_key, 1234567890, 0, message)

        result = decrypt_group_text(payload, channel_key)

        assert result is not None
        assert result.sender is None
        assert result.message == "Just a plain message"

    def test_decrypt_with_wrong_key_fails(self):
        """Decryption with wrong key fails MAC verification."""
        correct_key = hashlib.sha256(b"#correct").digest()[:16]
        wrong_key = hashlib.sha256(b"#wrong").digest()[:16]

        payload = self._create_encrypted_payload(correct_key, 1234567890, 0, "test")

        result = decrypt_group_text(payload, wrong_key)

        assert result is None

    def test_decrypt_corrupted_mac_fails(self):
        """Corrupted MAC causes decryption to fail."""
        channel_key = hashlib.sha256(b"#test").digest()[:16]
        payload = self._create_encrypted_payload(channel_key, 1234567890, 0, "test")

        # Corrupt the MAC (bytes 1-2)
        corrupted = payload[:1] + bytes([payload[1] ^ 0xFF, payload[2] ^ 0xFF]) + payload[3:]

        result = decrypt_group_text(corrupted, channel_key)

        assert result is None


class TestTryDecryptPacket:
    """Test the full packet decryption pipeline."""

    def test_only_group_text_packets_decrypted(self):
        """Non-GROUP_TEXT packets return None."""
        # TEXT_MESSAGE packet (payload_type=2)
        # Header: route_type=FLOOD(1), payload_type=TEXT_MESSAGE(2)
        # Header byte = (0 << 6) | (2 << 2) | 1 = 0x09
        packet = bytes([0x09, 0x00]) + b"some_data"
        key = bytes(16)

        result = try_decrypt_packet_with_channel_key(packet, key)

        assert result is None

    def test_channel_hash_mismatch_returns_none(self):
        """Packets with non-matching channel hash return None early."""
        # GROUP_TEXT packet with channel_hash that doesn't match our key
        # Header: route_type=FLOOD(1), payload_type=GROUP_TEXT(5)
        # Header byte = 0x15
        wrong_hash = bytes([0xFF])  # Unlikely to match any real key
        packet = bytes([0x15, 0x00]) + wrong_hash + bytes(20)

        key = hashlib.sha256(b"#test").digest()[:16]

        result = try_decrypt_packet_with_channel_key(packet, key)

        assert result is None


class TestRealWorldPackets:
    """Test with real captured packets to ensure decoder matches protocol."""

    def test_decrypt_six77_channel_message(self):
        """Decrypt a real packet from #six77 channel."""
        # Real packet captured from #six77 hashtag channel
        packet_hex = (
            "1500E69C7A89DD0AF6A2D69F5823B88F9720731E4B887C56932BF889255D8D926D"
            "99195927144323A42DD8A158F878B518B8304DF55E80501C7D02A9FFD578D35182"
            "83156BBA257BF8413E80A237393B2E4149BBBC864371140A9BBC4E23EB9BF203EF"
            "0D029214B3E3AAC3C0295690ACDB89A28619E7E5F22C83E16073AD679D25FA904D"
            "07E5ACF1DB5A7C77D7E1719FB9AE5BF55541EE0D7F59ED890E12CF0FEED6700818"
        )
        packet = bytes.fromhex(packet_hex)

        # Verify key derivation: SHA256("#six77")[:16]
        channel_key = hashlib.sha256(b"#six77").digest()[:16]
        assert channel_key.hex() == "7aba109edcf304a84433cb71d0f3ab73"

        # Decrypt the packet
        result = try_decrypt_packet_with_channel_key(packet, channel_key)

        assert result is not None
        assert result.sender == "Flightless🥝"
        assert "hashtag room is essentially public" in result.message
        assert result.channel_hash == "e6"
        assert result.timestamp == 1766604717


class TestAdvertisementParsing:
    """Test parsing of advertisement packets."""

    def test_parse_repeater_advertisement_with_gps(self):
        """Parse a repeater advertisement with GPS coordinates."""
        from app.decoder import parse_advertisement, parse_packet

        # Repeater packet with lat/lon of 49.02056 / -123.82935
        # Flags 0x92: Role=Repeater (2), Location=Yes, Name=Yes
        packet_hex = (
            "1106538B1CD273868576DC7F679B493F9AB5AC316173E1A56D3388BC3BA75F583F63"
            "AB0D1BA2A8ABD0BC6669DBF719E67E4C8517BA4E0D6F8C96A323E9D13A77F2630DED"
            "965A5C17C3EC6ED1601EEFE857749DA24E9F39CBEACD722C3708F433DB5FA9BAF0BA"
            "F9BC5B1241069290FEEB029A839EF843616E204F204D657368203220F09FA5AB"
        )
        packet = bytes.fromhex(packet_hex)

        info = parse_packet(packet)
        assert info is not None
        result = parse_advertisement(info.payload)

        assert result is not None
        assert (
            result.public_key == "8576dc7f679b493f9ab5ac316173e1a56d3388bc3ba75f583f63ab0d1ba2a8ab"
        )
        assert result.name == "Can O Mesh 2 🥫"
        assert result.device_role == 2  # Repeater
        assert result.timestamp > 0  # Has valid timestamp
        assert result.lat is not None
        assert result.lon is not None
        assert abs(result.lat - 49.02056) < 0.000001
        assert abs(result.lon - (-123.82935)) < 0.000001

    def test_parse_chat_node_advertisement_with_gps(self):
        """Parse a chat node advertisement with GPS coordinates."""
        from app.decoder import parse_advertisement, parse_packet

        # Chat node packet with lat/lon of 47.786038 / -122.344096
        # Flags 0x91: Role=Chat (1), Location=Yes, Name=Yes
        packet_hex = (
            "1100AE92564C5C9884854F04F469BBB2BAB8871A078053AF6CF4AA2C014B18CE8A83"
            "2DBF6669128E9476F36320F21D1B37FF1CF31680F50F4B17EDABCC7CF8C47D3C5E1D"
            "F3AFD0C8721EA06A8078462EF241DEF80AD6922751F206E3BB121DFB604F4146D60D"
            "913628D902602DB5F8466C696768746C657373F09FA59D"
        )
        packet = bytes.fromhex(packet_hex)

        info = parse_packet(packet)
        assert info is not None
        result = parse_advertisement(info.payload)

        assert result is not None
        assert (
            result.public_key == "ae92564c5c9884854f04f469bbb2bab8871a078053af6cf4aa2c014b18ce8a83"
        )
        assert result.name == "Flightless🥝"
        assert result.device_role == 1  # Chat node
        assert result.timestamp > 0  # Has valid timestamp
        assert result.lat is not None
        assert result.lon is not None
        assert abs(result.lat - 47.786038) < 0.000001
        assert abs(result.lon - (-122.344096)) < 0.000001

    def test_parse_advertisement_without_gps(self):
        """Parse an advertisement without GPS coordinates."""
        from app.decoder import parse_advertisement, parse_packet

        # Chat node packet without location
        # Flags 0x81: Role=Chat (1), Location=No, Name=Yes
        packet_hex = (
            "1104D7F9E07A2E38C81F7DC0C1CEDDED6B415B4367CF48F578C5A092CED3490FF0C7"
            "6EFDF1F5A4BD6669D3D143CFF384D8B3BD950CDCA31C98B7DA789D004D04DED31E16"
            "B998E1AE352B283EAC8ABCF1F07214EC3BBF7AF3EB8EBF15C00417F2425A259E7CE6"
            "A875BA0D814D656E6E697344"
        )
        packet = bytes.fromhex(packet_hex)

        info = parse_packet(packet)
        assert info is not None
        result = parse_advertisement(info.payload)

        assert result is not None
        assert (
            result.public_key == "2e38c81f7dc0c1cedded6b415b4367cf48f578c5a092ced3490ff0c76efdf1f5"
        )
        assert result.name == "MennisD"
        assert result.device_role == 1  # Chat node
        assert result.timestamp > 0  # Has valid timestamp
        assert result.lat is None
        assert result.lon is None

    def test_parse_advertisement_extracts_public_key(self):
        """Advertisement parsing extracts the public key correctly."""
        from app.decoder import parse_advertisement, parse_packet

        packet_hex = (
            "1100AE92564C5C9884854F04F469BBB2BAB8871A078053AF6CF4AA2C014B18CE8A83"
            "2DBF6669128E9476F36320F21D1B37FF1CF31680F50F4B17EDABCC7CF8C47D3C5E1D"
            "F3AFD0C8721EA06A8078462EF241DEF80AD6922751F206E3BB121DFB604F4146D60D"
            "913628D902602DB5F8466C696768746C657373F09FA59D"
        )
        packet = bytes.fromhex(packet_hex)

        info = parse_packet(packet)
        assert info is not None

        result = parse_advertisement(info.payload)
        assert result is not None
        assert (
            result.public_key == "ae92564c5c9884854f04f469bbb2bab8871a078053af6cf4aa2c014b18ce8a83"
        )

    def test_non_advertisement_returns_none(self):
        """Non-advertisement payload returns None when parsed as advertisement."""
        from app.decoder import parse_advertisement, parse_packet

        # GROUP_TEXT packet, not an advertisement
        packet = bytes([0x15, 0x00]) + bytes(50)

        info = parse_packet(packet)
        assert info is not None

        result = parse_advertisement(info.payload)
        assert result is None


class TestScalarClamping:
    """Test X25519 scalar clamping for ECDH."""

    def test_clamp_scalar_modifies_first_byte(self):
        """Clamping clears the lower 3 bits of the first byte."""
        # Input with all bits set in first byte
        scalar = bytes([0xFF]) + bytes(31)

        result = _clamp_scalar(scalar)

        # First byte should have lower 3 bits cleared: 0xFF & 248 = 0xF8
        assert result[0] == 0xF8

    def test_clamp_scalar_modifies_last_byte(self):
        """Clamping modifies the last byte for correct group operations."""
        # Input with all bits set in last byte
        scalar = bytes(31) + bytes([0xFF])

        result = _clamp_scalar(scalar)

        # Last byte: (0xFF & 63) | 64 = 0x7F
        assert result[31] == 0x7F

    def test_clamp_scalar_preserves_middle_bytes(self):
        """Clamping preserves the middle bytes unchanged."""
        # Known middle bytes
        scalar = bytes([0xAB]) + bytes([0x12, 0x34, 0x56] * 10)[:30] + bytes([0xCD])

        result = _clamp_scalar(scalar)

        # Middle bytes should be unchanged
        assert result[1:31] == scalar[1:31]

    def test_clamp_scalar_truncates_to_32_bytes(self):
        """Clamping uses only first 32 bytes of input."""
        # 64-byte input (typical Ed25519 private key)
        scalar = bytes(64)

        result = _clamp_scalar(scalar)

        assert len(result) == 32


class TestPublicKeyDerivation:
    """Test deriving Ed25519 public key from MeshCore private key."""

    # Test data from real MeshCore keys
    # The private key's first 32 bytes are the scalar (post-SHA-512 clamped)
    # The public key is derived via scalar × basepoint, NOT from the last 32 bytes
    #
    # IMPORTANT: The last 32 bytes of a MeshCore private key are the signing prefix,
    # NOT the public key! Standard Ed25519 libraries will give wrong results because
    # they expect a seed, not a raw scalar.
    FACE12_PRIV = bytes.fromhex(
        "58BA1940E97099CBB4357C62CE9C7F4B245C94C90D722E67201B989F9FEACF7B"
        "77ACADDB84438514022BDB0FC3140C2501859BE1772AC7B8C7E41DC0F40490A1"
    )
    # Expected public key derived from scalar × basepoint
    # Note: This starts with "face12" - the derived public key, NOT the signing prefix
    FACE12_PUB_EXPECTED = bytes.fromhex(
        "FACE123334789E2B81519AFDBC39A3C9EB7EA3457AD367D3243597A484847E46"
    )

    def test_derive_public_key_from_meshcore_private(self):
        """derive_public_key correctly derives public key from MeshCore private key."""
        result = derive_public_key(self.FACE12_PRIV)

        assert len(result) == 32
        assert result == self.FACE12_PUB_EXPECTED

    def test_derive_public_key_from_scalar_only(self):
        """derive_public_key works with just the 32-byte scalar."""
        scalar_only = self.FACE12_PRIV[:32]

        result = derive_public_key(scalar_only)

        assert len(result) == 32
        assert result == self.FACE12_PUB_EXPECTED

    def test_derive_public_key_deterministic(self):
        """Same private key always produces same public key."""
        result1 = derive_public_key(self.FACE12_PRIV)
        result2 = derive_public_key(self.FACE12_PRIV)

        assert result1 == result2


class TestSharedSecretDerivation:
    """Test ECDH shared secret derivation from Ed25519 keys."""

    # Test data from real MeshCore keys
    # The private key's first 32 bytes are the scalar (post-SHA-512 clamped)
    # The last 32 bytes are the signing prefix (NOT the public key, though they may match)
    FACE12_PRIV = bytes.fromhex(
        "58BA1940E97099CBB4357C62CE9C7F4B245C94C90D722E67201B989F9FEACF7B"
        "77ACADDB84438514022BDB0FC3140C2501859BE1772AC7B8C7E41DC0F40490A1"
    )
    # a1b2c3 public key (32 bytes)
    A1B2C3_PUB = bytes.fromhex("a1b2c3d3ba9f5fa8705b9845fe11cc6f01d1d49caaf4d122ac7121663c5beec7")

    def test_derive_shared_secret_returns_32_bytes(self):
        """Shared secret derivation returns 32-byte value."""
        result = derive_shared_secret(self.FACE12_PRIV, self.A1B2C3_PUB)

        assert len(result) == 32

    def test_derive_shared_secret_deterministic(self):
        """Same inputs always produce same shared secret."""
        result1 = derive_shared_secret(self.FACE12_PRIV, self.A1B2C3_PUB)
        result2 = derive_shared_secret(self.FACE12_PRIV, self.A1B2C3_PUB)

        assert result1 == result2

    def test_derive_shared_secret_different_keys_different_result(self):
        """Different key pairs produce different shared secrets."""
        # Use the real FACE12 public key as a second peer key (valid curve point)
        face12_pub = derive_public_key(self.FACE12_PRIV)

        result1 = derive_shared_secret(self.FACE12_PRIV, self.A1B2C3_PUB)
        result2 = derive_shared_secret(self.FACE12_PRIV, face12_pub)

        assert result1 != result2


class TestDirectMessageDecryption:
    """Test TEXT_MESSAGE (direct message) payload decryption."""

    # Real test vector from user
    # Payload: [dest_hash:1][src_hash:1][mac:2][ciphertext]
    PAYLOAD = bytes.fromhex(
        "FAA1295471ADB44A98B13CA528A4B5C4FBC29B4DA3CED477519B2FBD8FD5467C31E5D58B"
    )

    # Keys for deriving shared secret
    FACE12_PRIV = bytes.fromhex(
        "58BA1940E97099CBB4357C62CE9C7F4B245C94C90D722E67201B989F9FEACF7B"
        "77ACADDB84438514022BDB0FC3140C2501859BE1772AC7B8C7E41DC0F40490A1"
    )
    A1B2C3_PUB = bytes.fromhex("a1b2c3d3ba9f5fa8705b9845fe11cc6f01d1d49caaf4d122ac7121663c5beec7")

    EXPECTED_MESSAGE = "Hello there, Mr. Face!"

    def test_decrypt_real_dm_payload(self):
        """Decrypt a real DM payload with known shared secret."""
        shared_secret = derive_shared_secret(self.FACE12_PRIV, self.A1B2C3_PUB)

        result = decrypt_direct_message(self.PAYLOAD, shared_secret)

        assert result is not None
        assert result.message == self.EXPECTED_MESSAGE
        assert result.dest_hash == "fa"  # First byte of payload
        assert result.src_hash == "a1"  # Second byte, matches a1b2c3

    def test_decrypt_extracts_timestamp(self):
        """Decrypted message contains valid timestamp."""
        shared_secret = derive_shared_secret(self.FACE12_PRIV, self.A1B2C3_PUB)

        result = decrypt_direct_message(self.PAYLOAD, shared_secret)

        assert result is not None
        assert result.timestamp > 0  # Non-zero timestamp
        assert result.timestamp < 2**32  # Within uint32 range

    def test_decrypt_extracts_flags(self):
        """Decrypted message contains flags byte."""
        shared_secret = derive_shared_secret(self.FACE12_PRIV, self.A1B2C3_PUB)

        result = decrypt_direct_message(self.PAYLOAD, shared_secret)

        assert result is not None
        assert isinstance(result.flags, int)
        assert 0 <= result.flags <= 255

    def test_decrypt_with_wrong_secret_fails(self):
        """Decryption with incorrect shared secret fails MAC verification."""
        wrong_secret = bytes(32)  # All zeros

        result = decrypt_direct_message(self.PAYLOAD, wrong_secret)

        assert result is None

    def test_decrypt_with_corrupted_mac_fails(self):
        """Corrupted MAC causes decryption to fail."""
        shared_secret = derive_shared_secret(self.FACE12_PRIV, self.A1B2C3_PUB)

        # Corrupt the MAC (bytes 2-3)
        corrupted = self.PAYLOAD[:2] + bytes([0xFF, 0xFF]) + self.PAYLOAD[4:]

        result = decrypt_direct_message(corrupted, shared_secret)

        assert result is None

    def test_decrypt_too_short_payload_returns_none(self):
        """Payloads shorter than minimum (4 bytes) return None."""
        shared_secret = derive_shared_secret(self.FACE12_PRIV, self.A1B2C3_PUB)

        result = decrypt_direct_message(bytes(3), shared_secret)

        assert result is None

    def test_decrypt_invalid_ciphertext_length_returns_none(self):
        """Ciphertext not a multiple of 16 bytes returns None."""
        shared_secret = derive_shared_secret(self.FACE12_PRIV, self.A1B2C3_PUB)

        # 4-byte header + 15-byte ciphertext (not multiple of 16)
        invalid_payload = bytes(4 + 15)

        result = decrypt_direct_message(invalid_payload, shared_secret)

        assert result is None


class TestTryDecryptDM:
    """Test full packet decryption for direct messages."""

    # Full packet: header + path_length + payload
    # Header byte = 0x09: route_type=FLOOD(1), payload_type=TEXT_MESSAGE(2)
    # Header byte = (0 << 6) | (2 << 2) | 1 = 0x09
    # Path length = 0
    FULL_PACKET = bytes.fromhex(
        "0900FAA1295471ADB44A98B13CA528A4B5C4FBC29B4DA3CED477519B2FBD8FD5467C31E5D58B"
    )

    # Keys
    FACE12_PRIV = bytes.fromhex(
        "58BA1940E97099CBB4357C62CE9C7F4B245C94C90D722E67201B989F9FEACF7B"
        "77ACADDB84438514022BDB0FC3140C2501859BE1772AC7B8C7E41DC0F40490A1"
    )
    # FACE12 public key - derived via scalar × basepoint, NOT the last 32 bytes!
    # The last 32 bytes (77AC...) are the signing prefix, not the public key.
    FACE12_PUB = bytes.fromhex("FACE123334789E2B81519AFDBC39A3C9EB7EA3457AD367D3243597A484847E46")

    A1B2C3_PUB = bytes.fromhex("a1b2c3d3ba9f5fa8705b9845fe11cc6f01d1d49caaf4d122ac7121663c5beec7")

    EXPECTED_MESSAGE = "Hello there, Mr. Face!"

    def test_try_decrypt_dm_full_packet(self):
        """Decrypt a full TEXT_MESSAGE packet."""
        result = try_decrypt_dm(
            self.FULL_PACKET,
            self.FACE12_PRIV,
            self.A1B2C3_PUB,
            our_public_key=self.FACE12_PUB,
        )

        assert result is not None
        assert result.message == self.EXPECTED_MESSAGE

    def test_try_decrypt_dm_inbound_message(self):
        """Decrypt an inbound message (from them to us)."""
        # src_hash = a1 matches A1B2C3's first byte
        result = try_decrypt_dm(
            self.FULL_PACKET,
            self.FACE12_PRIV,
            self.A1B2C3_PUB,
            our_public_key=None,  # Without our pubkey, only checks inbound
        )

        assert result is not None
        assert result.src_hash == "a1"

    def test_try_decrypt_dm_non_text_message_returns_none(self):
        """Non-TEXT_MESSAGE packets return None."""
        # GROUP_TEXT packet (payload_type=5)
        # Header byte = (0 << 6) | (5 << 2) | 1 = 0x15
        group_text_packet = bytes([0x15, 0x00]) + self.FULL_PACKET[2:]

        result = try_decrypt_dm(
            group_text_packet,
            self.FACE12_PRIV,
            self.A1B2C3_PUB,
        )

        assert result is None

    def test_try_decrypt_dm_wrong_src_hash_returns_none(self):
        """Packets from unknown senders return None."""
        # Create a packet with different src_hash
        # Original: FA A1 ... -> dest=FA, src=A1
        # Modified: FA BB ... -> dest=FA, src=BB (doesn't match A1B2C3)
        modified_payload = bytes([0xFA, 0xBB]) + self.FULL_PACKET[4:]
        modified_packet = self.FULL_PACKET[:2] + modified_payload

        result = try_decrypt_dm(
            modified_packet,
            self.FACE12_PRIV,
            self.A1B2C3_PUB,
            our_public_key=self.FACE12_PUB,
        )

        assert result is None

    def test_try_decrypt_dm_empty_packet_returns_none(self):
        """Empty packets return None."""
        result = try_decrypt_dm(
            b"",
            self.FACE12_PRIV,
            self.A1B2C3_PUB,
        )

        assert result is None

    def test_try_decrypt_dm_truncated_packet_returns_none(self):
        """Truncated packets return None."""
        result = try_decrypt_dm(
            self.FULL_PACKET[:5],  # Only header + partial payload
            self.FACE12_PRIV,
            self.A1B2C3_PUB,
        )

        assert result is None


class TestRealWorldDMPacket:
    """End-to-end test with exact real-world test data."""

    def test_full_dm_decryption_flow(self):
        """
        Complete decryption flow with real test vectors.

        Test data from user:
        - face12 private key (64 bytes Ed25519)
        - a1b2c3 public key (32 bytes)
        - Encrypted payload producing "Hello there, Mr. Face!"
        """
        # Keys
        face12_priv = bytes.fromhex(
            "58BA1940E97099CBB4357C62CE9C7F4B245C94C90D722E67201B989F9FEACF7B"
            "77ACADDB84438514022BDB0FC3140C2501859BE1772AC7B8C7E41DC0F40490A1"
        )
        # Derived public key (scalar × basepoint) - NOT the signing prefix from bytes 32-64
        # First byte is 0xFA, matching dest_hash in test packet
        face12_pub = bytes.fromhex(
            "FACE123334789E2B81519AFDBC39A3C9EB7EA3457AD367D3243597A484847E46"
        )
        a1b2c3_pub = bytes.fromhex(
            "a1b2c3d3ba9f5fa8705b9845fe11cc6f01d1d49caaf4d122ac7121663c5beec7"
        )

        # Full packet with header
        full_packet = bytes.fromhex(
            "0900FAA1295471ADB44A98B13CA528A4B5C4FBC29B4DA3CED477519B2FBD8FD5467C31E5D58B"
        )

        # Decrypt
        result = try_decrypt_dm(
            full_packet,
            face12_priv,
            a1b2c3_pub,
            our_public_key=face12_pub,
        )

        # Verify
        assert result is not None
        assert isinstance(result, DecryptedDirectMessage)
        assert result.message == "Hello there, Mr. Face!"
        assert result.dest_hash == "fa"  # First byte of derived face12 pubkey (0xFA)
        assert result.src_hash == "a1"  # First byte of a1b2c3 pubkey
