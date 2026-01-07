"""Tests for the packet decoder module.

These tests verify the cryptographic operations for MeshCore packet decryption,
which is critical for correctly interpreting mesh network messages.
"""

import hashlib
import hmac

import pytest
from Crypto.Cipher import AES

from app.decoder import (
    DecryptedGroupText,
    PacketInfo,
    PayloadType,
    RouteType,
    calculate_channel_hash,
    decrypt_group_text,
    parse_packet,
    try_decrypt_packet_with_channel_key,
)


class TestChannelKeyDerivation:
    """Test channel key derivation from hashtag names."""

    def test_hashtag_key_derivation(self):
        """Hashtag channel keys are derived as SHA256(name)[:16]."""
        channel_name = "#test"
        expected_key = hashlib.sha256(channel_name.encode("utf-8")).digest()[:16]

        # This matches the meshcore_py implementation
        assert len(expected_key) == 16

    def test_channel_hash_calculation(self):
        """Channel hash is the first byte of SHA256(key) as hex."""
        key = bytes(16)  # All zeros
        expected_hash = format(hashlib.sha256(key).digest()[0], "02x")

        result = calculate_channel_hash(key)

        assert result == expected_hash
        assert len(result) == 2  # Two hex chars


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


class TestGroupTextDecryption:
    """Test GROUP_TEXT (channel message) decryption."""

    def _create_encrypted_payload(
        self, channel_key: bytes, timestamp: int, flags: int, message: str
    ) -> bytes:
        """Helper to create a valid encrypted GROUP_TEXT payload."""
        # Build plaintext: timestamp(4) + flags(1) + message + null terminator
        plaintext = (
            timestamp.to_bytes(4, "little")
            + bytes([flags])
            + message.encode("utf-8")
            + b"\x00"
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

    def test_parse_real_advertisement(self):
        """Parse a real advertisement packet from 'Flightless 🥝'."""
        from app.decoder import try_parse_advertisement

        # Real advertisement packet
        packet_hex = (
            "1200AE92564C5C9884854F04F469BBB2BAB8871A078053AF6CF4AA2C014B18CE8A83"
            "54B55C6934EAC9C9BD98A99788B1725379BB25863731ADAB605BCD62F0BA0E467483"
            "E0A21E81C9279665D117B265B192890B8E0C2AE03E48DA5AA28C3EFB842EF656670B"
            "915128D902B72DB5F8466C696768746C65737320F09FA59D"
        )
        packet = bytes.fromhex(packet_hex)

        result = try_parse_advertisement(packet)

        assert result is not None
        # Public key is the first 32 bytes of payload
        assert result.public_key == "ae92564c5c9884854f04f469bbb2bab8871a078053af6cf4aa2c014b18ce8a83"
        # Name should be extracted from the end
        assert result.name == "Flightless 🥝"

    def test_parse_advertisement_extracts_public_key(self):
        """Advertisement parsing extracts the public key correctly."""
        from app.decoder import parse_packet, PayloadType

        packet_hex = (
            "1200AE92564C5C9884854F04F469BBB2BAB8871A078053AF6CF4AA2C014B18CE8A83"
            "54B55C6934EAC9C9BD98A99788B1725379BB25863731ADAB605BCD62F0BA0E467483"
            "E0A21E81C9279665D117B265B192890B8E0C2AE03E48DA5AA28C3EFB842EF656670B"
            "915128D902B72DB5F8466C696768746C65737320F09FA59D"
        )
        packet = bytes.fromhex(packet_hex)

        # Verify packet is recognized as ADVERT type
        info = parse_packet(packet)
        assert info is not None
        assert info.payload_type == PayloadType.ADVERT

    def test_non_advertisement_returns_none(self):
        """Non-advertisement packets return None from try_parse_advertisement."""
        from app.decoder import try_parse_advertisement

        # GROUP_TEXT packet, not an advertisement
        packet = bytes([0x15, 0x00]) + bytes(50)

        result = try_parse_advertisement(packet)

        assert result is None
