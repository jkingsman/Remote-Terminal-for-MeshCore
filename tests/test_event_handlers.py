"""Tests for event handler logic.

These tests verify the ACK tracking and repeat detection mechanisms
that determine message delivery confirmation.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from app.event_handlers import (
    _cleanup_expired_acks,
    _pending_acks,
    track_pending_ack,
)
from app.packet_processor import (
    _cleanup_expired_repeats,
    _pending_repeat_expiry,
    _pending_repeats,
    track_pending_repeat,
)


@pytest.fixture(autouse=True)
def clear_pending_state():
    """Clear pending ACKs and repeats before each test."""
    _pending_acks.clear()
    _pending_repeats.clear()
    _pending_repeat_expiry.clear()
    yield
    _pending_acks.clear()
    _pending_repeats.clear()
    _pending_repeat_expiry.clear()


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

        _cleanup_expired_acks()

        assert "expired" not in _pending_acks
        assert "valid" in _pending_acks

    def test_cleanup_uses_2x_timeout_buffer(self):
        """Cleanup uses 2x timeout as buffer before expiring."""
        # ACK created 5 seconds ago with 10 second timeout
        # 2x buffer = 20 seconds, so should NOT be expired yet
        _pending_acks["recent"] = (1, time.time() - 5, 10000)

        _cleanup_expired_acks()

        assert "recent" in _pending_acks


class TestRepeatTracking:
    """Test repeat tracking for channel/flood messages."""

    def test_track_pending_repeat_stores_correctly(self):
        """Pending repeats are stored with channel key, text hash, and timestamp."""
        channel_key = "0123456789ABCDEF0123456789ABCDEF"
        track_pending_repeat(channel_key=channel_key, text="Hello", timestamp=1700000000, message_id=99)

        # Key is (channel_key, text_hash, timestamp)
        text_hash = str(hash("Hello"))
        key = (channel_key, text_hash, 1700000000)

        assert key in _pending_repeats
        assert _pending_repeats[key] == 99

    def test_same_message_different_channels_tracked_separately(self):
        """Same message on different channels creates separate entries."""
        track_pending_repeat(channel_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1", text="Test", timestamp=1000, message_id=1)
        track_pending_repeat(channel_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA2", text="Test", timestamp=1000, message_id=2)

        assert len(_pending_repeats) == 2

    def test_same_message_different_timestamps_tracked_separately(self):
        """Same message with different timestamps creates separate entries."""
        channel_key = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        track_pending_repeat(channel_key=channel_key, text="Test", timestamp=1000, message_id=1)
        track_pending_repeat(channel_key=channel_key, text="Test", timestamp=1001, message_id=2)

        assert len(_pending_repeats) == 2

    def test_cleanup_removes_old_repeats(self):
        """Expired repeats are removed during cleanup."""
        channel_key = "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
        text_hash = str(hash("test"))
        old_key = (channel_key, text_hash, 1000)
        new_key = (channel_key, text_hash, 2000)

        # Set up entries with expiry times
        _pending_repeats[old_key] = 1
        _pending_repeats[new_key] = 2
        _pending_repeat_expiry[old_key] = time.time() - 10  # Already expired
        _pending_repeat_expiry[new_key] = time.time() + 30  # Still valid

        _cleanup_expired_repeats()

        assert old_key not in _pending_repeats
        assert new_key in _pending_repeats


class TestAckEventHandler:
    """Test the on_ack event handler."""

    @pytest.mark.asyncio
    async def test_ack_matches_pending_message(self):
        """Matching ACK code updates message and broadcasts."""
        from app.event_handlers import on_ack

        # Setup pending ACK
        track_pending_ack("deadbeef", message_id=123, timeout_ms=10000)

        # Mock dependencies
        with patch("app.event_handlers.MessageRepository") as mock_repo, \
             patch("app.event_handlers.broadcast_event") as mock_broadcast:
            mock_repo.mark_acked = AsyncMock()

            # Create mock event
            class MockEvent:
                payload = {"code": "deadbeef"}

            await on_ack(MockEvent())

            # Verify message marked as acked
            mock_repo.mark_acked.assert_called_once_with(123)

            # Verify broadcast sent
            mock_broadcast.assert_called_once_with("message_acked", {"message_id": 123})

            # Verify pending ACK removed
            assert "deadbeef" not in _pending_acks

    @pytest.mark.asyncio
    async def test_ack_no_match_does_nothing(self):
        """Non-matching ACK code is ignored."""
        from app.event_handlers import on_ack

        track_pending_ack("expected", message_id=1, timeout_ms=10000)

        with patch("app.event_handlers.MessageRepository") as mock_repo, \
             patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {"code": "different"}

            await on_ack(MockEvent())

            mock_repo.mark_acked.assert_not_called()
            mock_broadcast.assert_not_called()
            assert "expected" in _pending_acks

    @pytest.mark.asyncio
    async def test_ack_empty_code_ignored(self):
        """ACK with empty code is ignored."""
        from app.event_handlers import on_ack

        with patch("app.event_handlers.MessageRepository") as mock_repo:
            mock_repo.mark_acked = AsyncMock()

            class MockEvent:
                payload = {"code": ""}

            await on_ack(MockEvent())

            mock_repo.mark_acked.assert_not_called()
