"""Tests for radio_sync module.

These tests verify the polling pause mechanism, radio time sync,
contact/channel sync operations, and default channel management.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from meshcore import EventType

from app.models import Favorite
from app.radio import RadioManager, radio_manager
from app.radio_sync import (
    _message_poll_loop,
    _periodic_advert_loop,
    _periodic_sync_loop,
    ensure_contact_on_radio,
    is_polling_paused,
    pause_polling,
    sync_radio_time,
    sync_recent_contacts_to_radio,
)
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
    MessageRepository,
)


@pytest.fixture(autouse=True)
def reset_sync_state():
    """Reset polling pause state, sync timestamp, and radio_manager before/after each test."""
    import app.radio_sync as radio_sync

    prev_mc = radio_manager._meshcore
    prev_lock = radio_manager._operation_lock

    radio_sync._polling_pause_count = 0
    radio_sync._last_contact_sync = 0.0
    yield
    radio_sync._polling_pause_count = 0
    radio_sync._last_contact_sync = 0.0
    radio_manager._meshcore = prev_mc
    radio_manager._operation_lock = prev_lock


KEY_A = "aa" * 32
KEY_B = "bb" * 32


async def _insert_contact(
    public_key=KEY_A,
    name="Alice",
    on_radio=False,
    contact_type=0,
    last_contacted=None,
    last_advert=None,
    last_path=None,
    last_path_len=-1,
    out_path_hash_mode=0,
):
    """Insert a contact into the test database."""
    await ContactRepository.upsert(
        {
            "public_key": public_key,
            "name": name,
            "type": contact_type,
            "flags": 0,
            "last_path": last_path,
            "last_path_len": last_path_len,
            "out_path_hash_mode": out_path_hash_mode,
            "last_advert": last_advert,
            "lat": None,
            "lon": None,
            "last_seen": None,
            "on_radio": on_radio,
            "last_contacted": last_contacted,
        }
    )


class TestPollingPause:
    """Test the polling pause mechanism."""

    def test_initially_not_paused(self):
        """Polling is not paused by default."""
        assert not is_polling_paused()

    @pytest.mark.asyncio
    async def test_pause_polling_pauses(self):
        """pause_polling context manager pauses polling."""
        assert not is_polling_paused()

        async with pause_polling():
            assert is_polling_paused()

        assert not is_polling_paused()

    @pytest.mark.asyncio
    async def test_nested_pause_stays_paused(self):
        """Nested pause_polling contexts keep polling paused until all exit."""
        assert not is_polling_paused()

        async with pause_polling():
            assert is_polling_paused()

            async with pause_polling():
                assert is_polling_paused()

            # Still paused - outer context active
            assert is_polling_paused()

        # Now unpaused - all contexts exited
        assert not is_polling_paused()

    @pytest.mark.asyncio
    async def test_pause_resumes_on_exception(self):
        """Polling resumes even if exception occurs in context."""
        try:
            async with pause_polling():
                assert is_polling_paused()
                raise ValueError("Test error")
        except ValueError:
            pass

        # Should be unpaused despite exception
        assert not is_polling_paused()

    @pytest.mark.asyncio
    async def test_nested_pause_resumes_correctly_on_inner_exception(self):
        """Nested contexts handle exceptions correctly."""
        async with pause_polling():
            try:
                async with pause_polling():
                    assert is_polling_paused()
                    raise ValueError("Inner error")
            except ValueError:
                pass

            # Outer context still active
            assert is_polling_paused()

        # All contexts exited
        assert not is_polling_paused()


class TestSyncRadioTime:
    """Test the radio time sync function."""

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        """sync_radio_time returns True when time is set successfully."""
        mock_mc = MagicMock()
        mock_mc.commands.set_time = AsyncMock()

        result = await sync_radio_time(mock_mc)

        assert result is True
        mock_mc.commands.set_time.assert_called_once()
        # Verify timestamp is reasonable (within last few seconds)
        call_args = mock_mc.commands.set_time.call_args[0][0]
        import time

        assert abs(call_args - int(time.time())) < 5

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        """sync_radio_time returns False and doesn't raise on error."""
        mock_mc = MagicMock()
        mock_mc.commands.set_time = AsyncMock(side_effect=Exception("Radio error"))

        result = await sync_radio_time(mock_mc)

        assert result is False


class TestSyncRecentContactsToRadio:
    """Test the sync_recent_contacts_to_radio function."""

    @pytest.mark.asyncio
    async def test_loads_favorite_contacts_not_on_radio(self, test_db):
        """Favorite contacts not on radio are added via add_contact."""
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)
        await _insert_contact(KEY_B, "Bob", last_contacted=1000)
        await AppSettingsRepository.update(
            favorites=[
                Favorite(type="contact", id=KEY_A),
                Favorite(type="contact", id=KEY_B),
            ]
        )

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 2
        # Verify contacts are now marked as on_radio in DB
        alice = await ContactRepository.get_by_key(KEY_A)
        bob = await ContactRepository.get_by_key(KEY_B)
        assert alice.on_radio is True
        assert bob.on_radio is True

    @pytest.mark.asyncio
    async def test_fills_remaining_slots_with_recently_contacted_then_advertised(self, test_db):
        """Fill order is favorites, then recent contacts, then recent adverts."""
        await _insert_contact(KEY_A, "Alice", last_contacted=100)
        await _insert_contact(KEY_B, "Bob", last_contacted=2000)
        await _insert_contact("cc" * 32, "Carol", last_contacted=1000)
        await _insert_contact("dd" * 32, "Dave", last_advert=3000)
        await _insert_contact("ee" * 32, "Eve", last_advert=2500)

        await AppSettingsRepository.update(
            max_radio_contacts=5, favorites=[Favorite(type="contact", id=KEY_A)]
        )

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 4
        loaded_keys = [
            call.args[0]["public_key"] for call in mock_mc.commands.add_contact.call_args_list
        ]
        assert loaded_keys == [KEY_A, KEY_B, "cc" * 32, "dd" * 32]

    @pytest.mark.asyncio
    async def test_favorites_can_exceed_non_favorite_refill_target(self, test_db):
        """Favorites are reloaded even when they exceed the 80% background refill target."""
        favorite_keys = ["aa" * 32, "bb" * 32, "cc" * 32, "dd" * 32]
        for index, key in enumerate(favorite_keys):
            await _insert_contact(key, f"Favorite{index}", last_contacted=2000 - index)

        await AppSettingsRepository.update(
            max_radio_contacts=4,
            favorites=[Favorite(type="contact", id=key) for key in favorite_keys],
        )

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 4
        loaded_keys = [
            call.args[0]["public_key"] for call in mock_mc.commands.add_contact.call_args_list
        ]
        assert loaded_keys == favorite_keys

    @pytest.mark.asyncio
    async def test_advert_fill_skips_repeaters(self, test_db):
        """Recent advert fallback only considers non-repeaters."""
        await _insert_contact(KEY_A, "Alice", last_advert=3000, contact_type=2)
        await _insert_contact(KEY_B, "Bob", last_advert=2000, contact_type=1)

        await AppSettingsRepository.update(max_radio_contacts=1, favorites=[])

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 1
        payload = mock_mc.commands.add_contact.call_args.args[0]
        assert payload["public_key"] == KEY_B

    @pytest.mark.asyncio
    async def test_duplicate_favorite_not_loaded_twice(self, test_db):
        """Duplicate favorite entries still load the contact only once."""
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)
        await _insert_contact(KEY_B, "Bob", last_contacted=1000)

        await AppSettingsRepository.update(
            max_radio_contacts=2,
            favorites=[
                Favorite(type="contact", id=KEY_A),
                Favorite(type="contact", id=KEY_A),
            ],
        )

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 2
        loaded_keys = [
            call.args[0]["public_key"] for call in mock_mc.commands.add_contact.call_args_list
        ]
        assert loaded_keys == [KEY_A, KEY_B]

    @pytest.mark.asyncio
    async def test_skips_contacts_already_on_radio(self, test_db):
        """Contacts already on radio are counted but not re-added."""
        await _insert_contact(KEY_A, "Alice", on_radio=True)
        await AppSettingsRepository.update(favorites=[Favorite(type="contact", id=KEY_A)])

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=MagicMock())  # Found
        mock_mc.commands.add_contact = AsyncMock()

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 0
        assert result["already_on_radio"] == 1
        mock_mc.commands.add_contact.assert_not_called()

    @pytest.mark.asyncio
    async def test_throttled_when_called_quickly(self, test_db):
        """Second call within throttle window returns throttled result."""
        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)

        radio_manager._meshcore = mock_mc

        # First call succeeds
        result1 = await sync_recent_contacts_to_radio()
        assert "throttled" not in result1

        # Second call is throttled
        result2 = await sync_recent_contacts_to_radio()
        assert result2["throttled"] is True
        assert result2["loaded"] == 0

    @pytest.mark.asyncio
    async def test_force_bypasses_throttle(self, test_db):
        """force=True bypasses the throttle window."""
        mock_mc = MagicMock()

        radio_manager._meshcore = mock_mc

        # First call
        await sync_recent_contacts_to_radio()

        # Forced second call is not throttled
        result = await sync_recent_contacts_to_radio(force=True)
        assert "throttled" not in result

    @pytest.mark.asyncio
    async def test_not_connected_returns_error(self):
        """Returns error when radio is not connected."""
        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.meshcore = None

            result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_marks_on_radio_when_found_but_not_flagged(self, test_db):
        """Contact found on radio but not flagged gets set_on_radio(True)."""
        await _insert_contact(KEY_A, "Alice", on_radio=False)
        await AppSettingsRepository.update(favorites=[Favorite(type="contact", id=KEY_A)])

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=MagicMock())  # Found

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["already_on_radio"] == 1
        # Should update the flag since contact.on_radio was False
        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact.on_radio is True

    @pytest.mark.asyncio
    async def test_handles_add_failure(self, test_db):
        """Failed add_contact increments the failed counter."""
        await _insert_contact(KEY_A, "Alice")
        await AppSettingsRepository.update(favorites=[Favorite(type="contact", id=KEY_A)])

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.ERROR
        mock_result.payload = {"error": "Radio full"}
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 0
        assert result["failed"] == 1

    @pytest.mark.asyncio
    async def test_add_contact_preserves_explicit_multibyte_hash_mode(self, test_db):
        """Radio offload uses the stored hash mode rather than inferring from path bytes."""
        await _insert_contact(
            KEY_A,
            "Alice",
            last_contacted=2000,
            last_path="aa00bb00",
            last_path_len=2,
            out_path_hash_mode=1,
        )
        await AppSettingsRepository.update(favorites=[Favorite(type="contact", id=KEY_A)])

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 1
        payload = mock_mc.commands.add_contact.call_args.args[0]
        assert payload["public_key"] == KEY_A
        assert payload["out_path"] == "aa00bb00"
        assert payload["out_path_len"] == 2
        assert payload["out_path_hash_mode"] == 1

    @pytest.mark.asyncio
    async def test_add_contact_decodes_legacy_packed_path_len(self, test_db):
        """Legacy signed packed path bytes are normalized before add_contact."""
        await _insert_contact(
            KEY_A,
            "Alice",
            last_contacted=2000,
            last_path="3f3f69de1c7b7e7662",
            last_path_len=-125,
            out_path_hash_mode=2,
        )
        await AppSettingsRepository.update(favorites=[Favorite(type="contact", id=KEY_A)])

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 1
        payload = mock_mc.commands.add_contact.call_args.args[0]
        assert payload["out_path"] == "3f3f69de1c7b7e7662"
        assert payload["out_path_len"] == 3
        assert payload["out_path_hash_mode"] == 2

    @pytest.mark.asyncio
    async def test_mc_param_bypasses_lock_acquisition(self, test_db):
        """When mc is passed, the function uses it directly without acquiring radio_operation.

        This tests the BUG-1 fix: sync_and_offload_all already holds the lock,
        so it passes mc directly to avoid deadlock (asyncio.Lock is not reentrant).
        """
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)
        await AppSettingsRepository.update(favorites=[Favorite(type="contact", id=KEY_A)])

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        # Make radio_operation raise if called — it should NOT be called
        # when mc is provided
        def radio_operation_should_not_be_called(*args, **kwargs):
            raise AssertionError("radio_operation should not be called when mc is passed")

        with patch.object(
            radio_manager, "radio_operation", side_effect=radio_operation_should_not_be_called
        ):
            result = await sync_recent_contacts_to_radio(mc=mock_mc)

        assert result["loaded"] == 1
        mock_mc.commands.add_contact.assert_called_once()

    @pytest.mark.asyncio
    async def test_mc_param_still_respects_throttle(self):
        """When mc is passed but throttle is active (not forced), it should still return throttled."""
        mock_mc = MagicMock()

        # First call to set _last_contact_sync
        radio_manager._meshcore = mock_mc
        await sync_recent_contacts_to_radio()

        # Second call with mc= but no force — should still be throttled
        result = await sync_recent_contacts_to_radio(mc=mock_mc)
        assert result["throttled"] is True
        assert result["loaded"] == 0

    @pytest.mark.asyncio
    async def test_uses_post_lock_meshcore_after_swap(self, test_db):
        """If _meshcore is swapped between pre-check and lock acquisition,
        the function uses the new (post-lock) instance, not the stale one."""
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)
        await AppSettingsRepository.update(favorites=[Favorite(type="contact", id=KEY_A)])

        old_mc = MagicMock(name="old_mc")
        new_mc = MagicMock(name="new_mc")
        new_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        new_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        # Pre-check sees old_mc (truthy, passes is_connected guard)
        radio_manager._meshcore = old_mc
        # Simulate reconnect swapping _meshcore before lock acquisition
        radio_manager._meshcore = new_mc

        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 1
        # new_mc was used, not old_mc
        new_mc.commands.add_contact.assert_called_once()
        old_mc.commands.add_contact.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_contact_on_radio_loads_single_contact_even_when_not_favorited(
        self, test_db
    ):
        """Targeted sync loads one contact without needing it in favorites."""
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await ensure_contact_on_radio(KEY_A, force=True)

        assert result["loaded"] == 1
        payload = mock_mc.commands.add_contact.call_args.args[0]
        assert payload["public_key"] == KEY_A


class TestSyncAndOffloadContacts:
    """Test sync_and_offload_contacts: pull contacts from radio, save to DB, remove from radio."""

    @pytest.mark.asyncio
    async def test_syncs_and_removes_contacts(self, test_db):
        """Contacts are upserted to DB and removed from radio."""
        from app.radio_sync import sync_and_offload_contacts

        contact_payload = {
            KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0},
            KEY_B: {"adv_name": "Bob", "type": 1, "flags": 0},
        }

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT  # Not ERROR
        mock_get_result.payload = contact_payload

        mock_remove_result = MagicMock()
        mock_remove_result.type = EventType.OK

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        mock_mc.commands.remove_contact = AsyncMock(return_value=mock_remove_result)

        result = await sync_and_offload_contacts(mock_mc)

        assert result["synced"] == 2
        assert result["removed"] == 2

        # Verify contacts are in real DB
        alice = await ContactRepository.get_by_key(KEY_A)
        bob = await ContactRepository.get_by_key(KEY_B)
        assert alice is not None
        assert alice.name == "Alice"
        assert bob is not None
        assert bob.name == "Bob"

    @pytest.mark.asyncio
    async def test_claims_prefix_messages_for_each_contact(self, test_db):
        """Prefix message claims still complete via scheduled reconciliation tasks."""
        from app.radio_sync import sync_and_offload_contacts

        # Pre-insert a message with a prefix key that matches KEY_A
        await MessageRepository.create(
            msg_type="PRIV",
            text="Hello from prefix",
            received_at=1700000000,
            conversation_key=KEY_A[:12],
            sender_timestamp=1700000000,
        )

        contact_payload = {KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0}}

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT
        mock_get_result.payload = contact_payload

        mock_remove_result = MagicMock()
        mock_remove_result.type = EventType.OK

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        mock_mc.commands.remove_contact = AsyncMock(return_value=mock_remove_result)

        created_tasks: list[asyncio.Task] = []
        real_create_task = asyncio.create_task

        def _capture_task(coro):
            task = real_create_task(coro)
            created_tasks.append(task)
            return task

        with patch("app.radio_sync.asyncio.create_task", side_effect=_capture_task):
            await sync_and_offload_contacts(mock_mc)

        await asyncio.gather(*created_tasks)

        # Verify the prefix message was claimed (promoted to full key)
        messages = await MessageRepository.get_all(conversation_key=KEY_A)
        assert len(messages) == 1
        assert messages[0].conversation_key == KEY_A.lower()

    @pytest.mark.asyncio
    async def test_reconciliation_does_not_block_contact_removal(self, test_db):
        """Slow reconciliation work is scheduled in background, not awaited inline."""
        from app.radio_sync import sync_and_offload_contacts

        contact_payload = {KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0}}

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT
        mock_get_result.payload = contact_payload

        mock_remove_result = MagicMock()
        mock_remove_result.type = EventType.OK

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        mock_mc.commands.remove_contact = AsyncMock(return_value=mock_remove_result)

        reconcile_started = asyncio.Event()
        reconcile_release = asyncio.Event()
        created_tasks: list[asyncio.Task] = []
        real_create_task = asyncio.create_task

        async def _slow_reconcile(*, public_key: str, contact_name: str | None, log):
            del public_key, contact_name, log
            reconcile_started.set()
            await reconcile_release.wait()

        def _capture_task(coro):
            task = real_create_task(coro)
            created_tasks.append(task)
            return task

        with (
            patch("app.radio_sync.reconcile_contact_messages", side_effect=_slow_reconcile),
            patch("app.radio_sync.asyncio.create_task", side_effect=_capture_task),
        ):
            result = await sync_and_offload_contacts(mock_mc)
            await asyncio.sleep(0)

        assert result["synced"] == 1
        assert result["removed"] == 1
        assert reconcile_started.is_set() is True
        assert created_tasks and created_tasks[0].done() is False
        mock_mc.commands.remove_contact.assert_awaited_once()

        reconcile_release.set()
        await asyncio.gather(*created_tasks)

    @pytest.mark.asyncio
    async def test_handles_remove_failure_gracefully(self, test_db):
        """Failed remove_contact logs warning but continues to next contact."""
        from app.radio_sync import sync_and_offload_contacts

        contact_payload = {
            KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0},
            KEY_B: {"adv_name": "Bob", "type": 1, "flags": 0},
        }

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT
        mock_get_result.payload = contact_payload

        mock_fail_result = MagicMock()
        mock_fail_result.type = EventType.ERROR
        mock_fail_result.payload = {"error": "busy"}

        mock_ok_result = MagicMock()
        mock_ok_result.type = EventType.OK

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        # First remove fails, second succeeds
        mock_mc.commands.remove_contact = AsyncMock(side_effect=[mock_fail_result, mock_ok_result])

        result = await sync_and_offload_contacts(mock_mc)

        # Both contacts synced, but only one removed successfully
        assert result["synced"] == 2
        assert result["removed"] == 1

    @pytest.mark.asyncio
    async def test_handles_remove_exception_gracefully(self, test_db):
        """Exception during remove_contact is caught and processing continues."""
        from app.radio_sync import sync_and_offload_contacts

        contact_payload = {KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0}}

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT
        mock_get_result.payload = contact_payload

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        mock_mc.commands.remove_contact = AsyncMock(side_effect=Exception("Timeout"))

        result = await sync_and_offload_contacts(mock_mc)

        assert result["synced"] == 1
        assert result["removed"] == 0

    @pytest.mark.asyncio
    async def test_returns_error_when_get_contacts_fails(self):
        """Error result from get_contacts returns error dict."""
        from app.radio_sync import sync_and_offload_contacts

        mock_error_result = MagicMock()
        mock_error_result.type = EventType.ERROR
        mock_error_result.payload = {"error": "radio busy"}

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_error_result)

        result = await sync_and_offload_contacts(mock_mc)

        assert result["synced"] == 0
        assert result["removed"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_upserts_with_on_radio_false(self, test_db):
        """Contacts are upserted with on_radio=False (being removed from radio)."""
        from app.radio_sync import sync_and_offload_contacts

        contact_payload = {KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0}}

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT
        mock_get_result.payload = contact_payload

        mock_remove_result = MagicMock()
        mock_remove_result.type = EventType.OK

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        mock_mc.commands.remove_contact = AsyncMock(return_value=mock_remove_result)

        await sync_and_offload_contacts(mock_mc)

        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact is not None
        assert contact.on_radio is False

    @pytest.mark.asyncio
    async def test_evicts_removed_contacts_from_library_cache(self, test_db):
        """Successfully removed contacts are evicted from mc._contacts.

        The MeshCore library's remove_contact() command does not update the
        library's in-memory _contacts cache. If we don't evict manually,
        sync_recent_contacts_to_radio() will find stale entries via
        get_contact_by_key_prefix() and skip re-adding contacts to the radio.
        """
        from app.radio_sync import sync_and_offload_contacts

        contact_payload = {
            KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0},
            KEY_B: {"adv_name": "Bob", "type": 1, "flags": 0},
        }

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT
        mock_get_result.payload = contact_payload

        mock_remove_result = MagicMock()
        mock_remove_result.type = EventType.OK

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        mock_mc.commands.remove_contact = AsyncMock(return_value=mock_remove_result)
        # Seed the library's in-memory cache with the same contacts —
        # simulating what happens after get_contacts() populates it.
        mock_mc._contacts = {
            KEY_A: {"public_key": KEY_A, "adv_name": "Alice"},
            KEY_B: {"public_key": KEY_B, "adv_name": "Bob"},
        }

        await sync_and_offload_contacts(mock_mc)

        # Both contacts should have been evicted from the library cache
        assert KEY_A not in mock_mc._contacts
        assert KEY_B not in mock_mc._contacts
        assert mock_mc._contacts == {}

    @pytest.mark.asyncio
    async def test_failed_remove_does_not_evict_from_library_cache(self, test_db):
        """Contacts that fail to remove from radio stay in mc._contacts.

        We only evict from the cache on successful removal — if the radio
        still has the contact, the cache should reflect that.
        """
        from app.radio_sync import sync_and_offload_contacts

        contact_payload = {
            KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0},
        }

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT
        mock_get_result.payload = contact_payload

        mock_fail_result = MagicMock()
        mock_fail_result.type = EventType.ERROR
        mock_fail_result.payload = {"error": "busy"}

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        mock_mc.commands.remove_contact = AsyncMock(return_value=mock_fail_result)
        mock_mc._contacts = {
            KEY_A: {"public_key": KEY_A, "adv_name": "Alice"},
        }

        await sync_and_offload_contacts(mock_mc)

        # Contact should still be in the cache since removal failed
        assert KEY_A in mock_mc._contacts


class TestSyncAndOffloadChannels:
    """Test sync_and_offload_channels: pull channels from radio, save to DB, clear from radio."""

    @pytest.mark.asyncio
    async def test_syncs_valid_channel_and_clears(self, test_db):
        """Valid channel is upserted to DB and cleared from radio."""
        from app.radio_sync import sync_and_offload_channels

        channel_result = MagicMock()
        channel_result.type = EventType.CHANNEL_INFO
        channel_result.payload = {
            "channel_name": "#general",
            "channel_secret": bytes.fromhex("8B3387E9C5CDEA6AC9E5EDBAA115CD72"),
        }

        # All other slots return non-CHANNEL_INFO
        empty_result = MagicMock()
        empty_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(side_effect=[channel_result] + [empty_result] * 39)

        clear_result = MagicMock()
        clear_result.type = EventType.OK
        mock_mc.commands.set_channel = AsyncMock(return_value=clear_result)

        result = await sync_and_offload_channels(mock_mc)

        assert result["synced"] == 1
        assert result["cleared"] == 1

        # Verify channel is in real DB
        channel = await ChannelRepository.get_by_key("8B3387E9C5CDEA6AC9E5EDBAA115CD72")
        assert channel is not None
        assert channel.name == "#general"
        assert channel.is_hashtag is True
        assert channel.on_radio is False

    @pytest.mark.asyncio
    async def test_skips_empty_channel_name(self):
        """Channels with empty names are skipped."""
        from app.radio_sync import sync_and_offload_channels

        empty_name_result = MagicMock()
        empty_name_result.type = EventType.CHANNEL_INFO
        empty_name_result.payload = {
            "channel_name": "",
            "channel_secret": bytes(16),
        }

        other_result = MagicMock()
        other_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(
            side_effect=[empty_name_result] + [other_result] * 39
        )

        result = await sync_and_offload_channels(mock_mc)

        assert result["synced"] == 0
        assert result["cleared"] == 0

    @pytest.mark.asyncio
    async def test_skips_channel_with_zero_key(self):
        """Channels with all-zero secret key are skipped."""
        from app.radio_sync import sync_and_offload_channels

        zero_key_result = MagicMock()
        zero_key_result.type = EventType.CHANNEL_INFO
        zero_key_result.payload = {
            "channel_name": "SomeChannel",
            "channel_secret": bytes(16),  # All zeros
        }

        other_result = MagicMock()
        other_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(
            side_effect=[zero_key_result] + [other_result] * 39
        )

        result = await sync_and_offload_channels(mock_mc)

        assert result["synced"] == 0

    @pytest.mark.asyncio
    async def test_non_hashtag_channel_detected(self, test_db):
        """Channel without '#' prefix has is_hashtag=False."""
        from app.radio_sync import sync_and_offload_channels

        channel_result = MagicMock()
        channel_result.type = EventType.CHANNEL_INFO
        channel_result.payload = {
            "channel_name": "Public",
            "channel_secret": bytes.fromhex("8B3387E9C5CDEA6AC9E5EDBAA115CD72"),
        }

        other_result = MagicMock()
        other_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(side_effect=[channel_result] + [other_result] * 39)

        clear_result = MagicMock()
        clear_result.type = EventType.OK
        mock_mc.commands.set_channel = AsyncMock(return_value=clear_result)

        await sync_and_offload_channels(mock_mc)

        channel = await ChannelRepository.get_by_key("8B3387E9C5CDEA6AC9E5EDBAA115CD72")
        assert channel is not None
        assert channel.is_hashtag is False

    @pytest.mark.asyncio
    async def test_clears_channel_with_empty_name_and_zero_key(self, test_db):
        """Cleared channels are set with empty name and 16 zero bytes."""
        from app.radio_sync import sync_and_offload_channels

        channel_result = MagicMock()
        channel_result.type = EventType.CHANNEL_INFO
        channel_result.payload = {
            "channel_name": "#test",
            "channel_secret": bytes.fromhex("AABBCCDD" * 4),
        }

        other_result = MagicMock()
        other_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(side_effect=[channel_result] + [other_result] * 39)

        clear_result = MagicMock()
        clear_result.type = EventType.OK
        mock_mc.commands.set_channel = AsyncMock(return_value=clear_result)

        await sync_and_offload_channels(mock_mc)

        mock_mc.commands.set_channel.assert_called_once_with(
            channel_idx=0,
            channel_name="",
            channel_secret=bytes(16),
        )

    @pytest.mark.asyncio
    async def test_handles_clear_failure_gracefully(self, test_db):
        """Failed set_channel logs warning but continues processing."""
        from app.radio_sync import sync_and_offload_channels

        channel_results = []
        for i in range(2):
            r = MagicMock()
            r.type = EventType.CHANNEL_INFO
            r.payload = {
                "channel_name": f"#ch{i}",
                "channel_secret": bytes([i + 1] * 16),
            }
            channel_results.append(r)

        other_result = MagicMock()
        other_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(side_effect=channel_results + [other_result] * 38)

        fail_result = MagicMock()
        fail_result.type = EventType.ERROR
        fail_result.payload = {"error": "busy"}

        ok_result = MagicMock()
        ok_result.type = EventType.OK

        mock_mc.commands.set_channel = AsyncMock(side_effect=[fail_result, ok_result])

        result = await sync_and_offload_channels(mock_mc)

        assert result["synced"] == 2
        assert result["cleared"] == 1

    @pytest.mark.asyncio
    async def test_iterates_all_40_channel_slots(self):
        """All firmware-reported channel slots are checked."""
        from app.radio_sync import sync_and_offload_channels

        empty_result = MagicMock()
        empty_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(return_value=empty_result)
        radio_manager.max_channels = 8

        result = await sync_and_offload_channels(mock_mc)

        assert mock_mc.commands.get_channel.call_count == 8
        assert result["synced"] == 0
        assert result["cleared"] == 0


class TestEnsureDefaultChannels:
    """Test ensure_default_channels: create/fix the Public channel."""

    PUBLIC_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"

    @pytest.mark.asyncio
    async def test_creates_public_channel_when_missing(self, test_db):
        """Public channel is created when it does not exist."""
        from app.radio_sync import ensure_default_channels

        await ensure_default_channels()

        channel = await ChannelRepository.get_by_key(self.PUBLIC_KEY)
        assert channel is not None
        assert channel.name == "Public"
        assert channel.is_hashtag is False
        assert channel.on_radio is False

    @pytest.mark.asyncio
    async def test_fixes_public_channel_with_wrong_name(self, test_db):
        """Public channel name is corrected when it exists with wrong name."""
        from app.radio_sync import ensure_default_channels

        # Pre-insert with wrong name
        await ChannelRepository.upsert(
            key=self.PUBLIC_KEY,
            name="public",  # Wrong case
            is_hashtag=False,
            on_radio=True,
        )

        await ensure_default_channels()

        channel = await ChannelRepository.get_by_key(self.PUBLIC_KEY)
        assert channel.name == "Public"
        assert channel.on_radio is True  # Preserves existing on_radio state

    @pytest.mark.asyncio
    async def test_no_op_when_public_channel_exists_correctly(self, test_db):
        """No upsert when Public channel already exists with correct name."""
        from app.radio_sync import ensure_default_channels

        await ChannelRepository.upsert(
            key=self.PUBLIC_KEY,
            name="Public",
            is_hashtag=False,
            on_radio=False,
        )

        await ensure_default_channels()

        # Still exists and unchanged
        channel = await ChannelRepository.get_by_key(self.PUBLIC_KEY)
        assert channel.name == "Public"

    @pytest.mark.asyncio
    async def test_preserves_on_radio_state_when_fixing_name(self, test_db):
        """existing.on_radio is passed through when fixing the channel name."""
        from app.radio_sync import ensure_default_channels

        await ChannelRepository.upsert(
            key=self.PUBLIC_KEY,
            name="Pub",
            is_hashtag=False,
            on_radio=True,
        )

        await ensure_default_channels()

        channel = await ChannelRepository.get_by_key(self.PUBLIC_KEY)
        assert channel.on_radio is True


# ---------------------------------------------------------------------------
# Background loop race-condition regression tests
#
# Each loop uses radio_operation(blocking=False) which can raise
# RadioDisconnectedError (disconnect between pre-check and lock) or
# RadioOperationBusyError (lock already held).  These tests verify the
# loops handle both gracefully and that the lock-scoped mc is what gets
# forwarded to helper functions (the R1 fix).
# ---------------------------------------------------------------------------


def _make_connected_manager() -> tuple[RadioManager, MagicMock]:
    """Create a RadioManager with a mock MeshCore that reports is_connected=True."""
    rm = RadioManager()
    mock_mc = MagicMock(name="lock_scoped_mc")
    mock_mc.is_connected = True
    mock_mc.stop_auto_message_fetching = AsyncMock()
    mock_mc.start_auto_message_fetching = AsyncMock()
    rm._meshcore = mock_mc
    return rm, mock_mc


def _disconnect_on_acquire(rm: RadioManager):
    """Monkey-patch rm so _meshcore is set to None right after the lock is acquired.

    This simulates the exact race: is_connected pre-check passes, but by the
    time radio_operation() checks _meshcore post-lock, a reconnect has set it
    to None → RadioDisconnectedError.
    """
    original = rm._acquire_operation_lock

    async def _acquire_then_disconnect(name, *, blocking):
        await original(name, blocking=blocking)
        rm._meshcore = None

    rm._acquire_operation_lock = _acquire_then_disconnect


async def _pre_hold_lock(rm: RadioManager) -> asyncio.Lock:
    """Pre-acquire the operation lock so non-blocking callers get RadioOperationBusyError."""
    if rm._operation_lock is None:
        rm._operation_lock = asyncio.Lock()
    await rm._operation_lock.acquire()
    return rm._operation_lock


def _sleep_controller(*, cancel_after: int = 2):
    """Return a (mock_sleep, calls) pair.

    mock_sleep returns normally for the first *cancel_after - 1* calls, then
    raises ``CancelledError`` to cleanly stop the infinite loop.
    """
    calls: list[float] = []

    async def _sleep(duration):
        calls.append(duration)
        if len(calls) >= cancel_after:
            raise asyncio.CancelledError()

    return _sleep, calls


class TestMessagePollLoopRaces:
    """Regression tests for disconnect/reconnect race paths in _message_poll_loop."""

    @pytest.mark.asyncio
    async def test_uses_hourly_audit_interval_when_fallback_disabled(self):
        rm, _mc = _make_connected_manager()
        mock_sleep, sleep_calls = _sleep_controller(cancel_after=1)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("app.radio_sync.settings.enable_message_poll_fallback", False),
            patch("asyncio.sleep", side_effect=mock_sleep),
        ):
            await _message_poll_loop()

        assert sleep_calls == [3600]

    @pytest.mark.asyncio
    async def test_uses_fast_poll_interval_when_fallback_enabled(self):
        rm, _mc = _make_connected_manager()
        mock_sleep, sleep_calls = _sleep_controller(cancel_after=1)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("app.radio_sync.settings.enable_message_poll_fallback", True),
            patch("asyncio.sleep", side_effect=mock_sleep),
        ):
            await _message_poll_loop()

        assert sleep_calls == [10]

    @pytest.mark.asyncio
    async def test_disconnect_race_between_precheck_and_lock(self):
        """RadioDisconnectedError between is_connected and radio_operation()
        is caught by the outer except — loop survives and continues."""
        rm, _mc = _make_connected_manager()
        _disconnect_on_acquire(rm)
        mock_sleep, sleep_calls = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.cleanup_expired_acks"),
            patch("app.radio_sync.poll_for_messages", new_callable=AsyncMock) as mock_poll,
        ):
            await _message_poll_loop()

        mock_poll.assert_not_called()
        # Loop ran two iterations: first handled the error, second was cancelled
        assert len(sleep_calls) == 2

    @pytest.mark.asyncio
    async def test_busy_lock_skips_iteration(self):
        """RadioOperationBusyError is caught and poll_for_messages is not called."""
        rm, _mc = _make_connected_manager()
        lock = await _pre_hold_lock(rm)
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        try:
            with (
                patch("app.radio_sync.radio_manager", rm),
                patch("asyncio.sleep", side_effect=mock_sleep),
                patch("app.radio_sync.cleanup_expired_acks"),
                patch("app.radio_sync.poll_for_messages", new_callable=AsyncMock) as mock_poll,
            ):
                await _message_poll_loop()
        finally:
            lock.release()

        mock_poll.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_lock_scoped_mc_not_stale_global(self):
        """The mc yielded by radio_operation() is forwarded to
        poll_for_messages — not a stale radio_manager.meshcore read."""
        rm, mock_mc = _make_connected_manager()
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.cleanup_expired_acks"),
            patch("app.radio_sync.poll_for_messages", new_callable=AsyncMock) as mock_poll,
        ):
            await _message_poll_loop()

        mock_poll.assert_called_once_with(mock_mc)

    @pytest.mark.asyncio
    async def test_hourly_audit_crows_loudly_when_it_finds_hidden_messages(self):
        rm, mock_mc = _make_connected_manager()
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("app.radio_sync.settings.enable_message_poll_fallback", False),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.poll_for_messages", new_callable=AsyncMock, return_value=2),
            patch("app.radio_sync.logger") as mock_logger,
            patch("app.radio_sync.broadcast_error") as mock_broadcast_error,
        ):
            await _message_poll_loop()

        mock_logger.error.assert_called_once()
        mock_broadcast_error.assert_called_once_with(
            "A periodic poll task has discovered radio inconsistencies.",
            "Please check the logs for recommendations (search "
            "'MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK').",
        )

    @pytest.mark.asyncio
    async def test_fast_poll_logs_missed_messages_without_error_toast(self):
        rm, mock_mc = _make_connected_manager()
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("app.radio_sync.settings.enable_message_poll_fallback", True),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.poll_for_messages", new_callable=AsyncMock, return_value=2),
            patch("app.radio_sync.logger") as mock_logger,
            patch("app.radio_sync.broadcast_error") as mock_broadcast_error,
        ):
            await _message_poll_loop()

        mock_logger.warning.assert_called_once()
        mock_broadcast_error.assert_not_called()


class TestPeriodicAdvertLoopRaces:
    """Regression tests for disconnect/reconnect race paths in _periodic_advert_loop."""

    @pytest.mark.asyncio
    async def test_disconnect_race_between_precheck_and_lock(self):
        """RadioDisconnectedError between is_connected and radio_operation()
        is caught by the outer except — loop survives and continues."""
        rm, _mc = _make_connected_manager()
        _disconnect_on_acquire(rm)
        # Advert loop: sleep first, then work. Sleep 1 (loop top) passes,
        # work hits RadioDisconnectedError, next iteration sleep 2 cancels
        # cleanly via except CancelledError without an extra backoff sleep.
        mock_sleep, sleep_calls = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock) as mock_advert,
        ):
            await _periodic_advert_loop()

        mock_advert.assert_not_called()
        assert len(sleep_calls) == 2

    @pytest.mark.asyncio
    async def test_busy_lock_skips_iteration(self):
        """RadioOperationBusyError is caught and send_advertisement is not called."""
        rm, _mc = _make_connected_manager()
        lock = await _pre_hold_lock(rm)
        # Sleep 1 (loop top) passes, work hits busy error, sleep 2 cancels.
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        try:
            with (
                patch("app.radio_sync.radio_manager", rm),
                patch("asyncio.sleep", side_effect=mock_sleep),
                patch("app.radio_sync.send_advertisement", new_callable=AsyncMock) as mock_advert,
            ):
                await _periodic_advert_loop()
        finally:
            lock.release()

        mock_advert.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_lock_scoped_mc_not_stale_global(self):
        """The mc yielded by radio_operation() is forwarded to
        send_advertisement — not a stale radio_manager.meshcore read."""
        rm, mock_mc = _make_connected_manager()
        # Sleep 1 (loop top) passes through, work runs, sleep 2 cancels.
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock) as mock_advert,
        ):
            await _periodic_advert_loop()

        mock_advert.assert_called_once_with(mock_mc)


class TestPeriodicSyncLoopRaces:
    """Regression tests for disconnect/reconnect race paths in _periodic_sync_loop."""

    @pytest.mark.asyncio
    async def test_should_run_full_periodic_sync_at_trigger_threshold(self, test_db):
        """Occupancy at 95% of configured capacity triggers a full offload/reload."""
        from app.radio_sync import should_run_full_periodic_sync

        await AppSettingsRepository.update(max_radio_contacts=100)

        mock_mc = MagicMock()
        mock_result = MagicMock()
        mock_result.type = EventType.NEW_CONTACT
        mock_result.payload = {f"{i:064x}": {"adv_name": f"Node{i}"} for i in range(95)}
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_result)

        assert await should_run_full_periodic_sync(mock_mc) is True

    @pytest.mark.asyncio
    async def test_should_skip_full_periodic_sync_below_trigger_threshold(self, test_db):
        """Occupancy below 95% of configured capacity does not trigger offload/reload."""
        from app.radio_sync import should_run_full_periodic_sync

        await AppSettingsRepository.update(max_radio_contacts=100)

        mock_mc = MagicMock()
        mock_result = MagicMock()
        mock_result.type = EventType.NEW_CONTACT
        mock_result.payload = {f"{i:064x}": {"adv_name": f"Node{i}"} for i in range(94)}
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_result)

        assert await should_run_full_periodic_sync(mock_mc) is False

    @pytest.mark.asyncio
    async def test_disconnect_race_between_precheck_and_lock(self):
        """RadioDisconnectedError between is_connected and radio_operation()
        is caught by the outer except — loop survives and continues."""
        rm, _mc = _make_connected_manager()
        _disconnect_on_acquire(rm)
        mock_sleep, sleep_calls = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.cleanup_expired_acks") as mock_cleanup,
            patch(
                "app.radio_sync.should_run_full_periodic_sync", new_callable=AsyncMock
            ) as mock_check,
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock) as mock_sync,
            patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock) as mock_time,
        ):
            await _periodic_sync_loop()

        mock_cleanup.assert_called_once()
        mock_check.assert_not_called()
        mock_sync.assert_not_called()
        mock_time.assert_not_called()
        assert len(sleep_calls) == 2

    @pytest.mark.asyncio
    async def test_busy_lock_skips_iteration(self):
        """RadioOperationBusyError is caught and sync functions are not called."""
        rm, _mc = _make_connected_manager()
        lock = await _pre_hold_lock(rm)
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        try:
            with (
                patch("app.radio_sync.radio_manager", rm),
                patch("asyncio.sleep", side_effect=mock_sleep),
                patch("app.radio_sync.cleanup_expired_acks") as mock_cleanup,
                patch(
                    "app.radio_sync.should_run_full_periodic_sync", new_callable=AsyncMock
                ) as mock_check,
                patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock) as mock_sync,
                patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock) as mock_time,
            ):
                await _periodic_sync_loop()
        finally:
            lock.release()

        mock_cleanup.assert_called_once()
        mock_check.assert_not_called()
        mock_sync.assert_not_called()
        mock_time.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_lock_scoped_mc_not_stale_global(self):
        """The mc yielded by radio_operation() is forwarded to
        sync_and_offload_all and sync_radio_time — not a stale
        radio_manager.meshcore read."""
        rm, mock_mc = _make_connected_manager()
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.cleanup_expired_acks") as mock_cleanup,
            patch(
                "app.radio_sync.should_run_full_periodic_sync",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock) as mock_sync,
            patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock) as mock_time,
        ):
            await _periodic_sync_loop()

        mock_cleanup.assert_called_once()
        mock_sync.assert_called_once_with(mock_mc)
        mock_time.assert_called_once_with(mock_mc)

    @pytest.mark.asyncio
    async def test_skips_full_sync_below_threshold_but_still_syncs_time(self):
        """Periodic maintenance still does time sync when occupancy is below the trigger."""
        rm, mock_mc = _make_connected_manager()
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.cleanup_expired_acks") as mock_cleanup,
            patch(
                "app.radio_sync.should_run_full_periodic_sync",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock) as mock_sync,
            patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock) as mock_time,
        ):
            await _periodic_sync_loop()

        mock_cleanup.assert_called_once()
        mock_sync.assert_not_called()
        mock_time.assert_called_once_with(mock_mc)
