"""Tests using real MeshCore packet data and cryptographic keys.

These tests verify the decryption pipeline end-to-end with actual radio packets
captured from the mesh network. No crypto functions are mocked.

Test data:
  - Client 1 ("a1b2c3d3"): sender of the DM
  - Client 2 ("face1233"): receiver of the DM
  - Channel: #six77 (hashtag room, key derived from SHA-256 of name)
"""

from hashlib import sha256
from unittest.mock import patch

import pytest

from app.decoder import (
    DecryptedDirectMessage,
    PayloadType,
    RouteType,
    decrypt_direct_message,
    derive_public_key,
    derive_shared_secret,
    parse_packet,
    try_decrypt_dm,
    try_decrypt_packet_with_channel_key,
)
from app.repository import ContactRepository, MessageRepository, RawPacketRepository

# ---------------------------------------------------------------------------
# Real test data captured from a MeshCore mesh network
# ---------------------------------------------------------------------------

# Client 1 (sender of the DM)
CLIENT1_PUBLIC_HEX = "a1b2c3d3ba9f5fa8705b9845fe11cc6f01d1d49caaf4d122ac7121663c5beec7"
CLIENT1_PRIVATE_HEX = (
    "1808C3512F063796E492B9FA101A7A6239F14E71F8D1D5AD086E8E228ED0A076"
    "D5ED26C82C6E64ABF1954336E42CF68E4AB288A4D38E40ED0F5870FED95C1DEB"
)
CLIENT1_PUBLIC = bytes.fromhex(CLIENT1_PUBLIC_HEX)
CLIENT1_PRIVATE = bytes.fromhex(CLIENT1_PRIVATE_HEX)

# Client 2 (receiver of the DM)
CLIENT2_PUBLIC_HEX = "face123334789e2b81519afdbc39a3c9eb7ea3457ad367d3243597a484847e46"
CLIENT2_PRIVATE_HEX = (
    "58BA1940E97099CBB4357C62CE9C7F4B245C94C90D722E67201B989F9FEACF7B"
    "77ACADDB84438514022BDB0FC3140C2501859BE1772AC7B8C7E41DC0F40490A1"
)
CLIENT2_PUBLIC = bytes.fromhex(CLIENT2_PUBLIC_HEX)
CLIENT2_PRIVATE = bytes.fromhex(CLIENT2_PRIVATE_HEX)

# DM packet: client 1 -> client 2
DM_PACKET_HEX = "0900FAA1295471ADB44A98B13CA528A4B5C4FBC29B4DA3CED477519B2FBD8FD5467C31E5D58B"
DM_PACKET = bytes.fromhex(DM_PACKET_HEX)
DM_PLAINTEXT = "Hello there, Mr. Face!"

# Channel message in #six77
CHANNEL_PACKET_HEX = (
    "1500E69C7A89DD0AF6A2D69F5823B88F9720731E4B887C56932BF889255D8D926D"
    "99195927144323A42DD8A158F878B518B8304DF55E80501C7D02A9FFD578D35182"
    "83156BBA257BF8413E80A237393B2E4149BBBC864371140A9BBC4E23EB9BF203EF"
    "0D029214B3E3AAC3C0295690ACDB89A28619E7E5F22C83E16073AD679D25FA904D"
    "07E5ACF1DB5A7C77D7E1719FB9AE5BF55541EE0D7F59ED890E12CF0FEED6700818"
)
CHANNEL_PACKET = bytes.fromhex(CHANNEL_PACKET_HEX)
CHANNEL_NAME = "#six77"
CHANNEL_KEY = sha256(CHANNEL_NAME.encode("utf-8")).digest()[:16]
CHANNEL_PLAINTEXT_FULL = (
    "Flightless🥝: hello there; this hashtag room is essentially public. "
    "MeshCore has great crypto; use private rooms or DMs for private comms instead!"
)
CHANNEL_SENDER = "Flightless🥝"
CHANNEL_MESSAGE_BODY = (
    "hello there; this hashtag room is essentially public. "
    "MeshCore has great crypto; use private rooms or DMs for private comms instead!"
)

# Channel messages in #bot using multi-byte path encodings
BOT_CHANNEL_NAME = "#bot"
BOT_CHANNEL_KEY = bytes.fromhex("eb50a1bcb3e4e5d7bf69a57c9dada211")

BOT_PACKET_3BYTE_3HOPS_HEX = "15833fa002860ccae0eed9ca78b9ab0775d477c1f6490a398bf4edc75240"
BOT_PACKET_3BYTE_3HOPS = bytes.fromhex(BOT_PACKET_3BYTE_3HOPS_HEX)
BOT_PACKET_3BYTE_3HOPS_PATH_HEX = "3fa002860ccae0eed9"
BOT_PACKET_3BYTE_3HOPS_SENDER = "Roy B V4"
BOT_PACKET_3BYTE_3HOPS_MESSAGE = "P"
BOT_PACKET_3BYTE_3HOPS_FULL = f"{BOT_PACKET_3BYTE_3HOPS_SENDER}: {BOT_PACKET_3BYTE_3HOPS_MESSAGE}"

BOT_PACKET_2BYTE_0HOPS_HEX = (
    "1540cab3b15626481a5ba64247ab25766e410b026e0678a32da9f0c3946fae5b714cab170f"
)
BOT_PACKET_2BYTE_0HOPS = bytes.fromhex(BOT_PACKET_2BYTE_0HOPS_HEX)
BOT_PACKET_2BYTE_0HOPS_SENDER = "Howl 👾"
BOT_PACKET_2BYTE_0HOPS_MESSAGE = "prefix 0101"
BOT_PACKET_2BYTE_0HOPS_FULL = f"{BOT_PACKET_2BYTE_0HOPS_SENDER}: {BOT_PACKET_2BYTE_0HOPS_MESSAGE}"


# ============================================================================
# Direct Message Decryption
# ============================================================================


class TestDMDecryption:
    """Test DM decryption using real captured packet data."""

    def test_derive_public_key_from_private(self):
        """derive_public_key reproduces known public keys from private keys."""
        assert derive_public_key(CLIENT1_PRIVATE) == CLIENT1_PUBLIC
        assert derive_public_key(CLIENT2_PRIVATE) == CLIENT2_PUBLIC

    def test_shared_secret_is_symmetric(self):
        """Both parties derive the same ECDH shared secret."""
        secret_1to2 = derive_shared_secret(CLIENT1_PRIVATE, CLIENT2_PUBLIC)
        secret_2to1 = derive_shared_secret(CLIENT2_PRIVATE, CLIENT1_PUBLIC)
        assert secret_1to2 == secret_2to1

    def test_parse_dm_packet_header(self):
        """Raw DM packet parses to the expected header fields."""
        info = parse_packet(DM_PACKET)
        assert info is not None
        assert info.route_type == RouteType.FLOOD
        assert info.payload_type == PayloadType.TEXT_MESSAGE
        assert info.path_length == 0

    def test_decrypt_dm_as_receiver(self):
        """Receiver (face1233) decrypts the DM with correct plaintext."""
        result = try_decrypt_dm(
            DM_PACKET,
            our_private_key=CLIENT2_PRIVATE,
            their_public_key=CLIENT1_PUBLIC,
            our_public_key=CLIENT2_PUBLIC,
        )
        assert result is not None
        assert isinstance(result, DecryptedDirectMessage)
        assert result.message == DM_PLAINTEXT

    def test_decrypt_dm_as_sender(self):
        """Sender (a1b2c3d3) decrypts the DM too (outgoing echo scenario)."""
        result = try_decrypt_dm(
            DM_PACKET,
            our_private_key=CLIENT1_PRIVATE,
            their_public_key=CLIENT2_PUBLIC,
            our_public_key=CLIENT1_PUBLIC,
        )
        assert result is not None
        assert result.message == DM_PLAINTEXT

    def test_direction_hashes_match_key_prefixes(self):
        """dest_hash and src_hash correspond to first bytes of public keys."""
        result = try_decrypt_dm(
            DM_PACKET,
            our_private_key=CLIENT2_PRIVATE,
            their_public_key=CLIENT1_PUBLIC,
            our_public_key=CLIENT2_PUBLIC,
        )
        assert result is not None
        # Packet was sent FROM client1 TO client2
        assert result.src_hash == format(CLIENT1_PUBLIC[0], "02x")  # a1
        assert result.dest_hash == format(CLIENT2_PUBLIC[0], "02x")  # fa

    def test_wrong_key_fails_mac(self):
        """Decryption with an unrelated key fails (MAC mismatch)."""
        wrong_private = b"\x01" * 64
        result = try_decrypt_dm(
            DM_PACKET,
            our_private_key=wrong_private,
            their_public_key=CLIENT1_PUBLIC,
        )
        assert result is None

    def test_decrypt_dm_payload_directly(self):
        """decrypt_direct_message works with just the payload and shared secret."""
        info = parse_packet(DM_PACKET)
        assert info is not None

        shared = derive_shared_secret(CLIENT2_PRIVATE, CLIENT1_PUBLIC)
        result = decrypt_direct_message(info.payload, shared)
        assert result is not None
        assert result.message == DM_PLAINTEXT
        assert result.timestamp > 0


# ============================================================================
# Channel Message Decryption
# ============================================================================


class TestChannelDecryption:
    """Test channel message decryption using real captured packet data."""

    def test_parse_channel_packet_header(self):
        """Raw channel packet parses to GROUP_TEXT."""
        info = parse_packet(CHANNEL_PACKET)
        assert info is not None
        assert info.payload_type == PayloadType.GROUP_TEXT

    def test_decrypt_channel_message(self):
        """Channel message decrypts to expected sender and body."""
        result = try_decrypt_packet_with_channel_key(CHANNEL_PACKET, CHANNEL_KEY)
        assert result is not None
        assert result.sender == CHANNEL_SENDER
        assert result.message == CHANNEL_MESSAGE_BODY

    def test_full_text_reconstructed(self):
        """Reconstructed 'sender: message' matches the original plaintext."""
        result = try_decrypt_packet_with_channel_key(CHANNEL_PACKET, CHANNEL_KEY)
        assert result is not None
        full = f"{result.sender}: {result.message}"
        assert full == CHANNEL_PLAINTEXT_FULL

    def test_channel_hash_matches_packet(self):
        """Channel hash in packet matches hash computed from key."""
        info = parse_packet(CHANNEL_PACKET)
        assert info is not None
        packet_hash = format(info.payload[0], "02x")
        expected_hash = format(sha256(CHANNEL_KEY).digest()[0], "02x")
        assert packet_hash == expected_hash

    def test_wrong_channel_key_fails(self):
        """Decryption with a different channel key returns None."""
        wrong_key = b"\x00" * 16
        result = try_decrypt_packet_with_channel_key(CHANNEL_PACKET, wrong_key)
        assert result is None

    def test_hashtag_key_derivation(self):
        """Hashtag channel key is SHA-256(name)[:16], matching radio firmware."""
        key = sha256(b"#six77").digest()[:16]
        assert len(key) == 16
        # Key should decrypt our packet
        result = try_decrypt_packet_with_channel_key(CHANNEL_PACKET, key)
        assert result is not None

    def test_parse_multibyte_channel_packet_with_3byte_hops(self):
        """Real #bot packet with path_len=0x83 parses as 3 hops × 3 bytes."""
        info = parse_packet(BOT_PACKET_3BYTE_3HOPS)
        assert info is not None
        assert info.route_type == RouteType.FLOOD
        assert info.payload_type == PayloadType.GROUP_TEXT
        assert info.path_hash_size == 3
        assert info.path_length == 3
        assert info.path.hex() == BOT_PACKET_3BYTE_3HOPS_PATH_HEX

    def test_decrypt_multibyte_channel_packet_with_3byte_hops(self):
        """Real #bot packet with 3-byte hop identifiers decrypts correctly."""
        result = try_decrypt_packet_with_channel_key(BOT_PACKET_3BYTE_3HOPS, BOT_CHANNEL_KEY)
        assert result is not None
        assert result.sender == BOT_PACKET_3BYTE_3HOPS_SENDER
        assert result.message == BOT_PACKET_3BYTE_3HOPS_MESSAGE
        assert f"{result.sender}: {result.message}" == BOT_PACKET_3BYTE_3HOPS_FULL

    def test_parse_multibyte_channel_packet_with_2byte_zero_hops(self):
        """Real #bot packet with path_len=0x40 keeps hash-size=2 despite zero hops."""
        info = parse_packet(BOT_PACKET_2BYTE_0HOPS)
        assert info is not None
        assert info.route_type == RouteType.FLOOD
        assert info.payload_type == PayloadType.GROUP_TEXT
        assert info.path_hash_size == 2
        assert info.path_length == 0
        assert info.path == b""

    def test_decrypt_multibyte_channel_packet_with_2byte_zero_hops(self):
        """Real #bot packet with zero-hop 2-byte mode decrypts correctly."""
        result = try_decrypt_packet_with_channel_key(BOT_PACKET_2BYTE_0HOPS, BOT_CHANNEL_KEY)
        assert result is not None
        assert result.sender == BOT_PACKET_2BYTE_0HOPS_SENDER
        assert result.message == BOT_PACKET_2BYTE_0HOPS_MESSAGE
        assert f"{result.sender}: {result.message}" == BOT_PACKET_2BYTE_0HOPS_FULL


# ============================================================================
# Historical DM Decryption Pipeline (Integration)
# ============================================================================


class TestHistoricalDMDecryptionPipeline:
    """Integration test: store a real DM packet, run historical decryption,
    verify correct message and direction end up in the DB."""

    @pytest.mark.asyncio
    async def test_historical_decrypt_stores_incoming_dm(self, test_db, captured_broadcasts):
        """run_historical_dm_decryption decrypts a real packet and stores it
        with the correct direction (incoming from client1 to client2)."""
        from app.packet_processor import run_historical_dm_decryption

        # Store the undecrypted raw packet (message_id=NULL means undecrypted)
        pkt_id, _ = await RawPacketRepository.create(DM_PACKET, 1700000000)

        # Add client1 as a known contact
        await ContactRepository.upsert(
            {
                "public_key": CLIENT1_PUBLIC_HEX,
                "name": "Client1",
                "type": 1,
            }
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            # Decrypt as client2 (the receiver)
            await run_historical_dm_decryption(
                private_key_bytes=CLIENT2_PRIVATE,
                contact_public_key_bytes=CLIENT1_PUBLIC,
                contact_public_key_hex=CLIENT1_PUBLIC_HEX,
                display_name="Client1",
            )

        # Verify the message was stored
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=CLIENT1_PUBLIC_HEX.lower(), limit=10
        )
        assert len(messages) == 1

        msg = messages[0]
        assert msg.text == DM_PLAINTEXT
        assert msg.outgoing is False  # We are client2, message is FROM client1
        assert msg.type == "PRIV"

        # Verify a message broadcast was sent
        msg_broadcasts = [b for b in broadcasts if b["type"] == "message"]
        assert len(msg_broadcasts) == 1
        assert msg_broadcasts[0]["data"]["text"] == DM_PLAINTEXT
        assert msg_broadcasts[0]["data"]["outgoing"] is False

    @pytest.mark.asyncio
    async def test_historical_decrypt_skips_outgoing_by_design(self, test_db, captured_broadcasts):
        """Historical decryption skips outgoing DMs (they're stored by the send endpoint).

        run_historical_dm_decryption passes our_public_key=None, which disables
        the outbound hash check. When our first byte differs from the contact's
        (255/256 cases), outgoing packets fail the inbound src_hash check and
        are skipped — this is correct behavior.
        """
        from app.packet_processor import run_historical_dm_decryption

        await RawPacketRepository.create(DM_PACKET, 1700000000)

        await ContactRepository.upsert(
            {
                "public_key": CLIENT2_PUBLIC_HEX,
                "name": "Client2",
                "type": 1,
            }
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            # Decrypt as client1 (the sender) — first bytes differ (a1 != fa)
            # so historical decryption correctly skips this outgoing packet
            await run_historical_dm_decryption(
                private_key_bytes=CLIENT1_PRIVATE,
                contact_public_key_bytes=CLIENT2_PUBLIC,
                contact_public_key_hex=CLIENT2_PUBLIC_HEX,
                display_name="Client2",
            )

        # No messages stored — outgoing DMs are handled by the send endpoint
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=CLIENT2_PUBLIC_HEX.lower(), limit=10
        )
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_historical_decrypt_broadcasts_success(self, test_db, captured_broadcasts):
        """Successful decryption broadcasts a success notification."""
        from app.packet_processor import run_historical_dm_decryption

        await RawPacketRepository.create(DM_PACKET, 1700000000)

        await ContactRepository.upsert(
            {
                "public_key": CLIENT1_PUBLIC_HEX,
                "name": "Client1",
                "type": 1,
            }
        )

        broadcasts, mock_broadcast = captured_broadcasts

        from unittest.mock import MagicMock

        mock_success = MagicMock()

        with (
            patch("app.packet_processor.broadcast_event", mock_broadcast),
            patch("app.websocket.broadcast_success", mock_success),
        ):
            await run_historical_dm_decryption(
                private_key_bytes=CLIENT2_PRIVATE,
                contact_public_key_bytes=CLIENT1_PUBLIC,
                contact_public_key_hex=CLIENT1_PUBLIC_HEX,
                display_name="Client1",
            )

        mock_success.assert_called_once()
        args = mock_success.call_args.args
        assert "Client1" in args[0]
        assert "1 message" in args[1]


class TestLiveDMDecryptionPipeline:
    """Integration test: process a real DM packet through the live
    process_raw_packet → _process_direct_message → try_decrypt_dm pipeline
    with no crypto mocking."""

    @pytest.fixture(autouse=True)
    def _reset_keystore(self):
        """Reset the global keystore state between tests."""
        import app.keystore as ks

        orig_priv, orig_pub = ks._private_key, ks._public_key
        ks._private_key = None
        ks._public_key = None
        yield
        ks._private_key, ks._public_key = orig_priv, orig_pub

    @pytest.mark.asyncio
    async def test_process_dm_packet_end_to_end(self, test_db, captured_broadcasts):
        """process_raw_packet decrypts a real DM packet and stores the message
        with correct text, direction, and contact attribution."""
        from app.keystore import set_private_key

        # Set up: client2 is "us" (receiver), client1 is the sender
        set_private_key(CLIENT2_PRIVATE)

        # Register client1 as a known contact
        await ContactRepository.upsert(
            {
                "public_key": CLIENT1_PUBLIC_HEX,
                "name": "Client1",
                "type": 1,
            }
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            from app.packet_processor import process_raw_packet

            result = await process_raw_packet(raw_bytes=DM_PACKET)

        # Verify process_raw_packet reports successful decryption
        assert result is not None
        assert result["decrypted"] is True
        assert result["contact_name"] == "Client1"
        assert result["message_id"] is not None

        # Verify message stored in DB with correct fields
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=CLIENT1_PUBLIC_HEX.lower(), limit=10
        )
        assert len(messages) == 1
        msg = messages[0]
        assert msg.text == DM_PLAINTEXT
        assert msg.outgoing is False  # We are client2, message is FROM client1
        assert msg.type == "PRIV"

        # Verify a "message" broadcast was sent
        msg_broadcasts = [b for b in broadcasts if b["type"] == "message"]
        assert len(msg_broadcasts) == 1
        assert msg_broadcasts[0]["data"]["text"] == DM_PLAINTEXT
        assert msg_broadcasts[0]["data"]["outgoing"] is False

    @pytest.mark.asyncio
    async def test_dm_from_unknown_contact_not_decrypted(self, test_db, captured_broadcasts):
        """DM from an unknown contact (not in DB) is stored but not decrypted."""
        from app.keystore import set_private_key

        set_private_key(CLIENT2_PRIVATE)
        # No contacts registered — client1 is unknown

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            from app.packet_processor import process_raw_packet

            result = await process_raw_packet(raw_bytes=DM_PACKET)

        # Raw packet is stored but not decrypted
        assert result is not None
        assert result["decrypted"] is False

        # No messages created (can't decrypt without knowing the contact)
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=CLIENT1_PUBLIC_HEX.lower(), limit=10
        )
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_dm_without_private_key_not_decrypted(self, test_db, captured_broadcasts):
        """Without a private key in the keystore, DMs are stored but not decrypted."""
        # Don't call set_private_key — keystore is empty

        await ContactRepository.upsert(
            {
                "public_key": CLIENT1_PUBLIC_HEX,
                "name": "Client1",
                "type": 1,
            }
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            from app.packet_processor import process_raw_packet

            result = await process_raw_packet(raw_bytes=DM_PACKET)

        assert result is not None
        assert result["decrypted"] is False

    @pytest.mark.asyncio
    async def test_dm_duplicate_packet_deduplicates(self, test_db, captured_broadcasts):
        """Processing the same DM packet twice doesn't create duplicate messages."""
        from app.keystore import set_private_key

        set_private_key(CLIENT2_PRIVATE)

        await ContactRepository.upsert(
            {
                "public_key": CLIENT1_PUBLIC_HEX,
                "name": "Client1",
                "type": 1,
            }
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            from app.packet_processor import process_raw_packet

            result1 = await process_raw_packet(raw_bytes=DM_PACKET)
            await process_raw_packet(raw_bytes=DM_PACKET)

        # First processing succeeds
        assert result1["decrypted"] is True
        assert result1["message_id"] is not None

        # Only one message stored (dedup via unique constraint)
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=CLIENT1_PUBLIC_HEX.lower(), limit=10
        )
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_historical_then_live_deduplicates(self, test_db, captured_broadcasts):
        """A DM decrypted historically and then received live doesn't duplicate."""
        from app.keystore import set_private_key
        from app.packet_processor import process_raw_packet, run_historical_dm_decryption

        set_private_key(CLIENT2_PRIVATE)

        await ContactRepository.upsert(
            {
                "public_key": CLIENT1_PUBLIC_HEX,
                "name": "Client1",
                "type": 1,
            }
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            # First: store packet undecrypted, then run historical decryption
            await RawPacketRepository.create(DM_PACKET, 1700000000)

            with patch("app.websocket.broadcast_success"):
                await run_historical_dm_decryption(
                    private_key_bytes=CLIENT2_PRIVATE,
                    contact_public_key_bytes=CLIENT1_PUBLIC,
                    contact_public_key_hex=CLIENT1_PUBLIC_HEX,
                    display_name="Client1",
                )

            # Then: same packet arrives again via live pipeline
            await process_raw_packet(raw_bytes=DM_PACKET)

        # Only one message stored despite both paths processing it
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=CLIENT1_PUBLIC_HEX.lower(), limit=10
        )
        assert len(messages) == 1
        assert messages[0].text == DM_PLAINTEXT


class TestHistoricalChannelDecryptionPipeline:
    """Integration test: store a real channel packet, process it through
    the channel message pipeline, verify correct message in DB."""

    @pytest.mark.asyncio
    async def test_process_channel_packet_end_to_end(self, test_db, captured_broadcasts):
        """process_raw_packet decrypts a real channel packet and stores
        the message with correct sender and text."""
        from app.repository import ChannelRepository

        # Register the #six77 channel
        channel_key_hex = CHANNEL_KEY.hex().upper()
        await ChannelRepository.upsert(key=channel_key_hex, name=CHANNEL_NAME, is_hashtag=True)

        # Store the raw packet and process it
        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            from app.packet_processor import process_raw_packet

            result = await process_raw_packet(raw_bytes=CHANNEL_PACKET)

        # Verify it was decrypted
        assert result is not None
        assert result["decrypted"] is True
        assert result["channel_name"] == CHANNEL_NAME
        assert result["sender"] == CHANNEL_SENDER

        # Verify message in DB
        messages = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=channel_key_hex, limit=10
        )
        assert len(messages) == 1
        assert messages[0].text == CHANNEL_PLAINTEXT_FULL

        # Verify a "message" broadcast was sent
        msg_broadcasts = [b for b in broadcasts if b["type"] == "message"]
        assert len(msg_broadcasts) == 1
        assert msg_broadcasts[0]["data"]["text"] == CHANNEL_PLAINTEXT_FULL

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("packet", "expected_text", "expected_sender"),
        [
            (
                BOT_PACKET_3BYTE_3HOPS,
                BOT_PACKET_3BYTE_3HOPS_FULL,
                BOT_PACKET_3BYTE_3HOPS_SENDER,
            ),
            (
                BOT_PACKET_2BYTE_0HOPS,
                BOT_PACKET_2BYTE_0HOPS_FULL,
                BOT_PACKET_2BYTE_0HOPS_SENDER,
            ),
        ],
    )
    async def test_process_multibyte_bot_channel_packets_end_to_end(
        self, test_db, captured_broadcasts, packet, expected_text, expected_sender
    ):
        """Real multibyte #bot packets decrypt and store correctly through the live pipeline."""
        from app.packet_processor import process_raw_packet
        from app.repository import ChannelRepository

        channel_key_hex = BOT_CHANNEL_KEY.hex().upper()
        await ChannelRepository.upsert(key=channel_key_hex, name=BOT_CHANNEL_NAME, is_hashtag=True)

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            result = await process_raw_packet(raw_bytes=packet)

        assert result is not None
        assert result["decrypted"] is True
        assert result["channel_name"] == BOT_CHANNEL_NAME
        assert result["sender"] == expected_sender

        messages = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=channel_key_hex, limit=10
        )
        assert len(messages) == 1
        assert messages[0].text == expected_text

        msg_broadcasts = [b for b in broadcasts if b["type"] == "message"]
        assert len(msg_broadcasts) == 1
        assert msg_broadcasts[0]["data"]["text"] == expected_text
