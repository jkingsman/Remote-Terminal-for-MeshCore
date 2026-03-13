"""Tests for outgoing message sending via the messages router."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType

from app.models import (
    SendChannelMessageRequest,
    SendDirectMessageRequest,
)
from app.radio import radio_manager
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
    MessageRepository,
)
from app.routers.messages import (
    resend_channel_message,
    send_channel_message,
    send_direct_message,
)


@pytest.fixture(autouse=True)
def _reset_radio_state():
    """Save/restore radio_manager state so tests don't leak."""
    prev = radio_manager._meshcore
    prev_lock = radio_manager._operation_lock
    prev_max_channels = radio_manager.max_channels
    prev_connection_info = radio_manager._connection_info
    prev_slot_by_key = radio_manager._channel_slot_by_key.copy()
    prev_key_by_slot = radio_manager._channel_key_by_slot.copy()
    yield
    radio_manager._meshcore = prev
    radio_manager._operation_lock = prev_lock
    radio_manager.max_channels = prev_max_channels
    radio_manager._connection_info = prev_connection_info
    radio_manager._channel_slot_by_key = prev_slot_by_key
    radio_manager._channel_key_by_slot = prev_key_by_slot


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
    mc.commands.set_flood_scope = AsyncMock(return_value=_make_radio_result())
    mc.commands.send_msg = AsyncMock(return_value=_make_radio_result())
    mc.commands.send_chan_msg = AsyncMock(return_value=_make_radio_result())
    mc.commands.add_contact = AsyncMock(return_value=_make_radio_result())
    mc.commands.set_channel = AsyncMock(return_value=_make_radio_result())
    mc.get_contact_by_key_prefix = MagicMock(return_value=None)
    return mc


async def _insert_contact(public_key, name="Alice", **overrides):
    """Insert a contact into the test database."""
    data = {
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
    data.update(overrides)
    await ContactRepository.upsert(data)


class TestOutgoingDMBroadcast:
    """Test that outgoing DMs are broadcast via broadcast_event for fanout dispatch."""

    @pytest.mark.asyncio
    async def test_send_dm_broadcasts_outgoing(self, test_db):
        """Sending a DM broadcasts the message with outgoing=True for fanout dispatch."""
        mc = _make_mc()
        pub_key = "ab" * 32
        await _insert_contact(pub_key, "Alice")

        broadcasts = []

        def capture_broadcast(event_type, data):
            broadcasts.append({"type": event_type, "data": data})

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event", side_effect=capture_broadcast),
        ):
            request = SendDirectMessageRequest(destination=pub_key, text="!lasttime Alice")
            await send_direct_message(request)

        msg_broadcasts = [b for b in broadcasts if b["type"] == "message"]
        assert len(msg_broadcasts) == 1
        data = msg_broadcasts[0]["data"]
        assert data["text"] == "!lasttime Alice"
        assert data["outgoing"] is True
        assert data["type"] == "PRIV"
        assert data["conversation_key"] == pub_key

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

    @pytest.mark.asyncio
    async def test_send_dm_preserves_stored_out_path_hash_mode(self, test_db):
        """Direct-message send pushes the persisted path hash mode back to the radio."""
        mc = _make_mc()
        pub_key = "cd" * 32
        await _insert_contact(
            pub_key,
            "Alice",
            last_path="aa00bb00",
            last_path_len=2,
            out_path_hash_mode=1,
        )

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendDirectMessageRequest(destination=pub_key, text="Hello")
            await send_direct_message(request)

        contact_payload = mc.commands.add_contact.call_args.args[0]
        assert contact_payload["public_key"] == pub_key
        assert contact_payload["out_path"] == "aa00bb00"
        assert contact_payload["out_path_len"] == 2
        assert contact_payload["out_path_hash_mode"] == 1

    @pytest.mark.asyncio
    async def test_send_dm_prefers_route_override_over_learned_path(self, test_db):
        mc = _make_mc()
        pub_key = "ef" * 32
        await _insert_contact(
            pub_key,
            "Alice",
            last_path="aabb",
            last_path_len=1,
            out_path_hash_mode=0,
            route_override_path="cc00dd00",
            route_override_len=2,
            route_override_hash_mode=1,
        )

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendDirectMessageRequest(destination=pub_key, text="Hello")
            await send_direct_message(request)

        contact_payload = mc.commands.add_contact.call_args.args[0]
        assert contact_payload["out_path"] == "cc00dd00"
        assert contact_payload["out_path_len"] == 2
        assert contact_payload["out_path_hash_mode"] == 1


class TestOutgoingChannelBroadcast:
    """Test that outgoing channel messages are broadcast via broadcast_event for fanout dispatch."""

    @pytest.mark.asyncio
    async def test_send_channel_msg_broadcasts_outgoing(self, test_db):
        """Sending a channel message broadcasts with outgoing=True for fanout dispatch."""
        mc = _make_mc(name="MyNode")
        chan_key = "aa" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")

        broadcasts = []

        def capture_broadcast(event_type, data):
            broadcasts.append({"type": event_type, "data": data})

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event", side_effect=capture_broadcast),
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="!lasttime5 someone")
            await send_channel_message(request)

        msg_broadcasts = [b for b in broadcasts if b["type"] == "message"]
        assert len(msg_broadcasts) == 1
        data = msg_broadcasts[0]["data"]
        assert data["outgoing"] is True
        assert data["type"] == "CHAN"
        assert data["conversation_key"] == chan_key.upper()
        assert data["sender_name"] == "MyNode"
        assert data["channel_name"] == "#general"

    @pytest.mark.asyncio
    async def test_send_channel_msg_response_includes_current_ack_count(self, test_db):
        """Send response reflects latest DB ack count at response time."""
        mc = _make_mc(name="MyNode")
        chan_key = "ff" * 16
        await ChannelRepository.upsert(key=chan_key, name="#acked")

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="acked now")
            message = await send_channel_message(request)

        # Fresh message has acked=0
        assert message.id is not None
        assert message.acked == 0
        assert message.channel_name == "#acked"

    @pytest.mark.asyncio
    async def test_send_channel_msg_includes_sender_key(self, test_db):
        """Outgoing channel message includes our public key as sender_key."""
        our_pubkey = "ab" * 32
        mc = _make_mc(name="MyNode")
        mc.self_info["public_key"] = our_pubkey
        chan_key = "ee" * 16
        await ChannelRepository.upsert(key=chan_key, name="#test")

        broadcasts = []

        def capture_broadcast(event_type, data):
            broadcasts.append({"type": event_type, "data": data})

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event", side_effect=capture_broadcast),
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="hello")
            message = await send_channel_message(request)

        # Response message includes sender_key
        assert message.sender_key == our_pubkey
        assert message.sender_name == "MyNode"

        # Broadcast also includes sender_key
        msg_broadcasts = [b for b in broadcasts if b["type"] == "message"]
        assert len(msg_broadcasts) == 1
        assert msg_broadcasts[0]["data"]["sender_key"] == our_pubkey

        # DB row also has sender_key
        db_msg = await MessageRepository.get_by_id(message.id)
        assert db_msg is not None
        assert db_msg.sender_key == our_pubkey

    @pytest.mark.asyncio
    async def test_send_channel_msg_uses_channel_flood_scope_override(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "de" * 16
        await ChannelRepository.upsert(key=chan_key, name="#flightless")
        await ChannelRepository.update_flood_scope_override(chan_key, "Esperance")
        await AppSettingsRepository.update(flood_scope="Baseline")

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="hello")
            await send_channel_message(request)

        assert mc.commands.set_flood_scope.await_args_list == [
            call("#Esperance"),
            call("#Baseline"),
        ]

    @pytest.mark.asyncio
    async def test_send_channel_msg_skips_temporary_scope_when_override_matches_global(
        self, test_db
    ):
        mc = _make_mc(name="MyNode")
        chan_key = "df" * 16
        await ChannelRepository.upsert(key=chan_key, name="#matching")
        await ChannelRepository.update_flood_scope_override(chan_key, "Esperance")
        await AppSettingsRepository.update(flood_scope="Esperance")

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="hello")
            await send_channel_message(request)

        mc.commands.set_flood_scope.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_channel_msg_aborts_when_override_apply_fails(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "a1" * 16
        await ChannelRepository.upsert(key=chan_key, name="#flightless")
        await ChannelRepository.update_flood_scope_override(chan_key, "#Esperance")
        await AppSettingsRepository.update(flood_scope="#Baseline")
        mc.commands.set_flood_scope = AsyncMock(
            return_value=MagicMock(type=EventType.ERROR, payload="unsupported")
        )

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            pytest.raises(HTTPException) as exc_info,
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="hello")
            await send_channel_message(request)

        assert exc_info.value.status_code == 500
        assert "regional override" in exc_info.value.detail.lower()
        mc.commands.set_channel.assert_not_awaited()
        mc.commands.send_chan_msg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_channel_msg_reuses_cached_slot_for_same_channel(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "b1" * 16
        await ChannelRepository.upsert(key=chan_key, name="#cached")
        radio_manager.max_channels = 4
        radio_manager._connection_info = "Serial: /dev/ttyUSB0"

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="first send")
            )
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="second send")
            )

        assert mc.commands.set_channel.await_count == 1
        assert mc.commands.send_chan_msg.await_count == 2
        assert [call.kwargs["chan"] for call in mc.commands.send_chan_msg.await_args_list] == [0, 0]

    @pytest.mark.asyncio
    async def test_send_channel_msg_uses_lru_slot_eviction(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key_a = "c1" * 16
        chan_key_b = "c2" * 16
        chan_key_c = "c3" * 16
        await ChannelRepository.upsert(key=chan_key_a, name="#alpha")
        await ChannelRepository.upsert(key=chan_key_b, name="#bravo")
        await ChannelRepository.upsert(key=chan_key_c, name="#charlie")
        radio_manager.max_channels = 2
        radio_manager._connection_info = "Serial: /dev/ttyUSB0"

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key_a, text="to alpha")
            )
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key_b, text="to bravo")
            )
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key_a, text="alpha again")
            )
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key_c, text="to charlie")
            )

        assert [call.kwargs["channel_idx"] for call in mc.commands.set_channel.await_args_list] == [
            0,
            1,
            1,
        ]
        assert [call.kwargs["chan"] for call in mc.commands.send_chan_msg.await_args_list] == [
            0,
            1,
            0,
            1,
        ]
        assert radio_manager.get_cached_channel_slot(chan_key_a) == 0
        assert radio_manager.get_cached_channel_slot(chan_key_b) is None
        assert radio_manager.get_cached_channel_slot(chan_key_c) == 1

    @pytest.mark.asyncio
    async def test_send_channel_msg_tcp_always_reconfigures_slot(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "d1" * 16
        await ChannelRepository.upsert(key=chan_key, name="#tcp")
        radio_manager.max_channels = 4
        radio_manager._connection_info = "TCP: 127.0.0.1:4000"

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="first send")
            )
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="second send")
            )

        assert mc.commands.set_channel.await_count == 2
        assert mc.commands.send_chan_msg.await_count == 2
        assert radio_manager.get_cached_channel_slot(chan_key) is None

    @pytest.mark.asyncio
    async def test_send_channel_msg_force_reconfigure_env_disables_reuse(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "e1" * 16
        await ChannelRepository.upsert(key=chan_key, name="#forced")
        radio_manager.max_channels = 4
        radio_manager._connection_info = "Serial: /dev/ttyUSB0"

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch("app.radio.settings.force_channel_slot_reconfigure", True),
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="first send")
            )
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="second send")
            )

        assert mc.commands.set_channel.await_count == 2
        assert radio_manager.get_cached_channel_slot(chan_key) is None

    @pytest.mark.asyncio
    async def test_send_channel_msg_error_invalidates_cached_slot(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "f1" * 16
        await ChannelRepository.upsert(key=chan_key, name="#stale")
        radio_manager.max_channels = 4
        radio_manager._connection_info = "Serial: /dev/ttyUSB0"

        mc.commands.send_chan_msg = AsyncMock(
            return_value=MagicMock(type=EventType.ERROR, payload="bad slot")
        )

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            pytest.raises(HTTPException) as exc_info,
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="this will fail")
            )

        assert exc_info.value.status_code == 500
        assert radio_manager.get_cached_channel_slot(chan_key) is None


class TestResendChannelMessage:
    """Test the user-triggered resend endpoint."""

    @pytest.mark.asyncio
    async def test_resend_within_window_succeeds(self, test_db):
        """Resend within 30-second window sends with same timestamp bytes."""
        mc = _make_mc(name="MyNode")
        chan_key = "aa" * 16
        await ChannelRepository.upsert(key=chan_key, name="#resend")

        now = int(time.time()) - 10  # 10 seconds ago
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: hello",
            conversation_key=chan_key.upper(),
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
        )
        assert msg_id is not None

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            result = await resend_channel_message(msg_id, new_timestamp=False)

        assert result["status"] == "ok"
        assert result["message_id"] == msg_id

        # Verify radio was called with correct timestamp bytes
        mc.commands.send_chan_msg.assert_awaited_once()
        call_kwargs = mc.commands.send_chan_msg.await_args.kwargs
        assert call_kwargs["timestamp"] == now.to_bytes(4, "little")
        assert call_kwargs["msg"] == "hello"  # Sender prefix stripped

    @pytest.mark.asyncio
    async def test_resend_outside_window_returns_400(self, test_db):
        """Resend after 30-second window fails."""
        mc = _make_mc(name="MyNode")
        chan_key = "bb" * 16
        await ChannelRepository.upsert(key=chan_key, name="#old")

        old_ts = int(time.time()) - 60  # 60 seconds ago
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: old message",
            conversation_key=chan_key.upper(),
            sender_timestamp=old_ts,
            received_at=old_ts,
            outgoing=True,
        )
        assert msg_id is not None

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            pytest.raises(HTTPException) as exc_info,
        ):
            await resend_channel_message(msg_id, new_timestamp=False)

        assert exc_info.value.status_code == 400
        assert "expired" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_resend_uses_current_channel_flood_scope_override(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "be" * 16
        await ChannelRepository.upsert(key=chan_key, name="#flightless")
        await ChannelRepository.update_flood_scope_override(chan_key, "#CurrentRegion")
        await AppSettingsRepository.update(flood_scope="#Baseline")

        now = int(time.time()) - 10
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: hello",
            conversation_key=chan_key.upper(),
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
        )
        assert msg_id is not None

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            await resend_channel_message(msg_id, new_timestamp=False)

        assert mc.commands.set_flood_scope.await_args_list == [
            call("#CurrentRegion"),
            call("#Baseline"),
        ]

    @pytest.mark.asyncio
    async def test_resend_restore_failure_broadcasts_warning(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "b1" * 16
        await ChannelRepository.upsert(key=chan_key, name="#flightless")
        await ChannelRepository.update_flood_scope_override(chan_key, "#CurrentRegion")
        await AppSettingsRepository.update(flood_scope="#Baseline")

        now = int(time.time()) - 10
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: hello",
            conversation_key=chan_key.upper(),
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
        )
        assert msg_id is not None

        mc.commands.set_flood_scope = AsyncMock(
            side_effect=[
                _make_radio_result(),
                MagicMock(type=EventType.ERROR, payload="restore failed"),
            ]
        )

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_error") as mock_broadcast_error,
        ):
            result = await resend_channel_message(msg_id, new_timestamp=False)

        assert result["status"] == "ok"
        mock_broadcast_error.assert_called_once()
        assert "restore failed" in mock_broadcast_error.call_args.args[0].lower()

    @pytest.mark.asyncio
    async def test_resend_new_timestamp_collision_returns_original_id(self, test_db):
        """When new-timestamp resend collides (same second), return original ID gracefully."""
        mc = _make_mc(name="MyNode")
        chan_key = "dd" * 16
        await ChannelRepository.upsert(key=chan_key, name="#collision")

        now = int(time.time())
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: duplicate",
            conversation_key=chan_key.upper(),
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
        )
        assert msg_id is not None

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch("app.routers.messages.time") as mock_time,
        ):
            # Force the same second so MessageRepository.create returns None (duplicate)
            mock_time.time.return_value = float(now)
            result = await resend_channel_message(msg_id, new_timestamp=True)

        # Should succeed gracefully, returning the original message ID
        assert result["status"] == "ok"
        assert result["message_id"] == msg_id

    @pytest.mark.asyncio
    async def test_resend_non_outgoing_returns_400(self, test_db):
        """Resend of incoming message fails."""
        mc = _make_mc(name="MyNode")
        chan_key = "cc" * 16
        await ChannelRepository.upsert(key=chan_key, name="#incoming")

        now = int(time.time())
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="SomeUser: incoming",
            conversation_key=chan_key.upper(),
            sender_timestamp=now,
            received_at=now,
            outgoing=False,
        )
        assert msg_id is not None

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            pytest.raises(HTTPException) as exc_info,
        ):
            await resend_channel_message(msg_id, new_timestamp=False)

        assert exc_info.value.status_code == 400
        assert "outgoing" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_resend_dm_returns_400(self, test_db):
        """Resend of DM message fails."""
        mc = _make_mc(name="MyNode")
        pub_key = "dd" * 32

        now = int(time.time())
        msg_id = await MessageRepository.create(
            msg_type="PRIV",
            text="hello dm",
            conversation_key=pub_key,
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
        )
        assert msg_id is not None

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            pytest.raises(HTTPException) as exc_info,
        ):
            await resend_channel_message(msg_id, new_timestamp=False)

        assert exc_info.value.status_code == 400
        assert "channel" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_resend_nonexistent_returns_404(self, test_db):
        """Resend of nonexistent message fails."""
        mc = _make_mc(name="MyNode")

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            pytest.raises(HTTPException) as exc_info,
        ):
            await resend_channel_message(999999, new_timestamp=False)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_resend_strips_sender_prefix(self, test_db):
        """Resend strips the sender prefix before sending to radio."""
        mc = _make_mc(name="MyNode")
        chan_key = "ee" * 16
        await ChannelRepository.upsert(key=chan_key, name="#strip")

        now = int(time.time()) - 5
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: hello world",
            conversation_key=chan_key.upper(),
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
        )
        assert msg_id is not None

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            await resend_channel_message(msg_id, new_timestamp=False)

        call_kwargs = mc.commands.send_chan_msg.await_args.kwargs
        assert call_kwargs["msg"] == "hello world"

    @pytest.mark.asyncio
    async def test_resend_new_timestamp_skips_window(self, test_db):
        """new_timestamp=True succeeds even when the 30s window has expired."""
        mc = _make_mc(name="MyNode")
        chan_key = "dd" * 16
        await ChannelRepository.upsert(key=chan_key, name="#old")

        old_ts = int(time.time()) - 60  # 60 seconds ago — outside byte-perfect window
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: old message",
            conversation_key=chan_key.upper(),
            sender_timestamp=old_ts,
            received_at=old_ts,
            outgoing=True,
        )
        assert msg_id is not None

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            result = await resend_channel_message(msg_id, new_timestamp=True)

        assert result["status"] == "ok"
        # Should return a NEW message id, not the original
        assert result["message_id"] != msg_id

    @pytest.mark.asyncio
    async def test_resend_new_timestamp_creates_new_message(self, test_db):
        """new_timestamp=True creates a new DB row with a different sender_timestamp."""
        mc = _make_mc(name="MyNode")
        chan_key = "dd" * 16
        await ChannelRepository.upsert(key=chan_key, name="#new")

        old_ts = int(time.time()) - 10
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: test",
            conversation_key=chan_key.upper(),
            sender_timestamp=old_ts,
            received_at=old_ts,
            outgoing=True,
        )
        assert msg_id is not None

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            result = await resend_channel_message(msg_id, new_timestamp=True)

        new_msg_id = result["message_id"]
        new_msg = await MessageRepository.get_by_id(new_msg_id)
        original_msg = await MessageRepository.get_by_id(msg_id)

        assert new_msg is not None
        assert original_msg is not None
        assert new_msg.sender_timestamp != original_msg.sender_timestamp
        assert new_msg.text == original_msg.text
        assert new_msg.outgoing is True

    @pytest.mark.asyncio
    async def test_resend_new_timestamp_broadcasts_message(self, test_db):
        """new_timestamp=True broadcasts the new message via WebSocket."""
        mc = _make_mc(name="MyNode")
        chan_key = "dd" * 16
        await ChannelRepository.upsert(key=chan_key, name="#broadcast")

        old_ts = int(time.time()) - 5
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: broadcast test",
            conversation_key=chan_key.upper(),
            sender_timestamp=old_ts,
            received_at=old_ts,
            outgoing=True,
        )
        assert msg_id is not None

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event") as mock_broadcast,
        ):
            result = await resend_channel_message(msg_id, new_timestamp=True)

        mock_broadcast.assert_called_once()
        event_type, event_data = mock_broadcast.call_args.args
        assert event_type == "message"
        assert event_data["id"] == result["message_id"]
        assert event_data["outgoing"] is True
        assert event_data["channel_name"] == "#broadcast"

    @pytest.mark.asyncio
    async def test_resend_byte_perfect_still_enforces_window(self, test_db):
        """Default (byte-perfect) resend still enforces the 30s window."""
        mc = _make_mc(name="MyNode")
        chan_key = "dd" * 16
        await ChannelRepository.upsert(key=chan_key, name="#window")

        old_ts = int(time.time()) - 60
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: expired",
            conversation_key=chan_key.upper(),
            sender_timestamp=old_ts,
            received_at=old_ts,
            outgoing=True,
        )
        assert msg_id is not None

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            pytest.raises(HTTPException) as exc_info,
        ):
            await resend_channel_message(msg_id, new_timestamp=False)

        assert exc_info.value.status_code == 400
        assert "expired" in exc_info.value.detail.lower()


class TestRadioExceptionMidSend:
    """Test that radio exceptions during send don't leave orphaned DB state."""

    @pytest.mark.asyncio
    async def test_dm_send_radio_exception_no_orphan_message(self, test_db):
        """When mc.commands.send_msg() raises, no message should be stored in DB."""
        mc = _make_mc()
        pub_key = "ab" * 32
        await _insert_contact(pub_key, "Alice")

        # Make the radio command raise (simulates serial timeout / connection drop)
        mc.commands.send_msg = AsyncMock(side_effect=ConnectionError("Serial port disconnected"))

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(ConnectionError):
                await send_direct_message(
                    SendDirectMessageRequest(destination=pub_key, text="This will fail")
                )

        # No message should be stored — the exception prevented reaching MessageRepository.create
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=pub_key, limit=10
        )
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_channel_send_radio_exception_no_orphan_message(self, test_db):
        """When mc.commands.send_chan_msg() raises, no message should be stored in DB."""
        from app.repository import ChannelRepository

        mc = _make_mc(name="TestNode")
        chan_key = "ab" * 16
        await ChannelRepository.upsert(key=chan_key, name="#test")

        mc.commands.send_chan_msg = AsyncMock(
            side_effect=ConnectionError("Serial port disconnected")
        )

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(ConnectionError):
                await send_channel_message(
                    SendChannelMessageRequest(channel_key=chan_key, text="This will fail")
                )

        messages = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=chan_key.upper(), limit=10
        )
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_channel_send_set_channel_exception_no_orphan(self, test_db):
        """When mc.commands.set_channel() raises, send is not attempted and no message stored."""
        from app.repository import ChannelRepository

        mc = _make_mc(name="TestNode")
        chan_key = "cd" * 16
        await ChannelRepository.upsert(key=chan_key, name="#broken")

        mc.commands.set_channel = AsyncMock(side_effect=TimeoutError("Radio not responding"))

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(TimeoutError):
                await send_channel_message(
                    SendChannelMessageRequest(channel_key=chan_key, text="Never sent")
                )

        # send_chan_msg should never have been called
        mc.commands.send_chan_msg.assert_not_called()

        messages = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=chan_key.upper(), limit=10
        )
        assert len(messages) == 0


class TestConcurrentChannelSends:
    """Test that concurrent channel sends are serialized by the radio operation lock.

    The send_channel_message endpoint uses set_channel (slot 0) then send_chan_msg.
    Concurrent sends must be serialized so two messages don't clobber the same
    temporary radio slot.
    """

    @pytest.mark.asyncio
    async def test_concurrent_sends_to_different_channels_both_succeed(self, test_db):
        """Two concurrent send_channel_message calls to different channels
        should both succeed — the radio_operation lock serializes them."""
        mc = _make_mc(name="TestNode")
        chan_key_a = "aa" * 16
        chan_key_b = "bb" * 16
        await ChannelRepository.upsert(key=chan_key_a, name="#alpha")
        await ChannelRepository.upsert(key=chan_key_b, name="#bravo")

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            results = await asyncio.gather(
                send_channel_message(
                    SendChannelMessageRequest(channel_key=chan_key_a, text="Hello alpha")
                ),
                send_channel_message(
                    SendChannelMessageRequest(channel_key=chan_key_b, text="Hello bravo")
                ),
            )

        # Both should have returned Message objects with distinct IDs
        assert results[0].id != results[1].id
        assert results[0].conversation_key == chan_key_a.upper()
        assert results[1].conversation_key == chan_key_b.upper()

        # set_channel should have been called twice (once per send, serialized)
        assert mc.commands.set_channel.await_count == 2

        # send_chan_msg should have been called twice
        assert mc.commands.send_chan_msg.await_count == 2

        # Both messages should be in DB
        msgs_a = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=chan_key_a.upper(), limit=10
        )
        msgs_b = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=chan_key_b.upper(), limit=10
        )
        assert len(msgs_a) == 1
        assert len(msgs_b) == 1

    @pytest.mark.asyncio
    async def test_concurrent_sends_to_same_channel_both_succeed(self, test_db):
        """Two concurrent sends to the same channel should both succeed
        with distinct timestamps (serialized, no slot clobber)."""
        mc = _make_mc(name="TestNode")
        chan_key = "cc" * 16
        await ChannelRepository.upsert(key=chan_key, name="#charlie")

        call_count = 0

        # Mock time to return incrementing seconds so the two messages
        # get distinct sender_timestamps (avoiding same-second collision).
        original_time = time.time

        def advancing_time():
            nonlocal call_count
            call_count += 1
            return original_time() + call_count

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch("app.routers.messages.time") as mock_time,
        ):
            mock_time.time = advancing_time
            results = await asyncio.gather(
                send_channel_message(
                    SendChannelMessageRequest(channel_key=chan_key, text="Message one")
                ),
                send_channel_message(
                    SendChannelMessageRequest(channel_key=chan_key, text="Message two")
                ),
            )

        assert results[0].id != results[1].id
        texts = {results[0].text, results[1].text}
        assert "TestNode: Message one" in texts
        assert "TestNode: Message two" in texts

        msgs = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=chan_key.upper(), limit=10
        )
        assert len(msgs) == 2


class TestChannelSendLockScope:
    """Channel send should release the radio lock before DB persistence work."""

    @pytest.mark.asyncio
    async def test_channel_message_row_created_after_radio_lock_released(self, test_db):
        mc = _make_mc(name="TestNode")
        chan_key = "de" * 16
        await ChannelRepository.upsert(key=chan_key, name="#lockscope")

        observed_lock_states: list[bool] = []
        original_create = MessageRepository.create

        async def _assert_lock_then_create(*args, **kwargs):
            observed_lock_states.append(bool(radio_manager._operation_lock.locked()))
            return await original_create(*args, **kwargs)

        with (
            patch("app.routers.messages.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch(
                "app.services.message_send.MessageRepository.create",
                side_effect=_assert_lock_then_create,
            ),
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="Lock scope test")
            )

        assert observed_lock_states == [False]
