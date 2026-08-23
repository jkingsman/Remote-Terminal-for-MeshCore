"""Server-side MCMP decode on ingest.

Compressed bodies arrive as ordinary text behind an ``mcmp2:``/``mcmp3:`` prefix.
These tests prove the shared channel and DM ingest points decode them to
plaintext before storage, so the DB (and therefore search, bots and the UI) see
the real message — while non-MCMP text passes through untouched.
"""

import pytest

from app.compression.mcmp import MeshCompressor, encode_v3_text
from app.repository import MessageRepository, RawPacketRepository

CHANNEL_KEY = "ABC123DEF456ABC123DEF456ABC12345"
CONTACT_PUB = "a1b2c3d3ba9f5fa8705b9845fe11cc6f01d1d49caaf4d122ac7121663c5beec7"
SENDER_TIMESTAMP = 1700000000

# Long enough to actually compress (short strings are sent as plaintext).
LONG_TEXT = "Battery at 40%, switching to power save and checking channel five for traffic."


@pytest.fixture(scope="module")
def compressor() -> MeshCompressor:
    c = MeshCompressor()
    c.load_from_path()
    return c


class TestChannelIngestDecode:
    @pytest.mark.asyncio
    async def test_v2_channel_body_is_decoded(self, test_db, compressor):
        from app.packet_processor import create_message_from_decrypted

        wire = compressor.encode_if_smaller(LONG_TEXT)
        assert wire.startswith("mcmp2:")  # guard: the sample really compressed

        packet_id, _ = await RawPacketRepository.create(b"chan_v2", SENDER_TIMESTAMP)

        msg_id = await create_message_from_decrypted(
            packet_id=packet_id,
            channel_key=CHANNEL_KEY,
            sender="Alice",
            message_text=wire,
            timestamp=SENDER_TIMESTAMP,
        )
        assert msg_id is not None
        stored = await MessageRepository.get_by_id(msg_id)
        assert stored is not None
        assert stored.text == f"Alice: {LONG_TEXT}"

    @pytest.mark.asyncio
    async def test_v3_channel_body_is_decoded(self, test_db, compressor):
        from app.packet_processor import create_message_from_decrypted

        wire = encode_v3_text(compressor, LONG_TEXT, timestamp=SENDER_TIMESTAMP)
        assert wire.startswith("mcmp3:")

        packet_id, _ = await RawPacketRepository.create(b"chan_v3", SENDER_TIMESTAMP)

        msg_id = await create_message_from_decrypted(
            packet_id=packet_id,
            channel_key=CHANNEL_KEY,
            sender="Bob",
            message_text=wire,
            timestamp=SENDER_TIMESTAMP,
        )
        assert msg_id is not None
        stored = await MessageRepository.get_by_id(msg_id)
        assert stored is not None
        assert stored.text == f"Bob: {LONG_TEXT}"

    @pytest.mark.asyncio
    async def test_plain_channel_body_passthrough(self, test_db, compressor):
        from app.packet_processor import create_message_from_decrypted

        packet_id, _ = await RawPacketRepository.create(b"chan_plain", SENDER_TIMESTAMP)

        msg_id = await create_message_from_decrypted(
            packet_id=packet_id,
            channel_key=CHANNEL_KEY,
            sender="Carol",
            message_text="just a normal message",
            timestamp=SENDER_TIMESTAMP,
        )
        assert msg_id is not None
        stored = await MessageRepository.get_by_id(msg_id)
        assert stored is not None
        assert stored.text == "Carol: just a normal message"


class TestDirectMessageIngestDecode:
    @pytest.mark.asyncio
    async def test_v2_dm_body_is_decoded(self, test_db, captured_broadcasts, compressor):
        from app.services.dm_ingest import ingest_fallback_direct_message

        _, mock_broadcast = captured_broadcasts
        wire = compressor.encode_if_smaller(LONG_TEXT)
        assert wire.startswith("mcmp2:")

        message = await ingest_fallback_direct_message(
            conversation_key=CONTACT_PUB,
            text=wire,
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP,
            path=None,
            path_len=None,
            txt_type=0,
            signature=None,
            sender_name="Peer",
            sender_key=CONTACT_PUB,
            broadcast_fn=mock_broadcast,
        )
        assert message is not None
        assert message.text == LONG_TEXT

    @pytest.mark.asyncio
    async def test_v3_dm_body_is_decoded(self, test_db, captured_broadcasts, compressor):
        from app.services.dm_ingest import ingest_fallback_direct_message

        _, mock_broadcast = captured_broadcasts
        wire = encode_v3_text(compressor, LONG_TEXT, timestamp=SENDER_TIMESTAMP)
        assert wire.startswith("mcmp3:")

        message = await ingest_fallback_direct_message(
            conversation_key=CONTACT_PUB,
            text=wire,
            sender_timestamp=SENDER_TIMESTAMP + 1,
            received_at=SENDER_TIMESTAMP + 1,
            path=None,
            path_len=None,
            txt_type=0,
            signature=None,
            sender_name="Peer",
            sender_key=CONTACT_PUB,
            broadcast_fn=mock_broadcast,
        )
        assert message is not None
        assert message.text == LONG_TEXT

    @pytest.mark.asyncio
    async def test_plain_dm_body_passthrough(self, test_db, captured_broadcasts, compressor):
        from app.services.dm_ingest import ingest_fallback_direct_message

        _, mock_broadcast = captured_broadcasts
        message = await ingest_fallback_direct_message(
            conversation_key=CONTACT_PUB,
            text="hello there",
            sender_timestamp=SENDER_TIMESTAMP + 2,
            received_at=SENDER_TIMESTAMP + 2,
            path=None,
            path_len=None,
            txt_type=0,
            signature=None,
            sender_name="Peer",
            sender_key=CONTACT_PUB,
            broadcast_fn=mock_broadcast,
        )
        assert message is not None
        assert message.text == "hello there"
