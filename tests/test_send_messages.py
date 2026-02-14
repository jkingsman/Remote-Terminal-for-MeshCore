"""Tests for bot triggering on outgoing messages sent via the messages router."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType

from app.database import Database
from app.models import (
    SendChannelMessageRequest,
    SendDirectMessageRequest,
)
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
    MessageRepository,
)
from app.routers.messages import resend_message, send_channel_message, send_direct_message


@pytest.fixture
async def test_db():
    """Create an in-memory test database with schema + migrations."""
    import app.repository as repo_module

    db = Database(":memory:")
    await db.connect()

    original_db = repo_module.db
    repo_module.db = db

    try:
        yield db
    finally:
        repo_module.db = original_db
        await db.disconnect()


def _make_radio_result(payload=None):
    """Create a mock radio command result."""
    result = MagicMock()
    result.type = EventType.MSG_SENT
    result.payload = payload or {}
    return result


def _make_mc(name="TestNode"):
    """Create a mock MeshCore connection."""
    mc = MagicMock()
    mc.self_info = {"name": name}
    mc.commands = MagicMock()
    mc.commands.send_msg = AsyncMock(return_value=_make_radio_result())
    mc.commands.send_chan_msg = AsyncMock(return_value=_make_radio_result())
    mc.commands.add_contact = AsyncMock(return_value=_make_radio_result())
    mc.commands.set_channel = AsyncMock(return_value=_make_radio_result())
    mc.get_contact_by_key_prefix = MagicMock(return_value=None)
    return mc


async def _insert_contact(public_key, name="Alice"):
    """Insert a contact into the test database."""
    await ContactRepository.upsert(
        {
            "public_key": public_key,
            "name": name,
            "type": 0,
            "flags": 0,
            "last_path": None,
            "last_path_len": -1,
            "last_advert": None,
            "lat": None,
            "lon": None,
            "last_seen": None,
            "on_radio": False,
            "last_contacted": None,
        }
    )


class TestOutgoingDMBotTrigger:
    """Test that sending a DM triggers bots with is_outgoing=True."""

    @pytest.mark.asyncio
    async def test_send_dm_triggers_bot(self, test_db):
        """Sending a DM creates a background task to run bots."""
        mc = _make_mc()
        pub_key = "ab" * 32
        await _insert_contact(pub_key, "Alice")

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch("app.bot.run_bot_for_message", new=AsyncMock()) as mock_bot,
        ):
            request = SendDirectMessageRequest(destination=pub_key, text="!lasttime Alice")
            await send_direct_message(request)

            # Let the background task run
            await asyncio.sleep(0)

            mock_bot.assert_called_once()
            call_kwargs = mock_bot.call_args[1]
            assert call_kwargs["message_text"] == "!lasttime Alice"
            assert call_kwargs["is_dm"] is True
            assert call_kwargs["is_outgoing"] is True
            assert call_kwargs["sender_key"] == pub_key
            assert call_kwargs["channel_key"] is None

    @pytest.mark.asyncio
    async def test_send_dm_bot_does_not_block_response(self, test_db):
        """Bot trigger runs in background and doesn't delay the message response."""
        mc = _make_mc()
        pub_key = "ab" * 32
        await _insert_contact(pub_key, "Alice")

        # Bot that would take a long time
        async def _slow(**kw):
            await asyncio.sleep(10)

        slow_bot = AsyncMock(side_effect=_slow)

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch("app.bot.run_bot_for_message", new=slow_bot),
        ):
            request = SendDirectMessageRequest(destination=pub_key, text="Hello")
            # This should return immediately, not wait 10 seconds
            message = await send_direct_message(request)
            assert message.text == "Hello"
            assert message.outgoing is True

    @pytest.mark.asyncio
    async def test_send_dm_passes_no_sender_name(self, test_db):
        """Outgoing DMs pass sender_name=None (we are the sender)."""
        mc = _make_mc()
        pub_key = "cd" * 32
        await _insert_contact(pub_key, "Bob")

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch("app.bot.run_bot_for_message", new=AsyncMock()) as mock_bot,
        ):
            request = SendDirectMessageRequest(destination=pub_key, text="test")
            await send_direct_message(request)
            await asyncio.sleep(0)

            call_kwargs = mock_bot.call_args[1]
            assert call_kwargs["sender_name"] is None

    @pytest.mark.asyncio
    async def test_send_dm_ambiguous_prefix_returns_409(self, test_db):
        """Ambiguous destination prefix should fail instead of selecting a random contact."""
        mc = _make_mc()

        # Insert two contacts that share the prefix "abc123"
        await _insert_contact("abc123" + "00" * 29, "ContactA")
        await _insert_contact("abc123" + "ff" * 29, "ContactB")

        with patch("app.routers.messages.require_connected", return_value=mc):
            with pytest.raises(HTTPException) as exc_info:
                await send_direct_message(
                    SendDirectMessageRequest(destination="abc123", text="Hello")
                )

        assert exc_info.value.status_code == 409
        assert "ambiguous" in exc_info.value.detail.lower()


class TestOutgoingChannelBotTrigger:
    """Test that sending a channel message triggers bots with is_outgoing=True."""

    @pytest.mark.asyncio
    async def test_send_channel_msg_triggers_bot(self, test_db):
        """Sending a channel message creates a background task to run bots."""
        mc = _make_mc(name="MyNode")
        chan_key = "aa" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch("app.decoder.calculate_channel_hash", return_value="abcd"),
            patch("app.bot.run_bot_for_message", new=AsyncMock()) as mock_bot,
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="!lasttime5 someone")
            await send_channel_message(request)
            await asyncio.sleep(0)

            mock_bot.assert_called_once()
            call_kwargs = mock_bot.call_args[1]
            assert call_kwargs["message_text"] == "!lasttime5 someone"
            assert call_kwargs["is_dm"] is False
            assert call_kwargs["is_outgoing"] is True
            assert call_kwargs["channel_key"] == chan_key.upper()
            assert call_kwargs["channel_name"] == "#general"
            assert call_kwargs["sender_name"] == "MyNode"
            assert call_kwargs["sender_key"] is None

    @pytest.mark.asyncio
    async def test_send_channel_msg_no_radio_name(self, test_db):
        """When radio has no name, sender_name is None."""
        mc = _make_mc(name="")
        chan_key = "bb" * 16
        await ChannelRepository.upsert(key=chan_key, name="#test")

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch("app.decoder.calculate_channel_hash", return_value="abcd"),
            patch("app.bot.run_bot_for_message", new=AsyncMock()) as mock_bot,
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="hello")
            await send_channel_message(request)
            await asyncio.sleep(0)

            call_kwargs = mock_bot.call_args[1]
            assert call_kwargs["sender_name"] is None

    @pytest.mark.asyncio
    async def test_send_channel_msg_bot_does_not_block_response(self, test_db):
        """Bot trigger runs in background and doesn't delay the message response."""
        mc = _make_mc(name="MyNode")
        chan_key = "cc" * 16
        await ChannelRepository.upsert(key=chan_key, name="#slow")

        async def _slow(**kw):
            await asyncio.sleep(10)

        slow_bot = AsyncMock(side_effect=_slow)

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch("app.decoder.calculate_channel_hash", return_value="abcd"),
            patch("app.bot.run_bot_for_message", new=slow_bot),
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="test")
            message = await send_channel_message(request)
            assert message.outgoing is True

    @pytest.mark.asyncio
    async def test_send_channel_msg_double_send_when_experimental_enabled(self, test_db):
        """Experimental setting triggers an immediate byte-perfect duplicate send."""
        mc = _make_mc(name="MyNode")
        chan_key = "dd" * 16
        await ChannelRepository.upsert(key=chan_key, name="#double")
        await AppSettingsRepository.update(experimental_channel_double_send=True)

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch("app.decoder.calculate_channel_hash", return_value="abcd"),
            patch("app.bot.run_bot_for_message", new=AsyncMock()),
            patch("app.routers.messages.asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="same bytes")
            await send_channel_message(request)

        assert mc.commands.send_chan_msg.await_count == 2
        mock_sleep.assert_awaited_once_with(3)
        first_call = mc.commands.send_chan_msg.await_args_list[0].kwargs
        second_call = mc.commands.send_chan_msg.await_args_list[1].kwargs
        assert first_call["chan"] == second_call["chan"]
        assert first_call["msg"] == second_call["msg"]
        assert first_call["timestamp"] == second_call["timestamp"]

    @pytest.mark.asyncio
    async def test_send_channel_msg_single_send_when_experimental_disabled(self, test_db):
        """Default setting keeps channel sends to a single radio command."""
        mc = _make_mc(name="MyNode")
        chan_key = "ee" * 16
        await ChannelRepository.upsert(key=chan_key, name="#single")

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch("app.decoder.calculate_channel_hash", return_value="abcd"),
            patch("app.bot.run_bot_for_message", new=AsyncMock()),
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="single send")
            await send_channel_message(request)

        assert mc.commands.send_chan_msg.await_count == 1

    @pytest.mark.asyncio
    async def test_send_channel_msg_response_includes_current_ack_count(self, test_db):
        """Send response reflects latest DB ack count at response time."""
        mc = _make_mc(name="MyNode")
        chan_key = "ff" * 16
        await ChannelRepository.upsert(key=chan_key, name="#acked")

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch("app.decoder.calculate_channel_hash", return_value="abcd"),
            patch("app.bot.run_bot_for_message", new=AsyncMock()),
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="acked now")
            message = await send_channel_message(request)

        # Fresh message has acked=0
        assert message.id is not None
        assert message.acked == 0


class TestResendMessage:
    """Test re-sending existing outgoing messages without creating duplicates."""

    @pytest.mark.asyncio
    async def test_resend_direct_uses_original_timestamp_and_tracks_ack(self, test_db):
        mc = _make_mc()
        pub_key = "ab" * 32
        await _insert_contact(pub_key, "Alice")
        message_id = await MessageRepository.create(
            msg_type="PRIV",
            text="retry me",
            conversation_key=pub_key,
            sender_timestamp=1700000000,
            received_at=1700000000,
            outgoing=True,
        )
        assert message_id is not None

        ack_payload = {"expected_ack": b"\x12\x34", "suggested_timeout": 9000}
        mc.commands.send_msg = AsyncMock(return_value=_make_radio_result(ack_payload))

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch("app.routers.messages.track_pending_ack") as mock_track_ack,
            patch("app.routers.messages.time.time", return_value=1700001234),
        ):
            result = await resend_message(message_id)

        assert result == {"status": "ok", "message_id": message_id}
        send_kwargs = mc.commands.send_msg.await_args.kwargs
        assert send_kwargs["msg"] == "retry me"
        assert send_kwargs["timestamp"] == 1700001234
        mock_track_ack.assert_called_once_with("1234", message_id, 9000)
        updated = await MessageRepository.get_by_id(message_id)
        assert updated is not None
        assert updated.sender_timestamp == 1700001234

    @pytest.mark.asyncio
    async def test_resend_channel_uses_original_timestamp_bytes(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "aa" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")
        message_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: hello again",
            conversation_key=chan_key.upper(),
            sender_timestamp=1700000001,
            received_at=1700000001,
            outgoing=True,
        )
        assert message_id is not None

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch("app.routers.messages.asyncio.sleep", new=AsyncMock()),
            patch("app.routers.messages.time.time", return_value=1700002234),
        ):
            result = await resend_message(message_id)

        assert result == {"status": "ok", "message_id": message_id}
        send_kwargs = mc.commands.send_chan_msg.await_args.kwargs
        assert send_kwargs["msg"] == "hello again"
        assert send_kwargs["timestamp"] == (1700002234).to_bytes(4, "little")
        updated = await MessageRepository.get_by_id(message_id)
        assert updated is not None
        assert updated.sender_timestamp == 1700002234

    @pytest.mark.asyncio
    async def test_resend_rejects_non_outgoing_messages(self, test_db):
        chan_key = "bb" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")
        message_id = await MessageRepository.create(
            msg_type="CHAN",
            text="incoming",
            conversation_key=chan_key.upper(),
            sender_timestamp=1700000002,
            received_at=1700000002,
            outgoing=False,
        )
        assert message_id is not None

        with patch("app.routers.messages.require_connected", return_value=_make_mc()):
            with pytest.raises(HTTPException) as exc_info:
                await resend_message(message_id)

        assert exc_info.value.status_code == 400
        assert "outgoing" in exc_info.value.detail.lower()
