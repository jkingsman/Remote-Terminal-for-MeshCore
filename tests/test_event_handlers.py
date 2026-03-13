"""Tests for event handler logic.

These tests verify the ACK tracking mechanism for direct message
delivery confirmation, contact message handling, and event registration.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.event_handlers import (
    _active_subscriptions,
    _pending_acks,
    cleanup_expired_acks,
    register_event_handlers,
    track_pending_ack,
)
from app.repository import (
    ContactRepository,
    MessageRepository,
)


@pytest.fixture(autouse=True)
def clear_test_state():
    """Clear pending ACKs and subscriptions before each test."""
    _pending_acks.clear()
    _active_subscriptions.clear()
    yield
    _pending_acks.clear()
    _active_subscriptions.clear()


class TestAckTracking:
    """Test ACK tracking for direct messages."""

    def test_track_pending_ack_stores_correctly(self):
        """Pending ACKs are stored with message ID and timeout."""
        track_pending_ack("abc123", message_id=42, timeout_ms=5000)

        assert "abc123" in _pending_acks
        msg_id, created_at, timeout = _pending_acks["abc123"]
        assert msg_id == 42
        assert timeout == 5000
        assert created_at <= time.time()

    def test_multiple_acks_tracked_independently(self):
        """Multiple pending ACKs can be tracked simultaneously."""
        track_pending_ack("ack1", message_id=1, timeout_ms=1000)
        track_pending_ack("ack2", message_id=2, timeout_ms=2000)
        track_pending_ack("ack3", message_id=3, timeout_ms=3000)

        assert len(_pending_acks) == 3
        assert _pending_acks["ack1"][0] == 1
        assert _pending_acks["ack2"][0] == 2
        assert _pending_acks["ack3"][0] == 3

    def test_cleanup_removes_expired_acks(self):
        """Expired ACKs are removed during cleanup."""
        # Add an ACK that's "expired" (created in the past with short timeout)
        _pending_acks["expired"] = (1, time.time() - 100, 1000)  # Created 100s ago, 1s timeout
        _pending_acks["valid"] = (2, time.time(), 60000)  # Created now, 60s timeout

        cleanup_expired_acks()

        assert "expired" not in _pending_acks
        assert "valid" in _pending_acks

    def test_cleanup_uses_2x_timeout_buffer(self):
        """Cleanup uses 2x timeout as buffer before expiring."""
        # ACK created 5 seconds ago with 10 second timeout
        # 2x buffer = 20 seconds, so should NOT be expired yet
        _pending_acks["recent"] = (1, time.time() - 5, 10000)

        cleanup_expired_acks()

        assert "recent" in _pending_acks

    def test_cleanup_handles_many_expired_acks_without_growth(self):
        """Many tracked ACKs that all expire should all be cleaned up,
        preventing unbounded memory growth when no ACKs ever arrive."""
        now = time.time()
        for i in range(100):
            _pending_acks[f"ack_{i}"] = (i, now - 300, 5000)  # All expired (300s ago, 5s timeout)

        assert len(_pending_acks) == 100

        cleanup_expired_acks()

        assert len(_pending_acks) == 0

    def test_cleanup_preserves_valid_acks_among_expired(self):
        """Cleanup removes only expired ACKs, preserving valid ones."""
        now = time.time()
        # 50 expired
        for i in range(50):
            _pending_acks[f"expired_{i}"] = (i, now - 300, 5000)
        # 50 valid
        for i in range(50):
            _pending_acks[f"valid_{i}"] = (100 + i, now, 60000)

        assert len(_pending_acks) == 100

        cleanup_expired_acks()

        assert len(_pending_acks) == 50
        assert all(k.startswith("valid_") for k in _pending_acks)


class TestAckEventHandler:
    """Test the on_ack event handler."""

    @pytest.mark.asyncio
    async def test_ack_matches_pending_message(self, test_db):
        """Matching ACK code updates message and broadcasts."""
        from app.event_handlers import on_ack

        # Insert a real message to get a valid ID
        msg_id = await MessageRepository.create(
            msg_type="PRIV",
            text="Hello",
            received_at=1700000000,
            conversation_key="aa" * 32,
            sender_timestamp=1700000000,
        )

        # Setup pending ACK with the real message ID
        track_pending_ack("deadbeef", message_id=msg_id, timeout_ms=10000)

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {"code": "deadbeef"}

            await on_ack(MockEvent())

            # Verify ack count incremented (real DB)
            ack_count, _ = await MessageRepository.get_ack_and_paths(msg_id)
            assert ack_count == 1

            # Verify broadcast sent with ack_count
            mock_broadcast.assert_called_once_with(
                "message_acked", {"message_id": msg_id, "ack_count": 1}
            )

            # Verify pending ACK removed
            assert "deadbeef" not in _pending_acks

    @pytest.mark.asyncio
    async def test_ack_no_match_does_nothing(self, test_db):
        """Non-matching ACK code is ignored."""
        from app.event_handlers import on_ack

        msg_id = await MessageRepository.create(
            msg_type="PRIV",
            text="Hello",
            received_at=1700000000,
            conversation_key="aa" * 32,
            sender_timestamp=1700000000,
        )
        track_pending_ack("expected", message_id=msg_id, timeout_ms=10000)

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {"code": "different"}

            await on_ack(MockEvent())

            # Ack count should remain 0
            ack_count, _ = await MessageRepository.get_ack_and_paths(msg_id)
            assert ack_count == 0

            mock_broadcast.assert_not_called()
            assert "expected" in _pending_acks

    @pytest.mark.asyncio
    async def test_ack_empty_code_ignored(self, test_db):
        """ACK with empty code is ignored."""
        from app.event_handlers import on_ack

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {"code": ""}

            await on_ack(MockEvent())

            mock_broadcast.assert_not_called()


class TestContactMessageCLIFiltering:
    """Test that CLI responses (txt_type=1) are filtered out."""

    @pytest.mark.asyncio
    async def test_cli_response_skipped_not_stored(self, test_db):
        """CLI responses (txt_type=1) are not stored in database."""
        from app.event_handlers import on_contact_message

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {
                    "pubkey_prefix": "abc123def456",
                    "text": "clock: 2024-01-01 12:00:00",
                    "txt_type": 1,  # CLI response
                    "sender_timestamp": 1700000000,
                }

            await on_contact_message(MockEvent())

            # Should NOT broadcast via WebSocket
            mock_broadcast.assert_not_called()

            # Should NOT have stored anything in DB
            messages = await MessageRepository.get_all()
            assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_normal_message_still_processed(self, test_db):
        """Normal messages (txt_type=0) are still processed normally."""
        from app.event_handlers import on_contact_message

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {
                    "pubkey_prefix": "abc123def456",
                    "text": "Hello, this is a normal message",
                    "txt_type": 0,  # Normal message (default)
                    "sender_timestamp": 1700000000,
                }

            await on_contact_message(MockEvent())

            # SHOULD be stored in database
            messages = await MessageRepository.get_all()
            assert len(messages) == 1
            assert messages[0].text == "Hello, this is a normal message"

            # Placeholder contact is broadcast first, then the message
            assert mock_broadcast.call_count == 2
            assert mock_broadcast.call_args_list[-1].args[0] == "message"

    @pytest.mark.asyncio
    async def test_broadcast_payload_has_correct_acked_type(self, test_db):
        """Broadcast payload should have acked as integer 0, not boolean False."""
        from app.event_handlers import on_contact_message

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {
                    "pubkey_prefix": "abc123def456",
                    "text": "Test message",
                    "txt_type": 0,
                    "sender_timestamp": 1700000000,
                }

            await on_contact_message(MockEvent())

            # Last broadcast is the message; first may be placeholder contact
            call_args = mock_broadcast.call_args_list[-1]

            # First arg is event type, second is payload dict
            event_type, payload = call_args[0]
            assert event_type == "message"
            assert payload["acked"] == 0
            assert payload["acked"] is not False  # Ensure it's int, not bool
            assert isinstance(payload["acked"], int)

    @pytest.mark.asyncio
    async def test_broadcast_message_payload_matches_frontend_type(self, test_db):
        """Broadcast payload keys must match the frontend Message interface in types.ts."""
        from app.event_handlers import on_contact_message

        # Keys the frontend Message interface expects (see frontend/src/types.ts)
        EXPECTED_MESSAGE_KEYS = {
            "id",
            "type",
            "conversation_key",
            "text",
            "sender_timestamp",
            "received_at",
            "paths",
            "txt_type",
            "signature",
            "sender_key",
            "outgoing",
            "acked",
            "sender_name",
            "channel_name",
        }

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {
                    "pubkey_prefix": "abc123def456",
                    "text": "shape test",
                    "txt_type": 0,
                    "sender_timestamp": 1700000000,
                }

            await on_contact_message(MockEvent())

            event_type, payload = mock_broadcast.call_args_list[-1][0]
            assert event_type == "message"
            assert set(payload.keys()) == EXPECTED_MESSAGE_KEYS

    @pytest.mark.asyncio
    async def test_broadcast_ack_payload_matches_frontend_type(self, test_db):
        """Broadcast payload keys for message_acked must match frontend expectations."""
        from app.event_handlers import on_ack

        msg_id = await MessageRepository.create(
            msg_type="PRIV",
            text="ack shape test",
            received_at=1700000000,
            conversation_key="bb" * 32,
            sender_timestamp=1700000000,
        )
        track_pending_ack("ackshape", message_id=msg_id, timeout_ms=10000)

        # Keys the frontend expects (see frontend/src/useWebSocket.ts:111-114)
        EXPECTED_ACK_KEYS = {"message_id", "ack_count"}

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {"code": "ackshape"}

            await on_ack(MockEvent())

            mock_broadcast.assert_called_once()
            event_type, payload = mock_broadcast.call_args[0]
            assert event_type == "message_acked"
            assert set(payload.keys()) == EXPECTED_ACK_KEYS

    @pytest.mark.asyncio
    async def test_missing_txt_type_defaults_to_normal(self, test_db):
        """Messages without txt_type field are treated as normal (not filtered)."""
        from app.event_handlers import on_contact_message

        with patch("app.event_handlers.broadcast_event"):

            class MockEvent:
                payload = {
                    "pubkey_prefix": "abc123def456",
                    "text": "Message without txt_type field",
                    "sender_timestamp": 1700000000,
                    # No txt_type field
                }

            await on_contact_message(MockEvent())

            # SHOULD still be processed (defaults to txt_type=0)
            messages = await MessageRepository.get_all()
            assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_unknown_full_key_dm_creates_placeholder_contact(self, test_db):
        """Fallback DMs with a full unknown key create a durable placeholder contact."""
        from app.event_handlers import on_contact_message

        full_key = "ab" * 32
        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {
                    "public_key": full_key,
                    "text": "hello from unknown full key",
                    "txt_type": 0,
                    "sender_timestamp": 1700000000,
                }

            await on_contact_message(MockEvent())

            contact = await ContactRepository.get_by_key(full_key)
            assert contact is not None
            assert contact.public_key == full_key
            assert contact.name is None

            messages = await MessageRepository.get_all(conversation_key=full_key)
            assert len(messages) == 1

            assert mock_broadcast.call_args_list[0].args[0] == "contact"
            assert mock_broadcast.call_args_list[-1].args[0] == "message"

    @pytest.mark.asyncio
    async def test_new_contact_promotes_prefix_placeholder_and_broadcasts_resolution(self, test_db):
        """NEW_CONTACT promotes prefix placeholders to the full key and emits contact_resolved."""
        from app.event_handlers import on_new_contact

        prefix = "abc123def456"
        full_key = prefix + ("00" * 26)
        await ContactRepository.upsert(
            {
                "public_key": prefix,
                "type": 0,
                "last_seen": 1700000000,
                "last_contacted": 1700000000,
                "first_seen": 1700000000,
                "out_path_hash_mode": -1,
            }
        )
        await MessageRepository.create(
            msg_type="PRIV",
            text="hello",
            conversation_key=prefix,
            sender_timestamp=1700000000,
            received_at=1700000000,
        )

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {
                    "public_key": full_key,
                    "adv_name": "Resolved Sender",
                    "type": 1,
                    "flags": 0,
                    "adv_lat": 0.0,
                    "adv_lon": 0.0,
                    "last_advert": 1700000010,
                    "out_path": "",
                    "out_path_len": -1,
                    "out_path_hash_mode": -1,
                }

            await on_new_contact(MockEvent())

            assert await ContactRepository.get_by_key(prefix) is None
            resolved = await ContactRepository.get_by_key(full_key)
            assert resolved is not None
            assert resolved.name == "Resolved Sender"

            messages = await MessageRepository.get_all(conversation_key=full_key)
            assert len(messages) == 1
            assert messages[0].conversation_key == full_key

            event_types = [call.args[0] for call in mock_broadcast.call_args_list]
            assert "contact" in event_types
            assert "contact_resolved" in event_types

    @pytest.mark.asyncio
    async def test_new_contact_keeps_ambiguous_prefix_placeholder_unresolved(self, test_db):
        """Ambiguous prefix placeholders should not resolve until a unique full-key match exists."""
        from app.event_handlers import on_new_contact

        prefix = "abc123"
        full_key = prefix + ("00" * 29)
        conflicting_full_key = prefix + ("ff" * 29)
        await ContactRepository.upsert(
            {
                "public_key": prefix,
                "type": 0,
                "last_seen": 1700000000,
                "out_path_hash_mode": -1,
            }
        )
        await ContactRepository.upsert(
            {
                "public_key": conflicting_full_key,
                "name": "Conflicting Sender",
                "type": 1,
                "flags": 0,
            }
        )
        await MessageRepository.create(
            msg_type="PRIV",
            text="hello from ambiguous prefix",
            conversation_key=prefix,
            sender_timestamp=1700000000,
            received_at=1700000000,
        )

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {
                    "public_key": full_key,
                    "adv_name": "Resolved Sender",
                    "type": 1,
                    "flags": 0,
                    "adv_lat": 0.0,
                    "adv_lon": 0.0,
                    "last_advert": 1700000010,
                    "out_path": "",
                    "out_path_len": -1,
                    "out_path_hash_mode": -1,
                }

            await on_new_contact(MockEvent())

            placeholder = await ContactRepository.get_by_key(prefix)
            assert placeholder is not None

            resolved = await ContactRepository.get_by_key(full_key)
            assert resolved is not None
            assert resolved.name == "Resolved Sender"

            prefix_messages = await MessageRepository.get_all(conversation_key=prefix)
            assert len(prefix_messages) == 1

            resolved_messages = await MessageRepository.get_all(conversation_key=full_key)
            assert resolved_messages == []

            event_types = [call.args[0] for call in mock_broadcast.call_args_list]
            assert "contact" in event_types
            assert "contact_resolved" not in event_types

    @pytest.mark.asyncio
    async def test_ambiguous_prefix_stores_dm_under_prefix(self, test_db):
        """Ambiguous sender prefixes should still be stored under the prefix key."""
        from app.event_handlers import on_contact_message

        # Insert two contacts that share the same prefix to trigger ambiguity
        await ContactRepository.upsert(
            {
                "public_key": "abc123" + "00" * 29,
                "name": "ContactA",
                "type": 1,
                "flags": 0,
            }
        )
        await ContactRepository.upsert(
            {
                "public_key": "abc123" + "ff" * 29,
                "name": "ContactB",
                "type": 1,
                "flags": 0,
            }
        )

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {
                    "pubkey_prefix": "abc123",
                    "text": "hello from ambiguous prefix",
                    "txt_type": 0,
                    "sender_timestamp": 1700000000,
                }

            await on_contact_message(MockEvent())

            # Should store in DB under the prefix key
            messages = await MessageRepository.get_all()
            assert len(messages) == 1
            assert messages[0].conversation_key == "abc123"

            assert mock_broadcast.call_count == 2
            assert mock_broadcast.call_args_list[0].args[0] == "contact"
            assert mock_broadcast.call_args_list[-1].args[0] == "message"
            _, payload = mock_broadcast.call_args.args
            assert payload["conversation_key"] == "abc123"

    @pytest.mark.asyncio
    async def test_repeater_message_skipped_not_stored(self, test_db):
        """Messages from repeater contacts (type=2) are dropped, not stored."""
        from app.event_handlers import on_contact_message

        repeater_key = "dd" * 32
        await ContactRepository.upsert(
            {
                "public_key": repeater_key,
                "name": "MyRepeater",
                "type": 2,  # CONTACT_TYPE_REPEATER
                "flags": 0,
            }
        )

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {
                    "public_key": repeater_key,
                    "text": "Some repeater noise",
                    "txt_type": 0,
                    "sender_timestamp": 1700000000,
                }

            await on_contact_message(MockEvent())

            mock_broadcast.assert_not_called()

            messages = await MessageRepository.get_all()
            assert len(messages) == 0


class TestContactMessageDBErrorResilience:
    """Test that DB errors in on_contact_message propagate without crashing silently."""

    @pytest.mark.asyncio
    async def test_db_error_in_create_propagates(self, test_db):
        """When MessageRepository.create raises, the exception propagates.

        If this handler silently swallowed DB errors, messages would be lost
        without any indication. The exception should propagate so the caller
        (MeshCore event dispatcher) can handle it.
        """
        from app.event_handlers import on_contact_message

        class MockEvent:
            payload = {
                "pubkey_prefix": "abc123def456",
                "text": "DB will fail",
                "txt_type": 0,
                "sender_timestamp": 1700000000,
            }

        with (
            patch("app.event_handlers.broadcast_event"),
            patch.object(
                MessageRepository,
                "create",
                side_effect=Exception("database is locked"),
            ),
        ):
            with pytest.raises(Exception, match="database is locked"):
                await on_contact_message(MockEvent())

    @pytest.mark.asyncio
    async def test_db_error_in_contact_lookup_propagates(self, test_db):
        """When ContactRepository.get_by_key_or_prefix raises an unexpected error,
        it propagates rather than being silently swallowed."""
        from app.event_handlers import on_contact_message

        class MockEvent:
            payload = {
                "public_key": "ab" * 32,
                "text": "Lookup will fail",
                "txt_type": 0,
                "sender_timestamp": 1700000000,
            }

        with patch.object(
            ContactRepository,
            "get_by_key_or_prefix",
            side_effect=RuntimeError("connection pool exhausted"),
        ):
            with pytest.raises(RuntimeError, match="connection pool exhausted"):
                await on_contact_message(MockEvent())


class TestEventHandlerRegistration:
    """Test event handler registration and cleanup."""

    def test_register_handlers_tracks_subscriptions(self):
        """Registering handlers populates _active_subscriptions."""
        mock_meshcore = MagicMock()
        mock_subscription = MagicMock()
        mock_meshcore.subscribe.return_value = mock_subscription

        register_event_handlers(mock_meshcore)

        # Should have 5 subscriptions (one per event type)
        assert len(_active_subscriptions) == 5
        assert mock_meshcore.subscribe.call_count == 5

    def test_register_handlers_twice_does_not_duplicate(self):
        """Calling register_event_handlers twice unsubscribes old handlers first."""
        mock_meshcore = MagicMock()

        # First call: create mock subscriptions
        first_subs = [MagicMock() for _ in range(5)]
        mock_meshcore.subscribe.side_effect = first_subs
        register_event_handlers(mock_meshcore)

        assert len(_active_subscriptions) == 5
        first_sub_objects = list(_active_subscriptions)

        # Second call: create new mock subscriptions
        second_subs = [MagicMock() for _ in range(5)]
        mock_meshcore.subscribe.side_effect = second_subs
        register_event_handlers(mock_meshcore)

        # Old subscriptions should have been unsubscribed
        for sub in first_sub_objects:
            sub.unsubscribe.assert_called_once()

        # Should still have exactly 5 subscriptions (not 10)
        assert len(_active_subscriptions) == 5

        # New subscriptions should be the second batch
        for sub in second_subs:
            assert sub in _active_subscriptions

    def test_register_handlers_clears_before_adding(self):
        """The subscription list is cleared before adding new subscriptions."""
        mock_meshcore = MagicMock()
        mock_meshcore.subscribe.return_value = MagicMock()

        # Pre-populate with stale subscriptions (simulating a bug scenario)
        stale_sub = MagicMock()
        _active_subscriptions.append(stale_sub)
        _active_subscriptions.append(stale_sub)

        register_event_handlers(mock_meshcore)

        # Stale subscriptions should have been unsubscribed
        assert stale_sub.unsubscribe.call_count == 2

        # Should have exactly 5 fresh subscriptions
        assert len(_active_subscriptions) == 5

    def test_register_handlers_survives_unsubscribe_exception(self):
        """If unsubscribe() throws, registration still completes successfully."""
        mock_meshcore = MagicMock()
        mock_meshcore.subscribe.return_value = MagicMock()

        # Create subscriptions where unsubscribe raises an exception
        bad_sub = MagicMock()
        bad_sub.unsubscribe.side_effect = RuntimeError("Dispatcher is dead")
        _active_subscriptions.append(bad_sub)

        good_sub = MagicMock()
        _active_subscriptions.append(good_sub)

        # Should not raise despite the exception
        register_event_handlers(mock_meshcore)

        # Both unsubscribe methods should have been called
        bad_sub.unsubscribe.assert_called_once()
        good_sub.unsubscribe.assert_called_once()

        # Should have exactly 5 fresh subscriptions
        assert len(_active_subscriptions) == 5


class TestOnPathUpdate:
    """Test the on_path_update event handler."""

    @pytest.mark.asyncio
    async def test_updates_path_for_existing_contact(self, test_db):
        """Path is updated when the contact exists and payload includes full key."""
        from app.event_handlers import on_path_update

        await ContactRepository.upsert(
            {
                "public_key": "aa" * 32,
                "name": "Alice",
                "type": 1,
                "flags": 0,
            }
        )

        class MockEvent:
            payload = {
                "public_key": "aa" * 32,
                "path": "0102",
                "path_len": 2,
            }

        await on_path_update(MockEvent())

        # Verify path was updated in DB
        contact = await ContactRepository.get_by_key("aa" * 32)
        assert contact is not None
        assert contact.last_path == "0102"
        assert contact.last_path_len == 2
        assert contact.out_path_hash_mode == 0

    @pytest.mark.asyncio
    async def test_updates_path_hash_mode_when_present(self, test_db):
        """PATH_UPDATE persists explicit multibyte path hash mode."""
        from app.event_handlers import on_path_update

        await ContactRepository.upsert(
            {
                "public_key": "ab" * 32,
                "name": "Alice",
                "type": 1,
                "flags": 0,
            }
        )

        class MockEvent:
            payload = {
                "public_key": "ab" * 32,
                "path": "aa00bb00",
                "path_len": 2,
                "path_hash_mode": 1,
            }

        await on_path_update(MockEvent())

        contact = await ContactRepository.get_by_key("ab" * 32)
        assert contact is not None
        assert contact.last_path == "aa00bb00"
        assert contact.last_path_len == 2
        assert contact.out_path_hash_mode == 1

    @pytest.mark.asyncio
    async def test_does_nothing_when_contact_not_found(self, test_db):
        """No update is attempted when the contact is not in the database."""
        from app.event_handlers import on_path_update

        class MockEvent:
            payload = {
                "public_key": "cc" * 32,
                "path": "0102",
                "path_len": 2,
            }

        # Should not raise
        await on_path_update(MockEvent())

    @pytest.mark.asyncio
    async def test_legacy_prefix_payload_still_supported(self, test_db):
        """Legacy prefix payloads still update path when uniquely resolvable."""
        from app.event_handlers import on_path_update

        await ContactRepository.upsert(
            {
                "public_key": "bb" * 32,
                "name": "Bob",
                "type": 1,
                "flags": 0,
            }
        )

        class MockEvent:
            payload = {
                "pubkey_prefix": "bbbbbb",
                "path": "0a0b",
                "path_len": 2,
            }

        await on_path_update(MockEvent())

        contact = await ContactRepository.get_by_key("bb" * 32)
        assert contact is not None
        assert contact.last_path == "0a0b"
        assert contact.last_path_len == 2

    @pytest.mark.asyncio
    async def test_missing_path_fields_does_not_modify_contact(self, test_db):
        """Current PATH_UPDATE payloads without path fields should not mutate DB path."""
        from app.event_handlers import on_path_update

        await ContactRepository.upsert(
            {
                "public_key": "dd" * 32,
                "name": "Dana",
                "type": 1,
                "flags": 0,
            }
        )
        await ContactRepository.update_path("dd" * 32, "beef", 2)

        class MockEvent:
            payload = {"public_key": "dd" * 32}

        await on_path_update(MockEvent())

        contact = await ContactRepository.get_by_key("dd" * 32)
        assert contact is not None
        assert contact.last_path == "beef"
        assert contact.last_path_len == 2

    @pytest.mark.asyncio
    async def test_missing_identity_fields_noop(self, test_db):
        """PATH_UPDATE with no key fields should be a no-op."""
        from app.event_handlers import on_path_update

        await ContactRepository.upsert(
            {
                "public_key": "ee" * 32,
                "name": "Eve",
                "type": 1,
                "flags": 0,
            }
        )
        await ContactRepository.update_path("ee" * 32, "abcd", 2)

        class MockEvent:
            payload = {}

        await on_path_update(MockEvent())

        contact = await ContactRepository.get_by_key("ee" * 32)
        assert contact is not None
        assert contact.last_path == "abcd"
        assert contact.last_path_len == 2


class TestOnNewContact:
    """Test the on_new_contact event handler."""

    @pytest.mark.asyncio
    async def test_creates_contact_and_broadcasts(self, test_db):
        """Valid new contact is upserted and broadcast via WebSocket."""
        from app.event_handlers import on_new_contact

        with (
            patch("app.event_handlers.broadcast_event") as mock_broadcast,
            patch("app.event_handlers.time") as mock_time,
        ):
            mock_time.time.return_value = 1700000000

            class MockEvent:
                payload = {
                    "public_key": "cc" * 32,
                    "adv_name": "Charlie",
                    "type": 1,
                    "flags": 0,
                }

            await on_new_contact(MockEvent())

            # Verify contact was created in real DB
            contact = await ContactRepository.get_by_key("cc" * 32)
            assert contact is not None
            assert contact.name == "Charlie"
            assert contact.on_radio is False
            assert contact.last_seen == 1700000000

            mock_broadcast.assert_called_once()
            event_type, contact_data = mock_broadcast.call_args[0]
            assert event_type == "contact"
            assert contact_data["public_key"] == "cc" * 32

    @pytest.mark.asyncio
    async def test_returns_early_on_empty_public_key(self, test_db):
        """Handler exits without upserting when public_key is empty."""
        from app.event_handlers import on_new_contact

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {"public_key": "", "adv_name": "Ghost"}

            await on_new_contact(MockEvent())

            mock_broadcast.assert_not_called()

            # No contacts should exist
            contacts = await ContactRepository.get_all()
            assert len(contacts) == 0

    @pytest.mark.asyncio
    async def test_returns_early_on_missing_public_key(self, test_db):
        """Handler exits without upserting when public_key field is absent."""
        from app.event_handlers import on_new_contact

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {"adv_name": "NoKey"}

            await on_new_contact(MockEvent())

            mock_broadcast.assert_not_called()

            contacts = await ContactRepository.get_all()
            assert len(contacts) == 0
