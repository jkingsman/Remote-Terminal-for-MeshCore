"""Tests for outgoing message sending via the messages router."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType

import app.services.message_send as message_send_service
from app.compression.mcmp import try_decode_incoming
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
from app.services import dm_ack_tracker
from app.services.flood_scope import FORCE_UNSCOPED_FRAME
from app.services.message_send import NO_RADIO_RESPONSE_AFTER_SEND_DETAIL


@pytest.fixture(autouse=True)
def _reset_radio_state():
    """Save/restore radio_manager state so tests don't leak."""
    prev = radio_manager._meshcore
    prev_lock = radio_manager._operation_lock
    prev_max_channels = radio_manager.max_channels
    prev_connection_info = radio_manager._connection_info
    prev_slot_by_key = radio_manager._channel_slot_by_key.copy()
    prev_key_by_slot = radio_manager._channel_key_by_slot.copy()
    prev_pending_acks = dm_ack_tracker._pending_acks.copy()
    prev_buffered_acks = dm_ack_tracker._buffered_acks.copy()
    yield
    radio_manager._meshcore = prev
    radio_manager._operation_lock = prev_lock
    radio_manager.max_channels = prev_max_channels
    radio_manager._connection_info = prev_connection_info
    radio_manager._channel_slot_by_key = prev_slot_by_key
    radio_manager._channel_key_by_slot = prev_key_by_slot
    dm_ack_tracker._pending_acks.clear()
    dm_ack_tracker._pending_acks.update(prev_pending_acks)
    dm_ack_tracker._buffered_acks.clear()
    dm_ack_tracker._buffered_acks.update(prev_buffered_acks)


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
    mc.commands.send = AsyncMock(return_value=_make_radio_result())
    mc.commands.send_msg = AsyncMock(return_value=_make_radio_result())
    mc.commands.send_chan_msg = AsyncMock(return_value=_make_radio_result())
    mc.commands.add_contact = AsyncMock(return_value=_make_radio_result())
    mc.commands.reset_path = AsyncMock(return_value=MagicMock(type=EventType.OK, payload={}))
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
        "direct_path": None,
        "direct_path_len": -1,
        "direct_path_hash_mode": -1,
        "last_advert": None,
        "lat": None,
        "lon": None,
        "last_seen": None,
        "on_radio": False,
        "last_contacted": None,
    }
    data.update(overrides)
    await ContactRepository.upsert(data)


@pytest.fixture(autouse=True)
def _disable_background_dm_retries(monkeypatch):
    monkeypatch.setattr(message_send_service, "DM_SEND_MAX_ATTEMPTS", 1)
    yield


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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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

        with patch("app.routers.messages.radio_manager.require_connected", return_value=mc):
            with pytest.raises(HTTPException) as exc_info:
                await send_direct_message(
                    SendDirectMessageRequest(destination="abc123", text="Hello")
                )

        assert exc_info.value.status_code == 409
        assert "ambiguous" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_send_dm_preserves_stored_direct_path_hash_mode(self, test_db):
        """Direct-message send pushes the persisted path hash mode back to the radio."""
        mc = _make_mc()
        pub_key = "cd" * 32
        await _insert_contact(
            pub_key,
            "Alice",
            direct_path="aa00bb00",
            direct_path_len=2,
            direct_path_hash_mode=1,
        )

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            direct_path="aabb",
            direct_path_len=1,
            direct_path_hash_mode=0,
            route_override_path="cc00dd00",
            route_override_len=2,
            route_override_hash_mode=1,
        )

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendDirectMessageRequest(destination=pub_key, text="Hello")
            await send_direct_message(request)

        contact_payload = mc.commands.add_contact.call_args.args[0]
        assert contact_payload["out_path"] == "cc00dd00"
        assert contact_payload["out_path_len"] == 2
        assert contact_payload["out_path_hash_mode"] == 1

    @pytest.mark.asyncio
    async def test_send_dm_same_second_duplicate_bumps_timestamp(self, test_db):
        mc = _make_mc()
        pub_key = "fa" * 32
        await _insert_contact(pub_key, "Alice")

        now = int(time.time())
        original_id = await MessageRepository.create(
            msg_type="PRIV",
            text="hello",
            conversation_key=pub_key,
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
        )
        assert original_id is not None

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch("app.routers.messages.time") as mock_time,
        ):
            mock_time.time.return_value = float(now)
            result = await send_direct_message(
                SendDirectMessageRequest(destination=pub_key, text="hello")
            )

        assert result.id != original_id
        assert result.sender_timestamp == now + 1
        assert result.received_at == now
        assert mc.commands.send_msg.await_args.kwargs["timestamp"] == now + 1

    @pytest.mark.asyncio
    async def test_send_dm_applies_buffered_ack_from_early_arrival(self, test_db):
        from app.event_handlers import on_ack

        mc = _make_mc()
        ack_bytes = b"\xde\xad\xbe\xef"
        result = MagicMock()
        result.type = EventType.MSG_SENT
        result.payload = {
            "expected_ack": ack_bytes,
            "suggested_timeout": 8000,
        }
        mc.commands.send_msg = AsyncMock(return_value=result)

        pub_key = "fb" * 32
        await _insert_contact(pub_key, "Alice")

        class MockAckEvent:
            payload = {"code": "deadbeef"}

        broadcasts = []

        def capture_broadcast(event_type, data):
            broadcasts.append((event_type, data))

        with (
            patch("app.event_handlers.broadcast_event", side_effect=capture_broadcast),
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event", side_effect=capture_broadcast),
        ):
            await on_ack(MockAckEvent())
            message = await send_direct_message(
                SendDirectMessageRequest(destination=pub_key, text="Hello")
            )

        ack_count, _ = await MessageRepository.get_ack_and_paths(message.id)
        assert ack_count == 1
        assert message.acked == 1
        assert any(event_type == "message_acked" for event_type, _data in broadcasts)

    @pytest.mark.asyncio
    async def test_send_dm_without_expected_ack_does_not_schedule_retries(self, test_db):
        mc = _make_mc()
        pub_key = "fb" * 32
        await _insert_contact(pub_key, "Alice")

        mc.commands.send_msg = AsyncMock(return_value=_make_radio_result({}))

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch("app.services.message_send.asyncio.create_task") as mock_create_task,
        ):
            message = await send_direct_message(
                SendDirectMessageRequest(destination=pub_key, text="Hello")
            )

        assert message.acked == 0
        mock_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_dm_background_retries_reset_path_before_final_attempt(self, test_db):
        mc = _make_mc()
        pub_key = "fc" * 32
        await _insert_contact(pub_key, "Alice")

        mc.commands.send_msg = AsyncMock(
            side_effect=[
                _make_radio_result(
                    {"expected_ack": b"\x00\x00\x00\x01", "suggested_timeout": 8000}
                ),
                _make_radio_result(
                    {"expected_ack": b"\x00\x00\x00\x02", "suggested_timeout": 7000}
                ),
                _make_radio_result(
                    {"expected_ack": b"\x00\x00\x00\x03", "suggested_timeout": 6000}
                ),
            ]
        )

        retry_tasks = []
        loop = asyncio.get_running_loop()
        slept_for = []

        def schedule_retry(coro):
            task = loop.create_task(coro)
            retry_tasks.append(task)
            return task

        async def no_wait(seconds):
            slept_for.append(seconds)
            return None

        with (
            patch.object(message_send_service, "DM_SEND_MAX_ATTEMPTS", 3),
            patch("app.routers.messages.track_pending_ack", return_value=False),
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch("app.services.message_send.asyncio.create_task", side_effect=schedule_retry),
            patch("app.services.message_send.asyncio.sleep", side_effect=no_wait),
        ):
            await send_direct_message(SendDirectMessageRequest(destination=pub_key, text="Hello"))
            await asyncio.gather(*retry_tasks)

        assert mc.commands.send_msg.await_count == 3
        assert mc.commands.add_contact.await_count == 3
        assert mc.commands.send_msg.await_args_list[1].kwargs["attempt"] == 1
        assert mc.commands.send_msg.await_args_list[2].kwargs["attempt"] == 2
        mc.commands.reset_path.assert_awaited_once_with(pub_key)
        assert slept_for == pytest.approx([9.6, 8.4])

    @pytest.mark.asyncio
    async def test_send_dm_background_retry_stops_after_late_ack(self, test_db):
        from app.event_handlers import on_ack

        mc = _make_mc()
        pub_key = "fd" * 32
        await _insert_contact(pub_key, "Alice")

        mc.commands.send_msg = AsyncMock(
            return_value=_make_radio_result(
                {"expected_ack": b"\xde\xad\xbe\xef", "suggested_timeout": 8000}
            )
        )

        retry_tasks = []
        sleep_gate = asyncio.Event()
        loop = asyncio.get_running_loop()

        def schedule_retry(coro):
            task = loop.create_task(coro)
            retry_tasks.append(task)
            return task

        async def gated_sleep(_seconds):
            await sleep_gate.wait()

        class MockAckEvent:
            payload = {"code": "deadbeef"}

        with (
            patch.object(message_send_service, "DM_SEND_MAX_ATTEMPTS", 3),
            patch("app.event_handlers.broadcast_event"),
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch("app.services.message_send.asyncio.create_task", side_effect=schedule_retry),
            patch("app.services.message_send.asyncio.sleep", side_effect=gated_sleep),
        ):
            message = await send_direct_message(
                SendDirectMessageRequest(destination=pub_key, text="Hello")
            )
            await on_ack(MockAckEvent())
            sleep_gate.set()
            await asyncio.gather(*retry_tasks)

        ack_count, _ = await MessageRepository.get_ack_and_paths(message.id)
        assert ack_count == 1
        assert mc.commands.send_msg.await_count == 1

    @pytest.mark.asyncio
    async def test_buffered_retry_ack_clears_older_dm_ack_codes(self, test_db):
        from app.event_handlers import on_ack

        mc = _make_mc()
        pub_key = "fe" * 32
        await _insert_contact(pub_key, "Alice")

        mc.commands.send_msg = AsyncMock(
            side_effect=[
                _make_radio_result(
                    {"expected_ack": b"\xaa\xaa\xaa\x01", "suggested_timeout": 8000}
                ),
                _make_radio_result(
                    {"expected_ack": b"\xbb\xbb\xbb\x02", "suggested_timeout": 8000}
                ),
            ]
        )

        retry_tasks = []
        sleep_gate = asyncio.Event()
        loop = asyncio.get_running_loop()

        def schedule_retry(coro):
            task = loop.create_task(coro)
            retry_tasks.append(task)
            return task

        async def gated_sleep(_seconds):
            await sleep_gate.wait()

        class RetryAckEvent:
            payload = {"code": "bbbbbb02"}

        class FirstAckEvent:
            payload = {"code": "aaaaaa01"}

        with (
            patch.object(message_send_service, "DM_SEND_MAX_ATTEMPTS", 3),
            patch("app.event_handlers.broadcast_event"),
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch("app.services.message_send.asyncio.create_task", side_effect=schedule_retry),
            patch("app.services.message_send.asyncio.sleep", side_effect=gated_sleep),
        ):
            message = await send_direct_message(
                SendDirectMessageRequest(destination=pub_key, text="Hello")
            )
            await on_ack(RetryAckEvent())
            sleep_gate.set()
            await asyncio.gather(*retry_tasks)
            await on_ack(FirstAckEvent())

        ack_count, _ = await MessageRepository.get_ack_and_paths(message.id)
        assert ack_count == 1


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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
    async def test_send_channel_same_second_duplicate_bumps_timestamp(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "ac" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")

        now = int(time.time())
        original_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: hello",
            conversation_key=chan_key.upper(),
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
        )
        assert original_id is not None

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch("app.routers.messages.time") as mock_time,
        ):
            mock_time.time.return_value = float(now)
            result = await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="hello")
            )

        assert result.id != original_id
        assert result.sender_timestamp == now + 1
        assert result.received_at == now
        sent_timestamp = int.from_bytes(
            mc.commands.send_chan_msg.await_args.kwargs["timestamp"], "little"
        )
        assert sent_timestamp == now + 1

    @pytest.mark.asyncio
    async def test_send_channel_msg_response_includes_current_ack_count(self, test_db):
        """Send response reflects latest DB ack count at response time."""
        mc = _make_mc(name="MyNode")
        chan_key = "ff" * 16
        await ChannelRepository.upsert(key=chan_key, name="#acked")

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="hello")
            await send_channel_message(request)

        mc.commands.set_flood_scope.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_channel_msg_request_override_beats_channel_override(self, test_db):
        """A per-send flood_scope_override wins over the channel's persisted override."""
        mc = _make_mc(name="MyNode")
        chan_key = "d0" * 16
        await ChannelRepository.upsert(key=chan_key, name="#flightless")
        await ChannelRepository.update_flood_scope_override(chan_key, "Esperance")
        await AppSettingsRepository.update(flood_scope="Baseline")

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendChannelMessageRequest(
                channel_key=chan_key, text="hello", flood_scope_override="Override"
            )
            await send_channel_message(request)

        # Per-send "Override" applied (not the channel's "Esperance"); baseline restored.
        assert mc.commands.set_flood_scope.await_args_list == [
            call("#Override"),
            call("#Baseline"),
        ]

    @pytest.mark.asyncio
    async def test_send_channel_msg_request_override_scopes_unscoped_channel(self, test_db):
        """A per-send override scopes a channel that has no persisted override."""
        mc = _make_mc(name="MyNode")
        chan_key = "d1" * 16
        await ChannelRepository.upsert(key=chan_key, name="#plain")
        await AppSettingsRepository.update(flood_scope="")

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch.object(radio_manager, "firmware_ver_code", 13),
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendChannelMessageRequest(
                channel_key=chan_key, text="hello", flood_scope_override="Region"
            )
            await send_channel_message(request)

        # Apply the region, then restore the empty baseline via explicit unscoped mode.
        assert mc.commands.set_flood_scope.await_args_list == [call("#Region")]
        assert mc.commands.send.await_args_list == [
            call(FORCE_UNSCOPED_FRAME, [EventType.OK, EventType.ERROR])
        ]

    @pytest.mark.asyncio
    async def test_send_channel_msg_explicit_unscoped_override_forces_plain_flood(self, test_db):
        """An explicit empty override forces unscoped flood even over a scoped baseline."""
        mc = _make_mc(name="MyNode")
        chan_key = "d2" * 16
        await ChannelRepository.upsert(key=chan_key, name="#flightless")
        await ChannelRepository.update_flood_scope_override(chan_key, "Esperance")
        await AppSettingsRepository.update(flood_scope="Baseline")

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch.object(radio_manager, "firmware_ver_code", 13),
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendChannelMessageRequest(
                channel_key=chan_key, text="hello", flood_scope_override=""
            )
            await send_channel_message(request)

        # Explicit unscoped uses firmware mode 1, then restores the global baseline.
        assert mc.commands.send.await_args_list == [
            call(FORCE_UNSCOPED_FRAME, [EventType.OK, EventType.ERROR])
        ]
        assert mc.commands.set_flood_scope.await_args_list == [call("#Baseline")]

    @pytest.mark.asyncio
    async def test_persisted_unscoped_channel_forces_plain_flood_over_scoped_global(self, test_db):
        """A channel persistently marked unscoped ('*') sends unscoped even when the
        companion has a global region set — the core of issue #303. No per-send
        override is supplied, so this exercises the persisted-override path."""
        mc = _make_mc(name="MyNode")
        chan_key = "d3" * 16
        await ChannelRepository.upsert(key=chan_key, name="#plain")
        await ChannelRepository.update_flood_scope_override(chan_key, "*")
        await AppSettingsRepository.update(flood_scope="Baseline")

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch.object(radio_manager, "firmware_ver_code", 13),
            patch("app.routers.messages.broadcast_event"),
        ):
            # No per-send flood_scope_override: fall back to the channel's persisted "*".
            request = SendChannelMessageRequest(channel_key=chan_key, text="hello")
            await send_channel_message(request)

        # Force unscoped via mode 1, then restore the global region baseline.
        assert mc.commands.send.await_args_list == [
            call(FORCE_UNSCOPED_FRAME, [EventType.OK, EventType.ERROR])
        ]
        assert mc.commands.set_flood_scope.await_args_list == [call("#Baseline")]

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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            pytest.raises(HTTPException) as exc_info,
        ):
            request = SendChannelMessageRequest(channel_key=chan_key, text="hello")
            await send_channel_message(request)

        assert exc_info.value.status_code == 422
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            pytest.raises(HTTPException) as exc_info,
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="this will fail")
            )

        assert exc_info.value.status_code == 422
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            result = await resend_channel_message(msg_id, new_timestamp=False)

        assert result.status == "ok"
        assert result.message_id == msg_id

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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_error") as mock_broadcast_error,
        ):
            result = await resend_channel_message(msg_id, new_timestamp=False)

        assert result.status == "ok"
        mock_broadcast_error.assert_called_once()
        assert "restore failed" in mock_broadcast_error.call_args.args[0].lower()

    @pytest.mark.asyncio
    async def test_resend_new_timestamp_collision_bumps_timestamp(self, test_db):
        """New-timestamp resend should bump the transmit timestamp instead of reusing the row."""
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch("app.routers.messages.time") as mock_time,
        ):
            mock_time.time.return_value = float(now)
            result = await resend_channel_message(msg_id, new_timestamp=True)

        assert result.status == "ok"
        assert result.message_id != msg_id
        resent = await MessageRepository.get_by_id(result.message_id)
        assert resent is not None
        assert result.message is not None
        assert result.message.id == resent.id
        assert result.message.conversation_key == resent.conversation_key
        assert result.message.text == resent.text
        assert result.message.sender_timestamp == resent.sender_timestamp
        assert result.message.outgoing is True
        assert resent.sender_timestamp == now + 1
        assert resent.received_at == now
        sent_timestamp = int.from_bytes(
            mc.commands.send_chan_msg.await_args.kwargs["timestamp"], "little"
        )
        assert sent_timestamp == now + 1

    @pytest.mark.asyncio
    async def test_resend_no_radio_response_returns_408_and_creates_no_new_row(self, test_db):
        """When resend returns None, report unknown outcome and create no new message row."""
        mc = _make_mc(name="MyNode")
        chan_key = "c1" * 16
        await ChannelRepository.upsert(key=chan_key, name="#resend-none")

        now = int(time.time()) - 5
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: hello",
            conversation_key=chan_key.upper(),
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
        )
        assert msg_id is not None

        mc.commands.send_chan_msg = AsyncMock(return_value=None)

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            pytest.raises(HTTPException) as exc_info,
        ):
            await resend_channel_message(msg_id, new_timestamp=True)

        assert exc_info.value.status_code == 408
        assert exc_info.value.detail == NO_RADIO_RESPONSE_AFTER_SEND_DETAIL

        messages = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=chan_key.upper(), limit=10
        )
        assert len(messages) == 1

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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            result = await resend_channel_message(msg_id, new_timestamp=True)

        assert result.status == "ok"
        # Should return a NEW message id, not the original
        assert result.message_id != msg_id

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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            result = await resend_channel_message(msg_id, new_timestamp=True)

        new_msg_id = result.message_id
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event") as mock_broadcast,
        ):
            result = await resend_channel_message(msg_id, new_timestamp=True)

        mock_broadcast.assert_called_once()
        event_type, event_data = mock_broadcast.call_args.args
        assert event_type == "message"
        assert event_data["id"] == result.message_id
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            pytest.raises(HTTPException) as exc_info,
        ):
            await resend_channel_message(msg_id, new_timestamp=False)

        assert exc_info.value.status_code == 400
        assert "expired" in exc_info.value.detail.lower()


class TestPathHashModeOverride:
    """Test per-channel path_hash_mode_override apply/restore behavior."""

    @pytest.mark.asyncio
    async def test_send_channel_msg_uses_phm_override(self, test_db):
        """Override is applied before send and baseline restored after."""
        mc = _make_mc(name="MyNode")
        mc.commands.set_path_hash_mode = AsyncMock(return_value=_make_radio_result())
        chan_key = "f1" * 16
        await ChannelRepository.upsert(key=chan_key, name="#phm")
        await ChannelRepository.update_path_hash_mode_override(chan_key, 2)

        radio_manager.path_hash_mode = 0
        radio_manager.path_hash_mode_supported = True

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="hello")
            )

        assert mc.commands.set_path_hash_mode.await_args_list == [call(2), call(0)]
        assert radio_manager.path_hash_mode == 0

    @pytest.mark.asyncio
    async def test_send_channel_msg_skips_phm_when_matching_baseline(self, test_db):
        """No set_path_hash_mode calls when override matches baseline."""
        mc = _make_mc(name="MyNode")
        mc.commands.set_path_hash_mode = AsyncMock()
        chan_key = "f2" * 16
        await ChannelRepository.upsert(key=chan_key, name="#same")
        await ChannelRepository.update_path_hash_mode_override(chan_key, 1)

        radio_manager.path_hash_mode = 1
        radio_manager.path_hash_mode_supported = True

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="hello")
            )

        mc.commands.set_path_hash_mode.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_channel_msg_skips_phm_when_unsupported(self, test_db):
        """No set_path_hash_mode calls when radio doesn't support it."""
        mc = _make_mc(name="MyNode")
        mc.commands.set_path_hash_mode = AsyncMock()
        chan_key = "f3" * 16
        await ChannelRepository.upsert(key=chan_key, name="#nosupport")
        await ChannelRepository.update_path_hash_mode_override(chan_key, 2)

        radio_manager.path_hash_mode = 0
        radio_manager.path_hash_mode_supported = False

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="hello")
            )

        mc.commands.set_path_hash_mode.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_channel_msg_aborts_on_phm_apply_error(self, test_db):
        """ERROR on apply aborts the send entirely."""
        mc = _make_mc(name="MyNode")
        mc.commands.set_path_hash_mode = AsyncMock(
            return_value=MagicMock(type=EventType.ERROR, payload="unsupported mode")
        )
        chan_key = "f4" * 16
        await ChannelRepository.upsert(key=chan_key, name="#fail")
        await ChannelRepository.update_path_hash_mode_override(chan_key, 2)

        radio_manager.path_hash_mode = 0
        radio_manager.path_hash_mode_supported = True

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            pytest.raises(HTTPException) as exc_info,
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="hello")
            )

        assert exc_info.value.status_code == 422
        assert "path hash mode" in exc_info.value.detail.lower()
        mc.commands.send_chan_msg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_channel_msg_restores_scope_when_phm_apply_fails(self, test_db):
        """A flood-scope override that was applied must still be restored when the
        subsequent path-hash-mode apply fails. Regression: the scope apply and the
        PHM apply used to sit outside the try/finally, so a PHM error left the radio
        stuck on the temporary region scope for all later traffic."""
        mc = _make_mc(name="MyNode")
        # PHM apply (first call) errors -> raises; PHM restore (second call, in the
        # finally) succeeds so this test isolates the scope-restore behavior.
        mc.commands.set_path_hash_mode = AsyncMock(
            side_effect=[
                MagicMock(type=EventType.ERROR, payload="unsupported mode"),
                _make_radio_result(),
            ]
        )
        chan_key = "f6" * 16
        await ChannelRepository.upsert(key=chan_key, name="#both")
        await ChannelRepository.update_flood_scope_override(chan_key, "Esperance")
        await ChannelRepository.update_path_hash_mode_override(chan_key, 2)
        await AppSettingsRepository.update(flood_scope="Baseline")

        radio_manager.path_hash_mode = 0
        radio_manager.path_hash_mode_supported = True

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            pytest.raises(HTTPException) as exc_info,
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="hello")
            )

        assert exc_info.value.status_code == 422
        assert "path hash mode" in exc_info.value.detail.lower()
        # The send never happens...
        mc.commands.send_chan_msg.assert_not_awaited()
        # ...but the applied region scope is restored to the global baseline.
        assert mc.commands.set_flood_scope.await_args_list == [
            call("#Esperance"),
            call("#Baseline"),
        ]

    @pytest.mark.asyncio
    async def test_send_channel_msg_phm_restore_failure_broadcasts_error(self, test_db):
        """Message sends OK but restore failure after 3 attempts broadcasts an error."""
        mc = _make_mc(name="MyNode")
        mc.commands.set_path_hash_mode = AsyncMock(
            side_effect=[
                _make_radio_result(),  # apply succeeds
                MagicMock(type=EventType.ERROR, payload="fail 1"),
                MagicMock(type=EventType.ERROR, payload="fail 2"),
                MagicMock(type=EventType.ERROR, payload="fail 3"),
            ]
        )
        chan_key = "f5" * 16
        await ChannelRepository.upsert(key=chan_key, name="#restorefail")
        await ChannelRepository.update_path_hash_mode_override(chan_key, 2)

        radio_manager.path_hash_mode = 0
        radio_manager.path_hash_mode_supported = True

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch("app.routers.messages.broadcast_error") as mock_err,
        ):
            result = await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="hello")
            )

        assert result is not None  # message sent OK
        mock_err.assert_called_once()
        assert "path hash mode" in mock_err.call_args.args[0].lower()
        # 1 apply + 3 restore attempts = 4 calls total
        assert mc.commands.set_path_hash_mode.await_count == 4

    @pytest.mark.asyncio
    async def test_send_channel_msg_phm_restore_succeeds_on_second_attempt(self, test_db):
        """Restore retries and succeeds on the second attempt — no error broadcast."""
        mc = _make_mc(name="MyNode")
        mc.commands.set_path_hash_mode = AsyncMock(
            side_effect=[
                _make_radio_result(),  # apply succeeds
                MagicMock(type=EventType.ERROR, payload="transient"),  # restore attempt 1
                _make_radio_result(),  # restore attempt 2 succeeds
            ]
        )
        chan_key = "f6" * 16
        await ChannelRepository.upsert(key=chan_key, name="#retry")
        await ChannelRepository.update_path_hash_mode_override(chan_key, 2)

        radio_manager.path_hash_mode = 0
        radio_manager.path_hash_mode_supported = True

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
            patch("app.routers.messages.broadcast_error") as mock_err,
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="hello")
            )

        mock_err.assert_not_called()
        assert radio_manager.path_hash_mode == 0  # restored to baseline
        # 1 apply + 2 restore attempts = 3 calls
        assert mc.commands.set_path_hash_mode.await_count == 3


class TestChannelEchoWatchdog:
    """Test the auto-resend echo watchdog for channel messages."""

    @pytest.fixture(autouse=True)
    def _skip_watchdog_delay(self, monkeypatch):
        monkeypatch.setattr(message_send_service, "ECHO_WATCHDOG_DELAY_SECONDS", 0)

    @pytest.mark.asyncio
    async def test_watchdog_skips_when_echo_already_received(self, test_db):
        """Watchdog sees acked > 0 and returns without resending."""
        chan_key = "e1" * 16
        await ChannelRepository.upsert(key=chan_key, name="#echo")

        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: hello",
            conversation_key=chan_key.upper(),
            sender_timestamp=int(time.time()),
            received_at=int(time.time()),
            outgoing=True,
        )
        await MessageRepository.increment_ack_count(msg_id)

        mc = _make_mc(name="MyNode")

        with patch.object(radio_manager, "_meshcore", mc):
            await message_send_service._channel_echo_watchdog(
                message_id=msg_id,
                radio_manager=radio_manager,
                broadcast_fn=MagicMock(),
                error_broadcast_fn=MagicMock(),
            )

        # No radio operation attempted
        mc.commands.send_chan_msg.assert_not_called()

    @pytest.mark.asyncio
    async def test_watchdog_skips_when_outside_resend_window(self, test_db):
        """Watchdog skips resend when message is older than 30 seconds."""
        chan_key = "e2" * 16
        await ChannelRepository.upsert(key=chan_key, name="#stale")

        old_ts = int(time.time()) - 60
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: old",
            conversation_key=chan_key.upper(),
            sender_timestamp=old_ts,
            received_at=old_ts,
            outgoing=True,
        )

        mc = _make_mc(name="MyNode")

        with patch.object(radio_manager, "_meshcore", mc):
            await message_send_service._channel_echo_watchdog(
                message_id=msg_id,
                radio_manager=radio_manager,
                broadcast_fn=MagicMock(),
                error_broadcast_fn=MagicMock(),
            )

        mc.commands.send_chan_msg.assert_not_called()

    @pytest.mark.asyncio
    async def test_watchdog_resends_when_no_echo(self, test_db):
        """Watchdog resends byte-perfect when no echo has arrived."""
        chan_key = "e3" * 16
        await ChannelRepository.upsert(key=chan_key, name="#resend")

        now = int(time.time())
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: payload",
            conversation_key=chan_key.upper(),
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
        )

        mc = _make_mc(name="MyNode")

        with patch.object(radio_manager, "_meshcore", mc):
            await message_send_service._channel_echo_watchdog(
                message_id=msg_id,
                radio_manager=radio_manager,
                broadcast_fn=MagicMock(),
                error_broadcast_fn=MagicMock(),
            )

        mc.commands.send_chan_msg.assert_awaited_once()
        call_kwargs = mc.commands.send_chan_msg.await_args.kwargs
        assert call_kwargs["msg"] == "payload"  # sender prefix stripped
        assert call_kwargs["timestamp"] == now.to_bytes(4, "little")

    @pytest.mark.asyncio
    async def test_watchdog_handles_radio_busy_gracefully(self, test_db):
        """RadioOperationBusyError is caught — no exception propagates."""

        chan_key = "e4" * 16
        await ChannelRepository.upsert(key=chan_key, name="#busy")

        now = int(time.time())
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="MyNode: busy test",
            conversation_key=chan_key.upper(),
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
        )

        mc = _make_mc(name="MyNode")
        radio_manager._meshcore = mc
        # Lock the radio so the non-blocking acquire raises RadioOperationBusyError
        if radio_manager._operation_lock is None:
            radio_manager._operation_lock = asyncio.Lock()
        await radio_manager._operation_lock.acquire()

        try:
            # Should not raise — RadioOperationBusyError is caught internally
            await message_send_service._channel_echo_watchdog(
                message_id=msg_id,
                radio_manager=radio_manager,
                broadcast_fn=MagicMock(),
                error_broadcast_fn=MagicMock(),
            )
        finally:
            radio_manager._operation_lock.release()

        mc.commands.send_chan_msg.assert_not_called()

    @pytest.mark.asyncio
    async def test_watchdog_skips_deleted_message(self, test_db):
        """Watchdog exits cleanly if the message was deleted before it wakes."""
        # Use a message_id that doesn't exist
        mc = _make_mc(name="MyNode")
        with patch.object(radio_manager, "_meshcore", mc):
            await message_send_service._channel_echo_watchdog(
                message_id=999999,
                radio_manager=radio_manager,
                broadcast_fn=MagicMock(),
                error_broadcast_fn=MagicMock(),
            )

        mc.commands.send_chan_msg.assert_not_called()


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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
    async def test_dm_send_no_radio_response_returns_408_without_storing_message(self, test_db):
        """When mc.commands.send_msg() returns None, report unknown outcome and store nothing."""
        mc = _make_mc()
        pub_key = "ac" * 32
        await _insert_contact(pub_key, "Alice")

        mc.commands.send_msg = AsyncMock(return_value=None)

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            pytest.raises(HTTPException) as exc_info,
        ):
            await send_direct_message(
                SendDirectMessageRequest(destination=pub_key, text="Did this send?")
            )

        assert exc_info.value.status_code == 408
        assert exc_info.value.detail == NO_RADIO_RESPONSE_AFTER_SEND_DETAIL

        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=pub_key, limit=10
        )
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_channel_send_no_radio_response_returns_408_without_storing_message(
        self, test_db
    ):
        """When mc.commands.send_chan_msg() returns None, report unknown outcome and store nothing."""
        mc = _make_mc(name="TestNode")
        chan_key = "ad" * 16
        await ChannelRepository.upsert(key=chan_key, name="#unknown-outcome")

        mc.commands.send_chan_msg = AsyncMock(return_value=None)

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            pytest.raises(HTTPException) as exc_info,
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="Did this send?")
            )

        assert exc_info.value.status_code == 408
        assert exc_info.value.detail == NO_RADIO_RESPONSE_AFTER_SEND_DETAIL

        messages = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=chan_key.upper(), limit=10
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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

    @pytest.mark.asyncio
    async def test_channel_send_set_channel_exception_invalidates_evicted_cached_slot(
        self, test_db
    ):
        """Eviction-path configure exceptions drop the stale cached slot owner."""
        from app.repository import ChannelRepository

        mc = _make_mc(name="TestNode")
        chan_key_a = "de" * 16
        chan_key_b = "ef" * 16
        await ChannelRepository.upsert(key=chan_key_a, name="#alpha")
        await ChannelRepository.upsert(key=chan_key_b, name="#bravo")

        radio_manager.max_channels = 1
        radio_manager._connection_info = "Serial: /dev/ttyUSB0"
        radio_manager.note_channel_slot_loaded(chan_key_a, 0)

        mc.commands.set_channel = AsyncMock(side_effect=TimeoutError("Radio not responding"))

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(TimeoutError):
                await send_channel_message(
                    SendChannelMessageRequest(channel_key=chan_key_b, text="Never sent")
                )

        assert radio_manager.get_cached_channel_slot(chan_key_a) is None
        assert radio_manager.get_cached_channel_slot(chan_key_b) is None
        mc.commands.send_chan_msg.assert_not_called()

    @pytest.mark.asyncio
    async def test_channel_send_set_channel_error_invalidates_evicted_cached_slot(self, test_db):
        """Eviction-path configure error results also drop the stale cached slot owner."""
        mc = _make_mc(name="TestNode")
        chan_key_a = "fa" * 16
        chan_key_b = "fb" * 16
        await ChannelRepository.upsert(key=chan_key_a, name="#alpha")
        await ChannelRepository.upsert(key=chan_key_b, name="#bravo")

        radio_manager.max_channels = 1
        radio_manager._connection_info = "Serial: /dev/ttyUSB0"
        radio_manager.note_channel_slot_loaded(chan_key_a, 0)

        mc.commands.set_channel = AsyncMock(
            return_value=MagicMock(type=EventType.ERROR, payload="radio busy")
        )

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            pytest.raises(HTTPException) as exc_info,
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key_b, text="Never sent")
            )

        assert exc_info.value.status_code == 422
        assert radio_manager.get_cached_channel_slot(chan_key_a) is None
        assert radio_manager.get_cached_channel_slot(chan_key_b) is None
        mc.commands.send_chan_msg.assert_not_called()


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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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
    """Channel send should persist the outgoing row while the radio lock is held."""

    @pytest.mark.asyncio
    async def test_channel_message_row_created_inside_radio_lock(self, test_db):
        mc = _make_mc(name="TestNode")
        chan_key = "de" * 16
        await ChannelRepository.upsert(key=chan_key, name="#lockscope")

        observed_lock_states: list[bool] = []
        original_create = MessageRepository.create

        async def _assert_lock_then_create(*args, **kwargs):
            observed_lock_states.append(bool(radio_manager._operation_lock.locked()))
            return await original_create(*args, **kwargs)

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
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

        assert observed_lock_states == [True]

    @pytest.mark.asyncio
    async def test_channel_self_observation_during_send_reconciles_to_reserved_outgoing_row(
        self, test_db
    ):
        """A self-observation that arrives during send should update the reserved outgoing row."""
        from app.services.messages import create_fallback_channel_message

        mc = _make_mc(name="TestNode")
        chan_key = "ef" * 16
        await ChannelRepository.upsert(key=chan_key, name="#race")

        broadcasts = []

        def capture_broadcast(event_type, data, *args, **kwargs):
            broadcasts.append({"type": event_type, "data": data})

        async def send_with_self_observation(*args, **kwargs):
            timestamp_bytes = kwargs["timestamp"]
            sender_timestamp = int.from_bytes(timestamp_bytes, "little")
            await create_fallback_channel_message(
                conversation_key=chan_key.upper(),
                message_text="Hello race",
                sender_timestamp=sender_timestamp,
                received_at=int(time.time()),
                path="a1b2",
                path_len=2,
                txt_type=0,
                sender_name="TestNode",
                channel_name="#race",
                broadcast_fn=capture_broadcast,
            )
            return _make_radio_result()

        mc.commands.send_chan_msg = AsyncMock(side_effect=send_with_self_observation)

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event", side_effect=capture_broadcast),
        ):
            message = await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="Hello race")
            )

        assert message.outgoing is True
        assert message.acked == 1
        assert message.paths is not None
        assert len(message.paths) == 1
        assert message.paths[0].path == "a1b2"

        stored = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=chan_key.upper(), limit=10
        )
        assert len(stored) == 1
        assert stored[0].outgoing is True
        assert stored[0].acked == 1

        message_events = [entry for entry in broadcasts if entry["type"] == "message"]
        ack_events = [entry for entry in broadcasts if entry["type"] == "message_acked"]
        assert len(message_events) == 1
        assert len(ack_events) == 1


# A message long enough that MCMP actually shrinks it (short strings stay plain).
_MCMP_LONG_TEXT = "Battery at 40%, switching to power save and checking channel five for traffic."


class TestMcmpCompression:
    """Outbound MCMP compression is applied per-conversation when opted in.

    Only the transmitted body is compressed; the stored/broadcast text stays
    plaintext so history, search and dedup are unaffected.
    """

    @pytest.mark.asyncio
    async def test_dm_compressed_on_wire_when_enabled(self, test_db):
        mc = _make_mc()
        pub_key = "1a" * 32
        await _insert_contact(pub_key, "Alice")
        assert await ContactRepository.set_mcmp_enabled(pub_key, True)

        broadcasts = []
        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.routers.messages.broadcast_event",
                side_effect=lambda t, d, **k: broadcasts.append({"type": t, "data": d}),
            ),
        ):
            await send_direct_message(
                SendDirectMessageRequest(destination=pub_key, text=_MCMP_LONG_TEXT)
            )

        sent = mc.commands.send_msg.await_args.kwargs["msg"]
        assert sent.startswith("mcmp2:")
        assert len(sent.encode("utf-8")) < len(_MCMP_LONG_TEXT.encode("utf-8"))
        decoded = try_decode_incoming(sent)
        assert decoded is not None and decoded.text == _MCMP_LONG_TEXT
        # Stored/broadcast text is the plaintext, not the compressed wire form.
        msg_events = [b for b in broadcasts if b["type"] == "message"]
        assert msg_events and msg_events[0]["data"]["text"] == _MCMP_LONG_TEXT

    @pytest.mark.asyncio
    async def test_dm_plaintext_on_wire_when_disabled(self, test_db):
        mc = _make_mc()
        pub_key = "2b" * 32
        await _insert_contact(pub_key, "Alice")  # mcmp_enabled defaults off

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            await send_direct_message(
                SendDirectMessageRequest(destination=pub_key, text=_MCMP_LONG_TEXT)
            )

        assert mc.commands.send_msg.await_args.kwargs["msg"] == _MCMP_LONG_TEXT

    @pytest.mark.asyncio
    async def test_channel_compressed_body_when_enabled(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "3c" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")
        assert await ChannelRepository.set_mcmp_enabled(chan_key, True)

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            message = await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text=_MCMP_LONG_TEXT)
            )

        # The wire body is compressed and carries no sender prefix (the firmware
        # prepends "<name>: "); the stored row keeps the plaintext with prefix.
        sent = mc.commands.send_chan_msg.await_args.kwargs["msg"]
        assert sent.startswith("mcmp2:")
        decoded = try_decode_incoming(sent)
        assert decoded is not None and decoded.text == _MCMP_LONG_TEXT
        assert message.text == f"MyNode: {_MCMP_LONG_TEXT}"

    @pytest.mark.asyncio
    async def test_channel_plaintext_body_when_disabled(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "4d" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text=_MCMP_LONG_TEXT)
            )

        assert mc.commands.send_chan_msg.await_args.kwargs["msg"] == _MCMP_LONG_TEXT

    @pytest.mark.asyncio
    async def test_dm_uses_v3_transport_when_selected(self, test_db):
        mc = _make_mc()
        pub_key = "5e" * 32
        await _insert_contact(pub_key, "Alice")
        assert await ContactRepository.set_mcmp_enabled(pub_key, True)
        assert await ContactRepository.set_mcmp_version(pub_key, 3)

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            await send_direct_message(
                SendDirectMessageRequest(destination=pub_key, text=_MCMP_LONG_TEXT)
            )

        sent = mc.commands.send_msg.await_args.kwargs["msg"]
        assert sent.startswith("mcmp3:")
        decoded = try_decode_incoming(sent)
        assert decoded is not None and decoded.text == _MCMP_LONG_TEXT and decoded.version == "v3"

    @pytest.mark.asyncio
    async def test_channel_uses_v3_transport_when_selected(self, test_db):
        mc = _make_mc(name="MyNode")
        chan_key = "6f" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")
        assert await ChannelRepository.set_mcmp_enabled(chan_key, True)
        assert await ChannelRepository.set_mcmp_version(chan_key, 3)

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.broadcast_event"),
        ):
            message = await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text=_MCMP_LONG_TEXT)
            )

        sent = mc.commands.send_chan_msg.await_args.kwargs["msg"]
        assert sent.startswith("mcmp3:")
        decoded = try_decode_incoming(sent)
        assert decoded is not None and decoded.text == _MCMP_LONG_TEXT
        # Stored row is still plaintext with the sender prefix.
        assert message.text == f"MyNode: {_MCMP_LONG_TEXT}"
