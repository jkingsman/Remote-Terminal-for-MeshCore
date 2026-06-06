"""Tests for repeater-specific contacts routes (telemetry, command, trace)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType

from app.models import CommandRequest, Contact, RepeaterLoginRequest, RepeaterLoginResponse
from app.radio import radio_manager
from app.repository import ContactRepository
from app.routers.contacts import request_trace
from app.routers.repeaters import (
    _batch_cli_fetch,
    prepare_repeater_connection,
    repeater_acl,
    repeater_advert_intervals,
    repeater_login,
    repeater_lpp_telemetry,
    repeater_neighbors,
    repeater_node_info,
    repeater_owner_info,
    repeater_radio_settings,
    repeater_status,
    send_repeater_command,
)
from app.routers.server_control import fetch_contact_cli_response

KEY_A = "aa" * 32

# Patch target for the wall-clock wrapper used by fetch_contact_cli_response.
# We patch _monotonic (not time.monotonic) to avoid breaking the asyncio event loop.
_MONOTONIC = "app.routers.server_control._monotonic"

# Patch targets for the store helpers called on consumed non-target messages.
_STORE_DM = "app.routers.server_control._store_pending_direct_message"
_STORE_CHAN = "app.routers.server_control._store_pending_channel_message"


@pytest.fixture(autouse=True)
def _reset_radio_state():
    """Save/restore radio_manager state so tests don't leak."""
    prev = radio_manager._meshcore
    prev_lock = radio_manager._operation_lock
    yield
    radio_manager._meshcore = prev
    radio_manager._operation_lock = prev_lock


@pytest.fixture(autouse=True)
def _no_op_pre_send_flush():
    """Neutralize the pre-send buffer flush for command/batch route tests.

    ``_flush_pending_messages`` drains ``mc.commands.get_msg``, which the tests
    in this module mock to return fetch responses; flushing here would consume
    them. The flush behavior and its stale-response regression guard are covered
    in ``test_cli_stale_response_flush.py``, which exercises the real flush.
    Tests in ``TestFetchContactCliResponse`` call ``fetch_contact_cli_response``
    directly and never reach the flush, so this patch is a harmless no-op there.
    """
    with patch(
        "app.routers.server_control._flush_pending_messages",
        new_callable=AsyncMock,
    ):
        yield


def _radio_result(event_type=EventType.OK, payload=None):
    result = MagicMock()
    result.type = event_type
    result.payload = payload or {}
    return result


async def _insert_contact(public_key: str, name: str = "Node", contact_type: int = 0):
    """Insert a contact into the test database."""
    await ContactRepository.upsert(
        {
            "public_key": public_key,
            "name": name,
            "type": contact_type,
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
            "first_seen": None,
        }
    )


def _mock_mc():
    mc = MagicMock()
    mc.commands = MagicMock()
    mc.commands.send_login = AsyncMock(return_value=_radio_result(EventType.MSG_SENT))
    mc.commands.req_status_sync = AsyncMock()
    mc.commands.fetch_all_neighbours = AsyncMock()
    mc.commands.req_acl_sync = AsyncMock()
    mc.commands.req_telemetry_sync = AsyncMock()
    mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
    mc.commands.get_msg = AsyncMock()
    mc.commands.add_contact = AsyncMock(return_value=_radio_result(EventType.OK))
    mc.commands.send_trace = AsyncMock(return_value=_radio_result(EventType.OK))
    mc.wait_for_event = AsyncMock()
    mc.subscribe = MagicMock(return_value=MagicMock(unsubscribe=MagicMock()))
    mc.stop_auto_message_fetching = AsyncMock()
    mc.start_auto_message_fetching = AsyncMock()
    return mc


def _advancing_clock(start=0.0, step=0.1):
    """Return a callable for _monotonic that advances by `step` each call."""
    t = start

    def _tick():
        nonlocal t
        val = t
        t += step
        return val

    return _tick


class TestFetchContactCliResponse:
    """Tests for the fetch_contact_cli_response helper."""

    @pytest.mark.asyncio
    async def test_returns_matching_cli_response(self):
        mc = _mock_mc()
        mc.commands.get_msg = AsyncMock(
            return_value=_radio_result(
                EventType.CONTACT_MSG_RECV,
                {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ok", "txt_type": 1},
            )
        )

        with patch(_MONOTONIC, side_effect=_advancing_clock()):
            result = await fetch_contact_cli_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ok"
        mc.commands.get_msg.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rejects_same_sender_non_cli_message(self):
        """A txt_type=0 message from the target repeater is NOT accepted as the CLI response."""
        mc = _mock_mc()
        non_cli = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "chat msg", "txt_type": 0},
        )
        cli_response = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ver 1.0", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[non_cli, cli_response])

        with (
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch(_STORE_DM, new_callable=AsyncMock) as store_dm,
        ):
            result = await fetch_contact_cli_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ver 1.0"
        assert mc.commands.get_msg.await_count == 2
        store_dm.assert_awaited_once_with(non_cli)

    @pytest.mark.asyncio
    async def test_unrelated_dm_is_stored(self):
        """Unrelated DMs consumed during CLI fetch are stored, not discarded."""
        mc = _mock_mc()
        unrelated = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "bbbbbbbbbbbb", "text": "hello", "txt_type": 0},
        )
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ver 1.0", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[unrelated, expected])

        with (
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch(_STORE_DM, new_callable=AsyncMock) as store_dm,
        ):
            result = await fetch_contact_cli_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ver 1.0"
        store_dm.assert_awaited_once_with(unrelated)

    @pytest.mark.asyncio
    async def test_channel_message_is_stored(self):
        mc = _mock_mc()
        channel_msg = _radio_result(
            EventType.CHANNEL_MSG_RECV,
            {"channel_idx": 0, "text": "flood msg"},
        )
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ok", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[channel_msg, expected])

        with (
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch(_STORE_CHAN, new_callable=AsyncMock) as store_chan,
        ):
            result = await fetch_contact_cli_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ok"
        store_chan.assert_awaited_once_with(mc, channel_msg.payload)

    @pytest.mark.asyncio
    async def test_no_more_msgs_retries_then_succeeds(self):
        mc = _mock_mc()
        no_msgs = _radio_result(EventType.NO_MORE_MSGS)
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ok", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[no_msgs, expected])

        with (
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await fetch_contact_cli_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ok"
        assert mc.commands.get_msg.await_count == 2

    @pytest.mark.asyncio
    async def test_returns_none_after_deadline(self):
        """Returns None when wall-clock deadline expires."""
        mc = _mock_mc()
        mc.commands.get_msg = AsyncMock(return_value=_radio_result(EventType.NO_MORE_MSGS))

        # Start at 100.0, jump past deadline (timeout=2.0) after 2 get_msg calls
        times = iter([100.0, 100.5, 101.0, 103.0])

        with (
            patch(_MONOTONIC, side_effect=times),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await fetch_contact_cli_response(mc, "aaaaaaaaaaaa", timeout=2.0)

        assert result is None

    @pytest.mark.asyncio
    async def test_error_retries_then_succeeds(self):
        mc = _mock_mc()
        error = _radio_result(EventType.ERROR, {"err": "busy"})
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ok", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[error, expected])

        with (
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await fetch_contact_cli_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ok"

    @pytest.mark.asyncio
    async def test_high_traffic_stores_all_consumed_messages(self):
        """Many unrelated messages are stored and don't prevent eventual success."""
        mc = _mock_mc()
        # 20 unrelated DMs followed by the expected CLI response
        unrelated = [
            _radio_result(
                EventType.CONTACT_MSG_RECV,
                {"pubkey_prefix": f"{i:012x}", "text": f"msg {i}", "txt_type": 0},
            )
            for i in range(20)
        ]
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ver 1.0", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[*unrelated, expected])

        with (
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch(_STORE_DM, new_callable=AsyncMock) as store_dm,
        ):
            result = await fetch_contact_cli_response(mc, "aaaaaaaaaaaa", timeout=30.0)

        assert result is not None
        assert result.payload["text"] == "ver 1.0"
        assert mc.commands.get_msg.await_count == 21
        assert store_dm.await_count == 20

    @pytest.mark.asyncio
    async def test_subscription_captures_response_when_get_msg_misses_it(self):
        """The drop race: get_msg never returns the response, but the
        request-scoped subscription captures the cloned push event."""
        mc = _mock_mc()
        captured: dict = {}

        def _fake_subscribe(event_type, callback, attribute_filters=None):
            captured["cb"] = callback
            return MagicMock(unsubscribe=MagicMock())

        mc.subscribe = MagicMock(side_effect=_fake_subscribe)

        push_event = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ver 1.0", "txt_type": 1},
        )
        calls = {"n": 0}

        async def _fake_get_msg(timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                # Simulate the response being delivered to the permanent
                # subscriber path (our scoped subscription) rather than via
                # this get_msg's return value.
                captured["cb"](push_event)
            return _radio_result(EventType.NO_MORE_MSGS)

        mc.commands.get_msg = _fake_get_msg

        with (
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await fetch_contact_cli_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ver 1.0"

    @pytest.mark.asyncio
    async def test_subscription_uses_target_and_cli_filter(self):
        """The scoped subscription filters on the target prefix and txt_type=1."""
        mc = _mock_mc()
        mc.commands.get_msg = AsyncMock(
            return_value=_radio_result(
                EventType.CONTACT_MSG_RECV,
                {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ok", "txt_type": 1},
            )
        )

        with patch(_MONOTONIC, side_effect=_advancing_clock()):
            await fetch_contact_cli_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        args, kwargs = mc.subscribe.call_args
        assert args[0] == EventType.CONTACT_MSG_RECV
        assert kwargs["attribute_filters"] == {
            "pubkey_prefix": "aaaaaaaaaaaa",
            "txt_type": 1,
        }

    @pytest.mark.asyncio
    async def test_unsubscribes_on_success(self):
        mc = _mock_mc()
        sub = MagicMock(unsubscribe=MagicMock())
        mc.subscribe = MagicMock(return_value=sub)
        mc.commands.get_msg = AsyncMock(
            return_value=_radio_result(
                EventType.CONTACT_MSG_RECV,
                {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ok", "txt_type": 1},
            )
        )

        with patch(_MONOTONIC, side_effect=_advancing_clock()):
            result = await fetch_contact_cli_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        sub.unsubscribe.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsubscribes_on_timeout(self):
        mc = _mock_mc()
        sub = MagicMock(unsubscribe=MagicMock())
        mc.subscribe = MagicMock(return_value=sub)
        mc.commands.get_msg = AsyncMock(return_value=_radio_result(EventType.NO_MORE_MSGS))
        times = iter([100.0, 100.5, 101.0, 103.0])

        with (
            patch(_MONOTONIC, side_effect=times),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await fetch_contact_cli_response(mc, "aaaaaaaaaaaa", timeout=2.0)

        assert result is None
        sub.unsubscribe.assert_called_once()


class TestRepeaterCommandRoute:
    @pytest.mark.asyncio
    async def test_send_cmd_error_raises_and_restores_auto_fetch(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"err": "bad"})
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert exc.value.status_code == 422
        mc.start_auto_message_fetching.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_returns_no_response_message(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.commands.get_msg = AsyncMock(return_value=_radio_result(EventType.NO_MORE_MSGS))

        # Expire the deadline after a couple of ticks
        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=[0.0, 5.0, 25.0]),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert "no response" in response.response.lower()
        mc.start_auto_message_fetching.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_returns_command_response_text_and_sender_timestamp(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.commands.get_msg = AsyncMock(
            return_value=_radio_result(
                EventType.CONTACT_MSG_RECV,
                {
                    "pubkey_prefix": KEY_A[:12],
                    "text": "firmware: v1.2.3",
                    "sender_timestamp": 1700000000,
                    "txt_type": 1,
                },
            )
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert response.response == "firmware: v1.2.3"
        assert response.sender_timestamp == 1700000000

    @pytest.mark.asyncio
    async def test_response_strips_firmware_prompt_prefix(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.commands.get_msg = AsyncMock(
            return_value=_radio_result(
                EventType.CONTACT_MSG_RECV,
                {
                    "pubkey_prefix": KEY_A[:12],
                    "text": "> firmware: v1.2.3",
                    "sender_timestamp": 1700000000,
                    "txt_type": 1,
                },
            )
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.response == "firmware: v1.2.3"

    @pytest.mark.asyncio
    async def test_success_falls_back_to_legacy_timestamp_field(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.commands.get_msg = AsyncMock(
            return_value=_radio_result(
                EventType.CONTACT_MSG_RECV,
                {
                    "pubkey_prefix": KEY_A[:12],
                    "text": "firmware: v1.2.3",
                    "timestamp": 1700000000,
                    "txt_type": 1,
                },
            )
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert response.response == "firmware: v1.2.3"
        assert response.sender_timestamp == 1700000000

    @pytest.mark.asyncio
    async def test_unrelated_dm_during_command_does_not_prevent_success(self, test_db):
        """Unrelated DMs arriving during command wait are skipped; correct response returned."""
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))

        unrelated = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "bbbbbbbbbbbb", "text": "hello from someone", "txt_type": 0},
        )
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": KEY_A[:12], "text": "ver 1.0", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[unrelated, expected])

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert response.response == "ver 1.0"

    @pytest.mark.asyncio
    async def test_channel_message_during_command_is_skipped(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))

        channel_msg = _radio_result(
            EventType.CHANNEL_MSG_RECV,
            {"channel_idx": 0, "text": "flood msg"},
        )
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": KEY_A[:12], "text": "ok", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[channel_msg, expected])

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert response.response == "ok"

    @pytest.mark.asyncio
    async def test_no_more_msgs_then_response_succeeds(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))

        no_msgs = _radio_result(EventType.NO_MORE_MSGS)
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": KEY_A[:12], "text": "done", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[no_msgs, expected])

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert response.response == "done"


class TestTraceRoute:
    @pytest.mark.asyncio
    async def test_send_trace_error_returns_500(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Client", contact_type=1)
        mc.commands.send_trace = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"err": "x"})
        )

        with (
            patch("app.routers.contacts.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.contacts.random.randint", return_value=1234),
        ):
            with pytest.raises(HTTPException) as exc:
                await request_trace(KEY_A)

        assert exc.value.status_code == 422
        mc.commands.send_trace.assert_awaited_once_with(
            path=KEY_A[:8],
            tag=1234,
            flags=2,
        )

    @pytest.mark.asyncio
    async def test_wait_timeout_returns_408(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Client", contact_type=1)
        mc.commands.send_trace = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.wait_for_event = AsyncMock(return_value=None)

        with (
            patch("app.routers.contacts.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.contacts.random.randint", return_value=1234),
        ):
            with pytest.raises(HTTPException) as exc:
                await request_trace(KEY_A)

        assert exc.value.status_code == 408
        mc.commands.send_trace.assert_awaited_once_with(
            path=KEY_A[:8],
            tag=1234,
            flags=2,
        )

    @pytest.mark.asyncio
    async def test_success_returns_remote_and_local_snr(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Client", contact_type=1)
        mc.commands.send_trace = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.wait_for_event = AsyncMock(
            return_value=MagicMock(payload={"path": [{"snr": 5.5}, {"snr": 3.2}], "path_len": 2})
        )

        with (
            patch("app.routers.contacts.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.contacts.random.randint", return_value=1234),
        ):
            response = await request_trace(KEY_A)

        assert response.remote_snr == 5.5
        assert response.local_snr == 3.2
        assert response.path_len == 2
        mc.commands.send_trace.assert_awaited_once_with(
            path=KEY_A[:8],
            tag=1234,
            flags=2,
        )


# ---------------------------------------------------------------------------
# Tests for new granular repeater endpoints
# ---------------------------------------------------------------------------


class TestRepeaterLogin:
    @pytest.mark.asyncio
    async def test_success(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.routers.repeaters.prepare_repeater_connection",
                new_callable=AsyncMock,
            ) as mock_prepare,
        ):
            mock_prepare.return_value = RepeaterLoginResponse(
                status="ok",
                authenticated=True,
                message=None,
            )
            response = await repeater_login(KEY_A, RepeaterLoginRequest(password="secret"))

        assert response.status == "ok"
        assert response.authenticated is True
        assert response.message is None
        mock_prepare.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_404_missing_contact(self, test_db):
        mc = _mock_mc()
        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await repeater_login(KEY_A, RepeaterLoginRequest(password="pw"))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_400_not_repeater(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Client", contact_type=1)
        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await repeater_login(KEY_A, RepeaterLoginRequest(password="pw"))
        assert exc.value.status_code == 400
        assert "not a repeater" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_login_error_returns_warning_response(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)

        async def _prepare_fail(*args, **kwargs):
            return RepeaterLoginResponse(
                status="error",
                authenticated=False,
                message="Login failed",
            )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.repeaters.prepare_repeater_connection", side_effect=_prepare_fail),
        ):
            response = await repeater_login(KEY_A, RepeaterLoginRequest(password="bad"))
        assert response.status == "error"
        assert response.authenticated is False
        assert response.message == "Login failed"


class TestPrepareRepeaterConnection:
    @pytest.mark.asyncio
    async def test_returns_success_when_login_confirmed(self):
        mc = _mock_mc()
        contact = _make_contact()
        subscriptions: dict[EventType, tuple[object, object]] = {}

        def _subscribe(event_type, callback, attribute_filters=None):
            subscriptions[event_type] = (callback, attribute_filters)
            return MagicMock(unsubscribe=MagicMock())

        async def _send_login(*args, **kwargs):
            callback, filters = subscriptions[EventType.LOGIN_SUCCESS]
            assert filters == {"pubkey_prefix": KEY_A[:12]}
            callback(_radio_result(EventType.LOGIN_SUCCESS, {"pubkey_prefix": KEY_A[:12]}))
            return _radio_result(EventType.MSG_SENT)

        mc.subscribe = MagicMock(side_effect=_subscribe)
        mc.commands.send_login = AsyncMock(side_effect=_send_login)

        response = await prepare_repeater_connection(mc, contact, "secret")

        assert response.status == "ok"
        assert response.authenticated is True
        assert response.message is None

    @pytest.mark.asyncio
    async def test_returns_error_when_login_rejected(self):
        mc = _mock_mc()
        contact = _make_contact()
        subscriptions: dict[EventType, tuple[object, object]] = {}

        def _subscribe(event_type, callback, attribute_filters=None):
            subscriptions[event_type] = (callback, attribute_filters)
            return MagicMock(unsubscribe=MagicMock())

        async def _send_login(*args, **kwargs):
            callback, _filters = subscriptions[EventType.LOGIN_FAILED]
            callback(_radio_result(EventType.LOGIN_FAILED, {"pubkey_prefix": KEY_A[:12]}))
            return _radio_result(EventType.MSG_SENT)

        mc.subscribe = MagicMock(side_effect=_subscribe)
        mc.commands.send_login = AsyncMock(side_effect=_send_login)

        response = await prepare_repeater_connection(mc, contact, "bad")

        assert response.status == "error"
        assert response.authenticated is False
        assert "did not confirm this login" in (response.message or "")

    @pytest.mark.asyncio
    async def test_returns_timeout_when_no_login_response(self):
        mc = _mock_mc()
        contact = _make_contact()

        with patch("app.routers.repeaters.REPEATER_LOGIN_RESPONSE_TIMEOUT_SECONDS", 0):
            response = await prepare_repeater_connection(mc, contact, "secret")

        assert response.status == "timeout"
        assert response.authenticated is False
        assert "No login confirmation was heard from the repeater" in (response.message or "")


class TestRepeaterStatus:
    @pytest.mark.asyncio
    async def test_success_with_field_mapping(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.req_status_sync = AsyncMock(
            return_value={
                "bat": 4200,
                "tx_queue_len": 2,
                "noise_floor": -120,
                "last_rssi": -85,
                "last_snr": 7.5,
                "nb_recv": 1000,
                "nb_sent": 500,
                "airtime": 3600,
                "rx_airtime": 7200,
                "uptime": 86400,
                "sent_flood": 100,
                "sent_direct": 400,
                "recv_flood": 300,
                "recv_direct": 700,
                "flood_dups": 10,
                "direct_dups": 5,
                "full_evts": 0,
                "recv_errors": 42,
            }
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await repeater_status(KEY_A)

        assert response.battery_volts == 4.2
        assert response.tx_queue_len == 2
        assert response.noise_floor_dbm == -120
        assert response.last_rssi_dbm == -85
        assert response.last_snr_db == 7.5
        assert response.packets_received == 1000
        assert response.packets_sent == 500
        assert response.uptime_seconds == 86400
        assert response.sent_flood == 100
        assert response.recv_direct == 700
        assert response.recv_errors == 42

    @pytest.mark.asyncio
    async def test_408_on_timeout(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.req_status_sync = AsyncMock(return_value=None)

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await repeater_status(KEY_A)
        assert exc.value.status_code == 408

    @pytest.mark.asyncio
    async def test_400_not_repeater(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Client", contact_type=1)
        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await repeater_status(KEY_A)
        assert exc.value.status_code == 400


class TestRepeaterLppTelemetry:
    @pytest.mark.asyncio
    async def test_success_with_sensors(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.req_telemetry_sync = AsyncMock(
            return_value=[
                {"channel": 0, "type": "temperature", "value": 24.5},
                {"channel": 1, "type": "humidity", "value": 62.0},
                {
                    "channel": 2,
                    "type": "gps",
                    "value": {"latitude": 37.7, "longitude": -122.4, "altitude": 15.0},
                },
            ]
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await repeater_lpp_telemetry(KEY_A)

        assert len(response.sensors) == 3
        assert response.sensors[0].channel == 0
        assert response.sensors[0].type_name == "temperature"
        assert response.sensors[0].value == 24.5
        assert response.sensors[1].type_name == "humidity"
        assert response.sensors[1].value == 62.0
        assert response.sensors[2].type_name == "gps"
        assert isinstance(response.sensors[2].value, dict)
        assert response.sensors[2].value["latitude"] == 37.7

    @pytest.mark.asyncio
    async def test_empty_sensors(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.req_telemetry_sync = AsyncMock(return_value=[])

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await repeater_lpp_telemetry(KEY_A)

        assert response.sensors == []

    @pytest.mark.asyncio
    async def test_408_on_timeout(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.req_telemetry_sync = AsyncMock(return_value=None)

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await repeater_lpp_telemetry(KEY_A)
        assert exc.value.status_code == 408

    @pytest.mark.asyncio
    async def test_400_not_repeater(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Client", contact_type=1)
        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await repeater_lpp_telemetry(KEY_A)
        assert exc.value.status_code == 400


class TestRepeaterNeighbors:
    @pytest.mark.asyncio
    async def test_success_with_name_resolution(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        neighbor_key = "bb" * 32
        await _insert_contact(neighbor_key, name="NeighborNode", contact_type=1)

        mc.commands.fetch_all_neighbours = AsyncMock(
            return_value={
                "neighbours": [
                    {"pubkey": neighbor_key[:12], "snr": 9.0, "secs_ago": 5},
                    {"pubkey": "cccccccccccc", "snr": 3.0, "secs_ago": 120},
                ]
            }
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await repeater_neighbors(KEY_A)

        assert len(response.neighbors) == 2
        assert response.neighbors[0].name == "NeighborNode"
        assert response.neighbors[0].snr == 9.0
        assert response.neighbors[1].name is None
        assert response.neighbors[1].last_heard_seconds == 120

    @pytest.mark.asyncio
    async def test_empty_neighbors(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.fetch_all_neighbours = AsyncMock(return_value={"neighbours": []})

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await repeater_neighbors(KEY_A)

        assert response.neighbors == []

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.fetch_all_neighbours = AsyncMock(return_value=None)

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await repeater_neighbors(KEY_A)

        assert response.neighbors == []


class TestRepeaterAcl:
    @pytest.mark.asyncio
    async def test_success_with_permission_mapping(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        neighbor_key = "bb" * 32
        await _insert_contact(neighbor_key, name="Admin User", contact_type=1)

        mc.commands.req_acl_sync = AsyncMock(
            return_value=[
                {"key": neighbor_key[:12], "perm": 3},
                {"key": "dddddddddddd", "perm": 0},
            ]
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await repeater_acl(KEY_A)

        assert len(response.acl) == 2
        assert response.acl[0].name == "Admin User"
        assert response.acl[0].permission_name == "Admin"
        assert response.acl[1].name is None
        assert response.acl[1].permission_name == "Guest"

    @pytest.mark.asyncio
    async def test_empty_acl(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.req_acl_sync = AsyncMock(return_value=[])

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await repeater_acl(KEY_A)

        assert response.acl == []

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.req_acl_sync = AsyncMock(return_value=None)

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await repeater_acl(KEY_A)

        assert response.acl == []


class TestRepeaterRadioSettings:
    @pytest.mark.asyncio
    async def test_full_success(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)

        # Build responses for all 6 commands
        responses = [
            "v2.1.0",  # ver
            "915.0,250,7,5",  # get radio
            "20",  # get tx
            "0",  # get af
            "1",  # get repeat
            "3",  # get flood.max
        ]
        get_msg_results = [
            _radio_result(
                EventType.CONTACT_MSG_RECV,
                {"pubkey_prefix": KEY_A[:12], "text": text, "txt_type": 1},
            )
            for text in responses
        ]
        mc.commands.get_msg = AsyncMock(side_effect=get_msg_results)

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await repeater_radio_settings(KEY_A)

        assert response.firmware_version == "v2.1.0"
        assert response.radio == "915.0,250,7,5"
        assert response.tx_power == "20"
        assert response.airtime_factor == "0"
        assert response.repeat_enabled == "1"
        assert response.flood_max == "3"

    @pytest.mark.asyncio
    async def test_partial_failure(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)

        # First command succeeds, rest timeout
        first_response = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": KEY_A[:12], "text": "v2.0.0", "txt_type": 1},
        )
        no_msgs = _radio_result(EventType.NO_MORE_MSGS)
        mc.commands.get_msg = AsyncMock(side_effect=[first_response] + [no_msgs] * 50)

        # Provide clock ticks: first command succeeds quickly, others expire
        clock_ticks = [0.0, 0.1]  # First fetch succeeds
        for i in range(5):
            base = 100.0 * (i + 1)
            clock_ticks.extend([base, base + 5.0, base + 11.0])

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=clock_ticks),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await repeater_radio_settings(KEY_A)

        assert response.firmware_version == "v2.0.0"
        assert response.radio is None
        assert response.tx_power is None

    @pytest.mark.asyncio
    async def test_400_not_repeater(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Client", contact_type=1)
        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await repeater_radio_settings(KEY_A)
        assert exc.value.status_code == 400


class TestRepeaterNodeInfo:
    @pytest.mark.asyncio
    async def test_full_success(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)

        responses = [
            "MyRepeater",  # get name
            "40.7128",  # get lat
            "-74.0060",  # get lon
            "2025-02-25 14:30:00",  # clock
        ]
        get_msg_results = [
            _radio_result(
                EventType.CONTACT_MSG_RECV,
                {"pubkey_prefix": KEY_A[:12], "text": text, "txt_type": 1},
            )
            for text in responses
        ]
        mc.commands.get_msg = AsyncMock(side_effect=get_msg_results)

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await repeater_node_info(KEY_A)

        assert response.name == "MyRepeater"
        assert response.lat == "40.7128"
        assert response.lon == "-74.0060"
        assert response.clock_utc == "2025-02-25 14:30:00"

    @pytest.mark.asyncio
    async def test_partial_failure(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)

        first_response = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": KEY_A[:12], "text": "MyRepeater", "txt_type": 1},
        )
        no_msgs = _radio_result(EventType.NO_MORE_MSGS)
        mc.commands.get_msg = AsyncMock(side_effect=[first_response] + [no_msgs] * 50)

        clock_ticks = [0.0, 0.1]
        for i in range(3):
            base = 100.0 * (i + 1)
            clock_ticks.extend([base, base + 5.0, base + 11.0])

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=clock_ticks),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await repeater_node_info(KEY_A)

        assert response.name == "MyRepeater"
        assert response.lat is None
        assert response.lon is None
        assert response.clock_utc is None


class TestRepeaterAdvertIntervals:
    @pytest.mark.asyncio
    async def test_success(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)

        responses = [
            _radio_result(
                EventType.CONTACT_MSG_RECV,
                {"pubkey_prefix": KEY_A[:12], "text": "30", "txt_type": 1},
            ),
            _radio_result(
                EventType.CONTACT_MSG_RECV,
                {"pubkey_prefix": KEY_A[:12], "text": "120", "txt_type": 1},
            ),
        ]
        mc.commands.get_msg = AsyncMock(side_effect=responses)

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await repeater_advert_intervals(KEY_A)

        assert response.advert_interval == "30"
        assert response.flood_advert_interval == "120"

    @pytest.mark.asyncio
    async def test_timeout_returns_none_fields(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.get_msg = AsyncMock(return_value=_radio_result(EventType.NO_MORE_MSGS))

        clock_ticks = []
        for i in range(2):
            base = 100.0 * i
            clock_ticks.extend([base, base + 5.0, base + 11.0])

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=clock_ticks),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await repeater_advert_intervals(KEY_A)

        assert response.advert_interval is None
        assert response.flood_advert_interval is None


class TestRepeaterOwnerInfo:
    @pytest.mark.asyncio
    async def test_success(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)

        responses = [
            _radio_result(
                EventType.CONTACT_MSG_RECV,
                {
                    "pubkey_prefix": KEY_A[:12],
                    "text": "John Doe - Contact: john@example.com",
                    "txt_type": 1,
                },
            ),
            _radio_result(
                EventType.CONTACT_MSG_RECV,
                {"pubkey_prefix": KEY_A[:12], "text": "guestpw123", "txt_type": 1},
            ),
        ]
        mc.commands.get_msg = AsyncMock(side_effect=responses)

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await repeater_owner_info(KEY_A)

        assert response.owner_info == "John Doe - Contact: john@example.com"
        assert response.guest_password == "guestpw123"

    @pytest.mark.asyncio
    async def test_timeout_returns_none_fields(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.get_msg = AsyncMock(return_value=_radio_result(EventType.NO_MORE_MSGS))

        clock_ticks = []
        for i in range(2):
            base = 100.0 * i
            clock_ticks.extend([base, base + 5.0, base + 11.0])

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=clock_ticks),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await repeater_owner_info(KEY_A)

        assert response.owner_info is None
        assert response.guest_password is None


def _make_contact(
    public_key: str = KEY_A, name: str = "Repeater", contact_type: int = 2
) -> Contact:
    """Create a Contact model instance for testing."""
    return Contact(public_key=public_key, name=name, type=contact_type)


class TestBatchCliFetch:
    """Tests for the _batch_cli_fetch helper."""

    @pytest.mark.asyncio
    async def test_add_contact_error_raises_500(self):
        mc = _mock_mc()
        mc.commands.add_contact = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"err": "radio busy"})
        )

        contact = _make_contact()

        with patch.object(radio_manager, "_meshcore", mc):
            with pytest.raises(HTTPException) as exc:
                await _batch_cli_fetch(contact, "test_op", [("ver", "firmware_version")])

        assert exc.value.status_code == 422
        assert "Failed to add contact to radio" in exc.value.detail

    @pytest.mark.asyncio
    async def test_send_cmd_error_skips_field(self):
        mc = _mock_mc()
        mc.commands.add_contact = AsyncMock(return_value=_radio_result(EventType.OK))

        # First command fails, second succeeds
        mc.commands.send_cmd = AsyncMock(
            side_effect=[
                _radio_result(EventType.ERROR, {"err": "bad cmd"}),
                _radio_result(EventType.OK),
            ]
        )
        mc.commands.get_msg = AsyncMock(
            return_value=_radio_result(
                EventType.CONTACT_MSG_RECV,
                {"pubkey_prefix": KEY_A[:12], "text": "result2", "txt_type": 1},
            )
        )

        contact = _make_contact()

        with (
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            results = await _batch_cli_fetch(
                contact, "test_op", [("bad_cmd", "field_a"), ("good_cmd", "field_b")]
            )

        assert results["field_a"] is None  # skipped due to send error
        assert results["field_b"] == "result2"

    @pytest.mark.asyncio
    async def test_no_response_leaves_field_none(self):
        mc = _mock_mc()
        mc.commands.add_contact = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.commands.get_msg = AsyncMock(return_value=_radio_result(EventType.NO_MORE_MSGS))

        contact = _make_contact()

        with (
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=[0.0, 5.0, 11.0]),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            results = await _batch_cli_fetch(contact, "test_op", [("clock", "clock_output")])

        assert results["clock_output"] is None


class TestRepeaterAddContactError:
    """Test that repeater endpoints raise 500 when add_contact fails."""

    @pytest.mark.asyncio
    async def test_status_add_contact_error(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.add_contact = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"err": "radio busy"})
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await repeater_status(KEY_A)

        assert exc.value.status_code == 422
        assert "Failed to add contact to radio" in exc.value.detail

    @pytest.mark.asyncio
    async def test_lpp_telemetry_add_contact_error(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.add_contact = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"err": "radio busy"})
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await repeater_lpp_telemetry(KEY_A)

        assert exc.value.status_code == 422
        assert "Failed to add contact to radio" in exc.value.detail

    @pytest.mark.asyncio
    async def test_neighbors_add_contact_error(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.add_contact = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"err": "radio busy"})
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await repeater_neighbors(KEY_A)

        assert exc.value.status_code == 422
        assert "Failed to add contact to radio" in exc.value.detail

    @pytest.mark.asyncio
    async def test_acl_add_contact_error(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.add_contact = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"err": "radio busy"})
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await repeater_acl(KEY_A)

        assert exc.value.status_code == 422
        assert "Failed to add contact to radio" in exc.value.detail
