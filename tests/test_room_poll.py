"""Tests for room-poll subscriptions: three-state credential, config endpoints,
stored-credential login, and the poll loop cycle."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models import RoomLoginRequest, RoomPollConfigRequest
from app.repository import ContactRepository
from app.repository.room_poll import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
    RoomPollRepository,
)
from app.routers.rooms import (
    delete_room_poll,
    get_room_poll,
    room_login,
    set_room_poll,
)

ROOM_KEY = "cc" * 32


async def _insert_room(public_key: str = ROOM_KEY) -> None:
    await ContactRepository.upsert(
        {
            "public_key": public_key,
            "name": "Room Server",
            "type": 3,
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


class TestCredentialThreeState:
    async def test_unset_vs_guest_vs_password_are_distinct(self, test_db):
        await _insert_room()
        # unset
        sub = await RoomPollRepository.upsert(ROOM_KEY)
        assert sub.credential is None
        assert sub.has_credential is False

        # guest ("") is a real stored credential, not "unset"
        sub = await RoomPollRepository.upsert(ROOM_KEY, credential_action="set", credential="")
        assert sub.credential == ""
        assert sub.has_credential is True
        assert sub.is_guest_credential is True

        # password
        sub = await RoomPollRepository.upsert(
            ROOM_KEY, credential_action="set", credential="s3cret"
        )
        assert sub.credential == "s3cret"
        assert sub.has_credential is True
        assert sub.is_guest_credential is False

        # keep leaves it untouched; clear removes it
        sub = await RoomPollRepository.upsert(ROOM_KEY, enabled=False)
        assert sub.credential == "s3cret"
        sub = await RoomPollRepository.upsert(ROOM_KEY, credential_action="clear")
        assert sub.credential is None
        assert sub.has_credential is False

    async def test_guest_row_is_pollable(self, test_db):
        await _insert_room()
        await RoomPollRepository.upsert(
            ROOM_KEY, enabled=True, credential_action="set", credential=""
        )
        pollable = await RoomPollRepository.get_pollable()
        assert [s.room_key for s in pollable] == [ROOM_KEY]

    async def test_interval_is_floored(self, test_db):
        await _insert_room()
        sub = await RoomPollRepository.upsert(ROOM_KEY, interval_seconds=5)
        assert sub.interval_seconds == MIN_POLL_INTERVAL_SECONDS


class TestPollConfigEndpoints:
    async def test_status_omits_credential(self, test_db):
        await _insert_room()
        await RoomPollRepository.upsert(
            ROOM_KEY, enabled=True, credential_action="set", credential="s3cret"
        )
        status = await get_room_poll(ROOM_KEY)
        assert status.has_stored_credential is True
        assert status.is_guest_credential is False
        # The status model has no field that could carry the secret.
        assert "credential" not in status.model_dump()
        assert "s3cret" not in str(status.model_dump())

    async def test_enable_without_credential_is_rejected(self, test_db):
        await _insert_room()
        with pytest.raises(HTTPException) as exc:
            await set_room_poll(ROOM_KEY, RoomPollConfigRequest(enabled=True))
        assert exc.value.status_code == 400

    async def test_enable_with_guest_credential_ok(self, test_db):
        await _insert_room()
        status = await set_room_poll(
            ROOM_KEY,
            RoomPollConfigRequest(enabled=True, credential_action="set", credential=""),
        )
        assert status.poll_enabled is True
        assert status.is_guest_credential is True

    async def test_delete_clears(self, test_db):
        await _insert_room()
        await set_room_poll(
            ROOM_KEY,
            RoomPollConfigRequest(enabled=True, credential_action="set", credential="p"),
        )
        status = await delete_room_poll(ROOM_KEY)
        assert status.has_stored_credential is False
        assert status.poll_enabled is False
        assert await RoomPollRepository.get(ROOM_KEY) is None

    async def test_default_interval_when_unset(self, test_db):
        await _insert_room()
        status = await get_room_poll(ROOM_KEY)
        assert status.interval_seconds == DEFAULT_POLL_INTERVAL_SECONDS
        assert status.has_stored_credential is False


class TestStoredCredentialLogin:
    async def test_login_uses_stored_credential_without_exposing_it(self, test_db):
        await _insert_room()
        await RoomPollRepository.upsert(ROOM_KEY, credential_action="set", credential="roompw")

        captured = {}

        async def _prepare(mc, contact, password, *, label=None, **kwargs):
            captured["password"] = password
            return MagicMock(status="ok", authenticated=True, message=None)

        mc = MagicMock()
        op = MagicMock()
        op.__aenter__ = AsyncMock(return_value=mc)
        op.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.routers.rooms.radio_manager.require_connected", return_value=None),
            patch("app.routers.rooms.radio_manager.radio_operation", return_value=op),
            patch(
                "app.routers.rooms.prepare_authenticated_contact_connection",
                side_effect=_prepare,
            ),
        ):
            resp = await room_login(ROOM_KEY, RoomLoginRequest(use_stored_credential=True))

        assert resp.authenticated is True
        assert captured["password"] == "roompw"

    async def test_stored_guest_credential_logs_in_as_guest(self, test_db):
        await _insert_room()
        await RoomPollRepository.upsert(ROOM_KEY, credential_action="set", credential="")

        captured = {}

        async def _prepare(mc, contact, password, *, label=None, **kwargs):
            captured["password"] = password
            return MagicMock(status="ok", authenticated=True, message=None)

        op = MagicMock()
        op.__aenter__ = AsyncMock(return_value=MagicMock())
        op.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.routers.rooms.radio_manager.require_connected", return_value=None),
            patch("app.routers.rooms.radio_manager.radio_operation", return_value=op),
            patch(
                "app.routers.rooms.prepare_authenticated_contact_connection",
                side_effect=_prepare,
            ),
        ):
            await room_login(ROOM_KEY, RoomLoginRequest(use_stored_credential=True))

        # "" is guest, not "missing" — the login must proceed with an empty string.
        assert captured["password"] == ""

    async def test_use_stored_without_stored_credential_is_400(self, test_db):
        await _insert_room()
        with (
            patch("app.routers.rooms.radio_manager.require_connected", return_value=None),
        ):
            with pytest.raises(HTTPException) as exc:
                await room_login(ROOM_KEY, RoomLoginRequest(use_stored_credential=True))
        assert exc.value.status_code == 400


class TestPollLoopCycle:
    async def test_login_failure_disables_polling(self, test_db):
        from app import radio_sync

        await _insert_room()
        sub = await RoomPollRepository.upsert(
            ROOM_KEY, enabled=True, credential_action="set", credential="wrong"
        )

        op = MagicMock()
        op.__aenter__ = AsyncMock(return_value=MagicMock())
        op.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(radio_sync.radio_manager, "radio_operation", return_value=op),
            patch(
                "app.routers.server_control.prepare_authenticated_contact_connection",
                new=AsyncMock(
                    return_value=MagicMock(
                        status="error", authenticated=False, message="bad password"
                    )
                ),
            ),
        ):
            await radio_sync._poll_one_room(sub)

        after = await RoomPollRepository.get(ROOM_KEY)
        assert after.poll_enabled is False
        assert after.last_result == "login_failed"

    async def test_success_drains_and_records(self, test_db):
        from app import radio_sync

        await _insert_room()
        sub = await RoomPollRepository.upsert(
            ROOM_KEY, enabled=True, credential_action="set", credential="ok"
        )

        op = MagicMock()
        op.__aenter__ = AsyncMock(return_value=MagicMock())
        op.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(radio_sync.radio_manager, "radio_operation", return_value=op),
            patch(
                "app.routers.server_control.prepare_authenticated_contact_connection",
                new=AsyncMock(
                    return_value=MagicMock(status="ok", authenticated=True, message=None)
                ),
            ),
            patch.object(radio_sync, "poll_for_messages", new=AsyncMock(return_value=2)),
        ):
            await radio_sync._poll_one_room(sub)

        after = await RoomPollRepository.get(ROOM_KEY)
        assert after.poll_enabled is True
        assert after.consecutive_errors == 0
        assert after.last_poll_at is not None
        assert "2 drained" in after.last_result

    async def test_timeout_keeps_enabled_and_counts_error(self, test_db):
        from app import radio_sync

        await _insert_room()
        sub = await RoomPollRepository.upsert(
            ROOM_KEY, enabled=True, credential_action="set", credential="ok"
        )

        op = MagicMock()
        op.__aenter__ = AsyncMock(return_value=MagicMock())
        op.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(radio_sync.radio_manager, "radio_operation", return_value=op),
            patch(
                "app.routers.server_control.prepare_authenticated_contact_connection",
                new=AsyncMock(
                    return_value=MagicMock(
                        status="timeout", authenticated=False, message="no reply"
                    )
                ),
            ),
        ):
            await radio_sync._poll_one_room(sub)

        after = await RoomPollRepository.get(ROOM_KEY)
        # A timeout is transient: stay enabled, advance backoff.
        assert after.poll_enabled is True
        assert after.consecutive_errors == 1
        assert after.last_result == "timeout"

    def test_backoff_widens_interval(self):
        from app import radio_sync

        base = MagicMock(interval_seconds=1200, consecutive_errors=0, last_poll_at=None)
        assert radio_sync._room_poll_effective_interval(base) == 1200
        base.consecutive_errors = 3
        assert radio_sync._room_poll_effective_interval(base) == 1200 * 8
        base.consecutive_errors = 10  # capped
        assert radio_sync._room_poll_effective_interval(base) == 1200 * 8
