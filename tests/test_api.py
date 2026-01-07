"""Tests for API endpoints.

These tests verify the REST API behavior for critical operations.
Uses FastAPI's TestClient for synchronous testing.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestHealthEndpoint:
    """Test the health check endpoint."""

    def test_health_returns_connection_status(self):
        """Health endpoint returns radio connection status."""
        from fastapi.testclient import TestClient

        with patch("app.routers.health.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.port = "/dev/ttyUSB0"

            from app.main import app
            client = TestClient(app)

            response = client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert data["radio_connected"] is True
            assert data["serial_port"] == "/dev/ttyUSB0"

    def test_health_disconnected_state(self):
        """Health endpoint reflects disconnected radio."""
        from fastapi.testclient import TestClient

        with patch("app.routers.health.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.port = None

            from app.main import app
            client = TestClient(app)

            response = client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert data["radio_connected"] is False
            assert data["serial_port"] is None


class TestMessagesEndpoint:
    """Test message-related endpoints."""

    def test_send_direct_message_requires_connection(self):
        """Sending message when disconnected returns 503."""
        from fastapi.testclient import TestClient

        with patch("app.dependencies.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.meshcore = None

            from app.main import app
            client = TestClient(app)

            response = client.post(
                "/messages/direct",
                json={"destination": "abc123", "text": "Hello"}
            )

            assert response.status_code == 503
            assert "not connected" in response.json()["detail"].lower()

    def test_send_channel_message_requires_connection(self):
        """Sending channel message when disconnected returns 503."""
        from fastapi.testclient import TestClient

        with patch("app.dependencies.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.meshcore = None

            from app.main import app
            client = TestClient(app)

            response = client.post(
                "/messages/channel",
                json={"channel_key": "0123456789ABCDEF0123456789ABCDEF", "text": "Hello"}
            )

            assert response.status_code == 503

    def test_send_direct_message_contact_not_found(self):
        """Sending to unknown contact returns 404."""
        from fastapi.testclient import TestClient

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix.return_value = None

        with patch("app.dependencies.radio_manager") as mock_rm, \
             patch("app.repository.ContactRepository.get_by_key_or_prefix", new_callable=AsyncMock) as mock_get:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc
            mock_get.return_value = None

            from app.main import app
            client = TestClient(app)

            response = client.post(
                "/messages/direct",
                json={"destination": "nonexistent", "text": "Hello"}
            )

            assert response.status_code == 404
            assert "not found" in response.json()["detail"].lower()


class TestChannelsEndpoint:
    """Test channel-related endpoints."""

    @pytest.mark.asyncio
    async def test_create_hashtag_channel_derives_key(self):
        """Creating hashtag channel derives key from name and stores in DB."""
        import hashlib
        from app.routers.channels import create_channel, CreateChannelRequest

        with patch("app.routers.channels.ChannelRepository") as mock_repo:
            mock_repo.upsert = AsyncMock()

            request = CreateChannelRequest(name="#mychannel")

            result = await create_channel(request)

            # Verify the key derivation - channel stored in DB, not pushed to radio
            expected_key_hex = hashlib.sha256(b"#mychannel").digest()[:16].hex().upper()
            mock_repo.upsert.assert_called_once()
            call_args = mock_repo.upsert.call_args
            assert call_args[1]["key"] == expected_key_hex
            assert call_args[1]["name"] == "#mychannel"
            assert call_args[1]["is_hashtag"] is True
            assert call_args[1]["on_radio"] is False  # Not pushed to radio on create

            # Verify response
            assert result.key == expected_key_hex
            assert result.name == "#mychannel"

    @pytest.mark.asyncio
    async def test_create_channel_with_explicit_key(self):
        """Creating channel with explicit key uses provided key."""
        from app.routers.channels import create_channel, CreateChannelRequest

        with patch("app.routers.channels.ChannelRepository") as mock_repo:
            mock_repo.upsert = AsyncMock()

            explicit_key = "0123456789abcdef0123456789abcdef"  # 32 hex chars = 16 bytes
            request = CreateChannelRequest(name="private", key=explicit_key)

            result = await create_channel(request)

            # Verify key stored in DB correctly (stored as uppercase hex)
            mock_repo.upsert.assert_called_once()
            call_args = mock_repo.upsert.call_args
            assert call_args[1]["key"] == explicit_key.upper()
            assert call_args[1]["name"] == "private"
            assert call_args[1]["on_radio"] is False

            # Verify response
            assert result.key == explicit_key.upper()


class TestPacketsEndpoint:
    """Test packet decryption endpoints."""

    def test_get_undecrypted_count(self):
        """Get undecrypted packet count returns correct value."""
        from fastapi.testclient import TestClient

        with patch("app.routers.packets.RawPacketRepository") as mock_repo:
            mock_repo.get_undecrypted_count = AsyncMock(return_value=42)

            from app.main import app
            client = TestClient(app)

            response = client.get("/packets/undecrypted/count")

            assert response.status_code == 200
            assert response.json()["count"] == 42
