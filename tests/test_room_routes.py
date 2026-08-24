"""Tests for room-server contact routes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType

from app.models import CommandRequest, RoomLoginRequest
from app.radio import radio_manager
from app.repository import ContactRepository
from app.routers.repeaters import send_repeater_command
from app.routers.rooms import room_acl, room_login, room_status

ROOM_KEY = "cc" * 32
AUTHOR_KEY = "12345678" + ("dd" * 28)


@pytest.fixture(autouse=True)
def _reset_radio_state():
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


def _mock_mc():
    mc = MagicMock()
    mc.commands = MagicMock()
    mc.commands.send_login = AsyncMock(return_value=_radio_result(EventType.MSG_SENT))
    mc.commands.req_status_sync = AsyncMock()
    mc.commands.req_acl_sync = AsyncMock()
    mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
    mc.commands.get_msg = AsyncMock()
    mc.commands.add_contact = AsyncMock(return_value=_radio_result(EventType.OK))
    mc.subscribe = MagicMock(return_value=MagicMock(unsubscribe=MagicMock()))
    mc.stop_auto_message_fetching = AsyncMock()
    mc.start_auto_message_fetching = AsyncMock()
    return mc


async def _insert_contact(public_key: str, name: str, contact_type: int):
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


class TestRoomLogin:
    @pytest.mark.asyncio
    async def test_room_login_success(self, test_db):
        mc = _mock_mc()
        await _insert_contact(ROOM_KEY, name="Room Server", contact_type=3)
        subscriptions: dict[EventType, tuple[object, object]] = {}

        def _subscribe(event_type, callback, attribute_filters=None):
            subscriptions[event_type] = (callback, attribute_filters)
            return MagicMock(unsubscribe=MagicMock())

        async def _send_login(*args, **kwargs):
            callback, _filters = subscriptions[EventType.LOGIN_SUCCESS]
            callback(_radio_result(EventType.LOGIN_SUCCESS, {"pubkey_prefix": ROOM_KEY[:12]}))
            return _radio_result(EventType.MSG_SENT)

        mc.subscribe = MagicMock(side_effect=_subscribe)
        mc.commands.send_login = AsyncMock(side_effect=_send_login)

        with (
            patch("app.routers.rooms.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await room_login(ROOM_KEY, RoomLoginRequest(password="hello"))

        assert response.status == "ok"
        assert response.authenticated is True

    @pytest.mark.asyncio
    async def test_room_login_rejects_non_room(self, test_db):
        mc = _mock_mc()
        await _insert_contact(ROOM_KEY, name="Client", contact_type=1)

        with (
            patch("app.routers.rooms.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await room_login(ROOM_KEY, RoomLoginRequest(password="hello"))

        assert exc.value.status_code == 400


class TestRoomStatus:
    @pytest.mark.asyncio
    async def test_room_status_maps_fields(self, test_db):
        mc = _mock_mc()
        await _insert_contact(ROOM_KEY, name="Room Server", contact_type=3)
        mc.commands.req_status_sync = AsyncMock(
            return_value={
                "bat": 4025,
                "tx_queue_len": 1,
                "noise_floor": -118,
                "last_rssi": -82,
                "last_snr": 6.0,
                "nb_recv": 80,
                "nb_sent": 40,
                "airtime": 120,
                "rx_airtime": 240,
                "uptime": 600,
                "sent_flood": 5,
                "sent_direct": 35,
                "recv_flood": 7,
                "recv_direct": 73,
                "flood_dups": 2,
                "direct_dups": 1,
                "full_evts": 0,
                "recv_errors": 7,
            }
        )

        with (
            patch("app.routers.rooms.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await room_status(ROOM_KEY)

        assert response.battery_volts == 4.025
        assert response.packets_received == 80
        assert response.recv_direct == 73
        assert response.recv_errors == 7

    @pytest.mark.asyncio
    async def test_room_acl_maps_entries(self, test_db):
        mc = _mock_mc()
        await _insert_contact(ROOM_KEY, name="Room Server", contact_type=3)
        await _insert_contact(AUTHOR_KEY, name="Author", contact_type=1)
        mc.commands.req_acl_sync = AsyncMock(return_value=[{"key": AUTHOR_KEY[:12], "perm": 3}])

        with (
            patch("app.routers.rooms.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await room_acl(ROOM_KEY)

        assert len(response.acl) == 1
        assert response.acl[0].name == "Author"
        assert response.acl[0].permission_name == "Admin"


class TestRoomCommandReuse:
    @pytest.mark.asyncio
    async def test_generic_command_route_accepts_room_servers(self, test_db):
        mc = _mock_mc()
        await _insert_contact(ROOM_KEY, name="Room Server", contact_type=3)
        # The CLI send path now flushes the radio buffer before sending, so get_msg
        # must report an empty buffer (NO_MORE_MSGS) for that drain to terminate;
        # the command's actual reply is returned by the subsequent fetch.
        mc.commands.get_msg = AsyncMock(
            side_effect=[
                _radio_result(EventType.NO_MORE_MSGS),
                _radio_result(
                    EventType.CONTACT_MSG_RECV,
                    {"pubkey_prefix": ROOM_KEY[:12], "text": "> ok", "txt_type": 1},
                ),
            ]
        )

        with (
            patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await send_repeater_command(ROOM_KEY, CommandRequest(command="ver"))

        assert response.response == "ok"
