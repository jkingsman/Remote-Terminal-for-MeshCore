"""Tests for bot-disable enforcement.

Verifies that when disable_bots=True:
- POST /api/fanout with type=bot returns 403
- Health endpoint includes bots_disabled=True
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.repository.fanout import FanoutConfigRepository
from app.routers.fanout import FanoutConfigCreate, create_fanout_config
from app.routers.health import build_health_data


class TestDisableBotsFanoutEndpoint:
    """Test that bot creation via fanout router is rejected when bots are disabled."""

    @pytest.mark.asyncio
    async def test_bot_create_rejected_as_invalid_type(self, test_db):
        """POST /api/fanout with type=bot is no longer a valid type.

        Python bots moved to the Bots workspace (/api/bots, migration 064);
        the fanout API refuses to create new ones regardless of disable state.
        """
        with pytest.raises(HTTPException) as exc_info:
            await create_fanout_config(
                FanoutConfigCreate(
                    type="bot",
                    name="Test Bot",
                    config={"code": "def bot(**k): pass"},
                    enabled=False,
                )
            )

        assert exc_info.value.status_code == 400
        assert "invalid type" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_mqtt_create_allowed_when_bots_disabled(self, test_db):
        """Non-bot fanout configs can still be created when bots are disabled."""
        with patch(
            "app.routers.fanout.fanout_manager.get_bots_disabled_source", return_value="env"
        ):
            # Create as disabled so fanout_manager.reload_config is not called
            result = await create_fanout_config(
                FanoutConfigCreate(
                    type="mqtt_private",
                    name="Test MQTT",
                    config={"broker_host": "localhost", "broker_port": 1883},
                    enabled=False,
                )
            )
            assert result["type"] == "mqtt_private"

    @pytest.mark.asyncio
    async def test_bot_create_rejected_even_when_disabled_until_restart(self, test_db):
        with patch(
            "app.routers.fanout.fanout_manager.get_bots_disabled_source",
            return_value="until_restart",
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_fanout_config(
                    FanoutConfigCreate(
                        type="bot",
                        name="Test Bot",
                        config={"code": "def bot(**k): pass"},
                        enabled=False,
                    )
                )

            # Invalid-type rejection now precedes the disabled-state check.
            assert exc_info.value.status_code == 400
            assert "invalid type" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_disable_bots_until_restart_endpoint(self, test_db):
        from app.routers.fanout import disable_bots_until_restart

        await FanoutConfigRepository.create(
            config_type="bot",
            name="Test Bot",
            config={"code": "def bot(**k): pass"},
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        with (
            patch(
                "app.routers.fanout.fanout_manager.disable_bots_until_restart",
                new=AsyncMock(return_value="until_restart"),
            ) as mock_disable,
            patch("app.websocket.broadcast_health") as mock_broadcast_health,
            patch("app.services.radio_runtime.radio_runtime") as mock_radio_runtime,
        ):
            mock_radio_runtime.is_connected = True
            mock_radio_runtime.connection_info = "TCP: 1.2.3.4:4000"

            result = await disable_bots_until_restart()

        mock_disable.assert_awaited_once()
        mock_broadcast_health.assert_called_once_with(True, "TCP: 1.2.3.4:4000")
        assert result == {
            "status": "ok",
            "bots_disabled": True,
            "bots_disabled_source": "until_restart",
        }


class TestDisableBotsHealthEndpoint:
    """Test that bots_disabled is exposed in health data."""

    @pytest.mark.asyncio
    async def test_health_includes_bots_disabled_true(self, test_db):
        with patch(
            "app.routers.health.settings",
            MagicMock(disable_bots=True, basic_auth_enabled=False, database_path="x"),
        ):
            with patch("app.routers.health.os.path.getsize", return_value=0):
                data = await build_health_data(True, "TCP: 1.2.3.4:4000")

        assert data["bots_disabled"] is True

    @pytest.mark.asyncio
    async def test_health_includes_bots_disabled_false(self, test_db):
        with patch(
            "app.routers.health.settings",
            MagicMock(disable_bots=False, basic_auth_enabled=False, database_path="x"),
        ):
            with patch("app.routers.health.os.path.getsize", return_value=0):
                data = await build_health_data(True, "TCP: 1.2.3.4:4000")

        assert data["bots_disabled"] is False

    @pytest.mark.asyncio
    async def test_health_includes_basic_auth_enabled(self, test_db):
        with patch(
            "app.routers.health.settings",
            MagicMock(disable_bots=False, basic_auth_enabled=True, database_path="x"),
        ):
            with patch("app.routers.health.os.path.getsize", return_value=0):
                data = await build_health_data(True, "TCP: 1.2.3.4:4000")

        assert data["basic_auth_enabled"] is True

    @pytest.mark.asyncio
    async def test_health_includes_runtime_bot_disable_source(self, test_db):
        with (
            patch(
                "app.routers.health.settings",
                MagicMock(disable_bots=False, basic_auth_enabled=False, database_path="x"),
            ),
            patch("app.routers.health.os.path.getsize", return_value=0),
            patch("app.fanout.manager.fanout_manager") as mock_fm,
        ):
            mock_fm.get_statuses.return_value = {}
            mock_fm.get_bots_disabled_source.return_value = "until_restart"
            data = await build_health_data(True, "TCP: 1.2.3.4:4000")

        assert data["bots_disabled"] is True
        assert data["bots_disabled_source"] == "until_restart"
