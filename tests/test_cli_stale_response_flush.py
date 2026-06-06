"""Tests for the pre-send CLI buffer flush.

These exercise the *real* ``_flush_pending_messages`` (unlike the route tests in
``test_repeater_routes.py``, which neutralize it), including the regression
guard that a stale buffered CLI response is not mis-attributed to a later
command.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from meshcore import EventType

from app.models import CommandRequest
from app.radio import radio_manager
from app.repository import ContactRepository
from app.routers import server_control
from app.routers.repeaters import send_repeater_command

KEY_A = "aa" * 32

# Patch target for the wall-clock wrapper used by fetch_contact_cli_response.
_MONOTONIC = "app.routers.server_control._monotonic"


@pytest.fixture(autouse=True)
def _reset_radio_state():
    """Save/restore radio_manager state so tests don't leak."""
    prev = radio_manager._meshcore
    prev_lock = radio_manager._operation_lock
    yield
    radio_manager._meshcore = prev
    radio_manager._operation_lock = prev_lock


def _radio_result(event_type=EventType.OK, payload=None):
    result = MagicMock()
    result.type = event_type
    result.payload = payload or {}
    return result


def _advancing_clock(start=0.0, step=0.1):
    t = start

    def _tick():
        nonlocal t
        val = t
        t += step
        return val

    return _tick


def _mock_mc():
    mc = MagicMock()
    mc.commands = MagicMock()
    mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
    mc.commands.get_msg = AsyncMock(return_value=_radio_result(EventType.NO_MORE_MSGS))
    mc.commands.add_contact = AsyncMock(return_value=_radio_result(EventType.OK))
    mc.subscribe = MagicMock(return_value=MagicMock(unsubscribe=MagicMock()))
    mc.stop_auto_message_fetching = AsyncMock()
    mc.start_auto_message_fetching = AsyncMock()
    return mc


async def _insert_contact(public_key: str, name: str = "Repeater", contact_type: int = 2):
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


class TestFlushPendingMessages:
    @pytest.mark.asyncio
    async def test_flush_drains_pending_buffer(self):
        mc = _mock_mc()
        with patch(
            "app.routers.server_control.drain_pending_messages",
            new_callable=AsyncMock,
            return_value=2,
        ) as drain:
            await server_control._flush_pending_messages(mc)

        drain.assert_awaited_once_with(mc)

    @pytest.mark.asyncio
    async def test_flush_swallows_drain_errors(self):
        """A flaky radio mid-flush must not abort the command."""
        mc = _mock_mc()
        with patch(
            "app.routers.server_control.drain_pending_messages",
            new_callable=AsyncMock,
            side_effect=RuntimeError("radio gone"),
        ):
            # Must not raise.
            await server_control._flush_pending_messages(mc)


class TestStaleResponseRegression:
    @pytest.mark.asyncio
    async def test_stale_buffered_cli_response_is_flushed_not_returned(self, test_db):
        """A stale CLI response buffered before the command is drained, so the
        fetch returns the fresh response rather than the leftover one.

        Without the pre-send flush, the fetch loop would pull ``stale`` (same
        contact, txt_type=1) and return it as the answer to ``get lat``.
        """
        mc = _mock_mc()
        await _insert_contact(KEY_A)

        stale = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": KEY_A[:12], "text": "stale-name", "txt_type": 1},
        )
        no_more = _radio_result(EventType.NO_MORE_MSGS)
        fresh = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": KEY_A[:12], "text": "fresh-lat", "txt_type": 1},
        )
        # Flush drains [stale, no_more]; the subsequent fetch then sees [fresh].
        mc.commands.get_msg = AsyncMock(side_effect=[stale, no_more, fresh])

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch("app.routers.server_control.asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="get lat"))

        assert response.command == "get lat"
        assert response.response == "fresh-lat"
