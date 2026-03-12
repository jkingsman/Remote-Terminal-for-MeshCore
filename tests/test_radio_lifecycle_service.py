from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.radio_lifecycle import (
    prepare_connected_radio,
    reconnect_and_prepare_radio,
    run_post_connect_setup,
)


class TestPrepareConnectedRadio:
    @pytest.mark.asyncio
    async def test_runs_setup_then_broadcasts_health(self):
        radio_manager = MagicMock()
        radio_manager._last_connected = False
        radio_manager.connection_info = "TCP: test:4000"

        call_order: list[str] = []

        async def _setup():
            call_order.append("setup")

        radio_manager.post_connect_setup = AsyncMock(side_effect=_setup)

        with patch("app.websocket.broadcast_health") as mock_broadcast:
            await prepare_connected_radio(radio_manager, broadcast_on_success=True)

        assert call_order == ["setup"]
        assert radio_manager._last_connected is True
        mock_broadcast.assert_called_once_with(True, "TCP: test:4000")

    @pytest.mark.asyncio
    async def test_can_skip_broadcast(self):
        radio_manager = MagicMock()
        radio_manager._last_connected = False
        radio_manager.connection_info = "TCP: test:4000"
        radio_manager.post_connect_setup = AsyncMock()

        with patch("app.websocket.broadcast_health") as mock_broadcast:
            await prepare_connected_radio(radio_manager, broadcast_on_success=False)

        assert radio_manager._last_connected is True
        mock_broadcast.assert_not_called()


class TestReconnectAndPrepareRadio:
    @pytest.mark.asyncio
    async def test_reconnects_without_early_health_broadcast(self):
        radio_manager = MagicMock()
        radio_manager._last_connected = False
        radio_manager.connection_info = "Serial: /dev/ttyUSB0"

        reconnect_calls: list[bool] = []
        call_order: list[str] = []

        async def _reconnect(*, broadcast_on_success: bool):
            reconnect_calls.append(broadcast_on_success)
            call_order.append("reconnect")
            return True

        async def _setup():
            call_order.append("setup")

        radio_manager.reconnect = AsyncMock(side_effect=_reconnect)
        radio_manager.post_connect_setup = AsyncMock(side_effect=_setup)

        with patch("app.websocket.broadcast_health") as mock_broadcast:
            result = await reconnect_and_prepare_radio(radio_manager, broadcast_on_success=True)

        assert result is True
        assert reconnect_calls == [False]
        assert call_order == ["reconnect", "setup"]
        assert radio_manager._last_connected is True
        mock_broadcast.assert_called_once_with(True, "Serial: /dev/ttyUSB0")

    @pytest.mark.asyncio
    async def test_returns_false_without_running_setup_when_reconnect_fails(self):
        radio_manager = MagicMock()
        radio_manager.reconnect = AsyncMock(return_value=False)
        radio_manager.post_connect_setup = AsyncMock()

        with patch("app.websocket.broadcast_health") as mock_broadcast:
            result = await reconnect_and_prepare_radio(radio_manager, broadcast_on_success=True)

        assert result is False
        radio_manager.post_connect_setup.assert_not_awaited()
        mock_broadcast.assert_not_called()


class TestRunPostConnectSetup:
    @pytest.mark.asyncio
    async def test_uses_current_meshcore_after_waiting_for_operation_lock(self):
        initial_mc = MagicMock()
        initial_mc.commands.send_device_query = AsyncMock(return_value=None)
        initial_mc.commands.set_flood_scope = AsyncMock(return_value=None)
        initial_mc._reader = MagicMock()
        initial_mc._reader.handle_rx = AsyncMock()
        initial_mc.start_auto_message_fetching = AsyncMock()

        replacement_mc = MagicMock()
        replacement_mc.commands.send_device_query = AsyncMock(
            return_value=MagicMock(payload={"max_channels": 8})
        )
        replacement_mc.commands.set_flood_scope = AsyncMock(return_value=None)
        replacement_mc._reader = MagicMock()
        replacement_mc._reader.handle_rx = AsyncMock()
        replacement_mc.start_auto_message_fetching = AsyncMock()

        radio_manager = MagicMock()
        radio_manager.meshcore = initial_mc
        radio_manager._setup_lock = None
        radio_manager._setup_in_progress = False
        radio_manager._setup_complete = False
        radio_manager.max_channels = 40
        radio_manager.path_hash_mode = 0
        radio_manager.path_hash_mode_supported = False

        async def _acquire(*args, **kwargs):
            radio_manager.meshcore = replacement_mc

        radio_manager._acquire_operation_lock = AsyncMock(side_effect=_acquire)
        radio_manager._release_operation_lock = MagicMock()

        with (
            patch("app.event_handlers.register_event_handlers") as mock_register_handlers,
            patch("app.keystore.export_and_store_private_key", new=AsyncMock()) as mock_export_key,
            patch("app.radio_sync.sync_radio_time", new=AsyncMock()) as mock_sync_time,
            patch(
                "app.repository.AppSettingsRepository.get",
                new=AsyncMock(return_value=MagicMock(flood_scope=None)),
            ),
            patch("app.radio_sync.sync_and_offload_all", new=AsyncMock(return_value={"synced": 0})),
            patch("app.radio_sync.send_advertisement", new=AsyncMock(return_value=False)),
            patch("app.radio_sync.drain_pending_messages", new=AsyncMock(return_value=0)),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.start_periodic_advert"),
            patch("app.radio_sync.start_message_polling"),
        ):
            await run_post_connect_setup(radio_manager)

        mock_register_handlers.assert_called_once_with(replacement_mc)
        mock_export_key.assert_awaited_once_with(replacement_mc)
        mock_sync_time.assert_awaited_once_with(replacement_mc)
        replacement_mc.start_auto_message_fetching.assert_awaited_once()
        initial_mc.start_auto_message_fetching.assert_not_called()
        assert radio_manager.max_channels == 8
