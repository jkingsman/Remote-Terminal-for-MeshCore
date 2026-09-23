from unittest.mock import AsyncMock, MagicMock

import pytest
from meshcore import EventType

from app.routers.radio import RadioConfigUpdate, RadioSettings
from app.services.radio_commands import (
    KeystoreRefreshError,
    PathHashModeUnsupportedError,
    RadioCommandRejectedError,
    RepeatModeUnsupportedError,
    apply_radio_config_update,
    import_private_key_and_refresh_keystore,
)


def _radio_result(event_type=EventType.OK, payload=None):
    result = MagicMock()
    result.type = event_type
    result.payload = payload or {}
    return result


def _mock_meshcore_with_info():
    mc = MagicMock()
    mc.self_info = {
        "adv_lat": 10.0,
        "adv_lon": 20.0,
        "radio_freq": 910.525,
        "radio_bw": 62.5,
        "radio_sf": 7,
        "radio_cr": 5,
    }
    mc.commands = MagicMock()
    mc.commands.set_name = AsyncMock()
    mc.commands.set_coords = AsyncMock()
    mc.commands.set_tx_power = AsyncMock()
    mc.commands.set_radio = AsyncMock(return_value=_radio_result())
    mc.commands.set_path_hash_mode = AsyncMock(return_value=_radio_result())
    mc.commands.set_advert_loc_policy = AsyncMock(return_value=_radio_result())
    mc.commands.set_multi_acks = AsyncMock(return_value=_radio_result())
    mc.commands.send_appstart = AsyncMock()
    mc.commands.import_private_key = AsyncMock(return_value=_radio_result())
    return mc


class TestApplyRadioConfigUpdate:
    @pytest.mark.asyncio
    async def test_updates_requested_fields_and_refreshes_info(self):
        mc = _mock_meshcore_with_info()
        sync_radio_time_fn = AsyncMock()
        set_path_hash_mode = MagicMock()
        update = RadioConfigUpdate(
            name="NodeUpdated",
            lat=1.23,
            tx_power=17,
            radio=RadioSettings(freq=910.525, bw=62.5, sf=7, cr=5),
            path_hash_mode=1,
        )

        await apply_radio_config_update(
            mc,
            update,
            path_hash_mode_supported=True,
            set_path_hash_mode=set_path_hash_mode,
            repeat_supported=False,
            repeat_enabled=False,
            allowed_repeat_freqs=[],
            set_repeat_enabled=MagicMock(),
            sync_radio_time_fn=sync_radio_time_fn,
        )

        mc.commands.set_name.assert_awaited_once_with("NodeUpdated")
        mc.commands.set_coords.assert_awaited_once_with(lat=1.23, lon=20.0)
        mc.commands.set_tx_power.assert_awaited_once_with(val=17)
        mc.commands.set_radio.assert_awaited_once_with(
            freq=910.525, bw=62.5, sf=7, cr=5, repeat=None
        )
        mc.commands.set_path_hash_mode.assert_awaited_once_with(1)
        set_path_hash_mode.assert_called_once_with(1)
        sync_radio_time_fn.assert_awaited_once_with(mc)
        mc.commands.send_appstart.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updates_advert_location_source(self):
        mc = _mock_meshcore_with_info()

        await apply_radio_config_update(
            mc,
            RadioConfigUpdate(advert_location_source="current"),
            path_hash_mode_supported=True,
            set_path_hash_mode=MagicMock(),
            repeat_supported=False,
            repeat_enabled=False,
            allowed_repeat_freqs=[],
            set_repeat_enabled=MagicMock(),
            sync_radio_time_fn=AsyncMock(),
        )

        mc.commands.set_advert_loc_policy.assert_awaited_once_with(1)
        mc.commands.send_appstart.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updates_multi_acks_enabled(self):
        mc = _mock_meshcore_with_info()

        await apply_radio_config_update(
            mc,
            RadioConfigUpdate(multi_acks_enabled=True),
            path_hash_mode_supported=True,
            set_path_hash_mode=MagicMock(),
            repeat_supported=False,
            repeat_enabled=False,
            allowed_repeat_freqs=[],
            set_repeat_enabled=MagicMock(),
            sync_radio_time_fn=AsyncMock(),
        )

        mc.commands.set_multi_acks.assert_awaited_once_with(1)
        mc.commands.send_appstart.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_when_radio_rejects_multi_acks(self):
        mc = _mock_meshcore_with_info()
        mc.commands.set_multi_acks = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"error": "nope"})
        )

        with pytest.raises(RadioCommandRejectedError):
            await apply_radio_config_update(
                mc,
                RadioConfigUpdate(multi_acks_enabled=False),
                path_hash_mode_supported=True,
                set_path_hash_mode=MagicMock(),
                repeat_supported=False,
                repeat_enabled=False,
                allowed_repeat_freqs=[],
                set_repeat_enabled=MagicMock(),
                sync_radio_time_fn=AsyncMock(),
            )

        mc.commands.send_appstart.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_when_radio_rejects_advert_location_source(self):
        mc = _mock_meshcore_with_info()
        mc.commands.set_advert_loc_policy = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"error": "nope"})
        )

        with pytest.raises(RadioCommandRejectedError):
            await apply_radio_config_update(
                mc,
                RadioConfigUpdate(advert_location_source="off"),
                path_hash_mode_supported=True,
                set_path_hash_mode=MagicMock(),
                repeat_supported=False,
                repeat_enabled=False,
                allowed_repeat_freqs=[],
                set_repeat_enabled=MagicMock(),
                sync_radio_time_fn=AsyncMock(),
            )

        mc.commands.send_appstart.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_unsupported_path_hash_mode(self):
        mc = _mock_meshcore_with_info()
        update = RadioConfigUpdate(path_hash_mode=1)

        with pytest.raises(PathHashModeUnsupportedError):
            await apply_radio_config_update(
                mc,
                update,
                path_hash_mode_supported=False,
                set_path_hash_mode=MagicMock(),
                repeat_supported=False,
                repeat_enabled=False,
                allowed_repeat_freqs=[],
                set_repeat_enabled=MagicMock(),
                sync_radio_time_fn=AsyncMock(),
            )

        mc.commands.set_path_hash_mode.assert_not_awaited()
        mc.commands.send_appstart.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_when_radio_rejects_path_hash_mode(self):
        mc = _mock_meshcore_with_info()
        mc.commands.set_path_hash_mode = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"error": "nope"})
        )
        update = RadioConfigUpdate(path_hash_mode=1)
        set_path_hash_mode = MagicMock()

        with pytest.raises(RadioCommandRejectedError):
            await apply_radio_config_update(
                mc,
                update,
                path_hash_mode_supported=True,
                set_path_hash_mode=set_path_hash_mode,
                repeat_supported=False,
                repeat_enabled=False,
                allowed_repeat_freqs=[],
                set_repeat_enabled=MagicMock(),
                sync_radio_time_fn=AsyncMock(),
            )

        set_path_hash_mode.assert_not_called()
        mc.commands.send_appstart.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_radio_param_update_preserves_current_repeat(self):
        mc = _mock_meshcore_with_info()
        set_repeat_enabled = MagicMock()

        await apply_radio_config_update(
            mc,
            RadioConfigUpdate(radio=RadioSettings(freq=918.0, bw=62.5, sf=8, cr=5)),
            path_hash_mode_supported=True,
            set_path_hash_mode=MagicMock(),
            repeat_supported=True,
            repeat_enabled=True,
            allowed_repeat_freqs=[],
            set_repeat_enabled=set_repeat_enabled,
            sync_radio_time_fn=AsyncMock(),
        )

        mc.commands.set_radio.assert_awaited_once_with(freq=918.0, bw=62.5, sf=8, cr=5, repeat=True)
        set_repeat_enabled.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_repeat_only_update_reuses_current_radio_params(self):
        mc = _mock_meshcore_with_info()
        set_repeat_enabled = MagicMock()

        await apply_radio_config_update(
            mc,
            RadioConfigUpdate(repeat_enabled=True),
            path_hash_mode_supported=True,
            set_path_hash_mode=MagicMock(),
            repeat_supported=True,
            repeat_enabled=False,
            allowed_repeat_freqs=[],
            set_repeat_enabled=set_repeat_enabled,
            sync_radio_time_fn=AsyncMock(),
        )

        mc.commands.set_radio.assert_awaited_once_with(
            freq=910.525, bw=62.5, sf=7, cr=5, repeat=True
        )
        set_repeat_enabled.assert_called_once_with(True)
        mc.commands.send_appstart.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_when_radio_rejects_repeat(self):
        mc = _mock_meshcore_with_info()
        mc.commands.set_radio = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"error_code": 6})
        )
        set_repeat_enabled = MagicMock()

        with pytest.raises(RadioCommandRejectedError, match="permitted frequencies"):
            await apply_radio_config_update(
                mc,
                RadioConfigUpdate(repeat_enabled=True),
                path_hash_mode_supported=True,
                set_path_hash_mode=MagicMock(),
                repeat_supported=True,
                repeat_enabled=False,
                allowed_repeat_freqs=[],
                set_repeat_enabled=set_repeat_enabled,
                sync_radio_time_fn=AsyncMock(),
            )

        set_repeat_enabled.assert_not_called()
        mc.commands.send_appstart.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejection_quotes_node_allowed_repeat_freqs(self):
        mc = _mock_meshcore_with_info()
        mc.commands.set_radio = AsyncMock(
            return_value=_radio_result(
                EventType.ERROR, {"error_code": 6, "code_string": "ERR_CODE_ILLEGAL_ARG"}
            )
        )

        with pytest.raises(RadioCommandRejectedError) as exc:
            await apply_radio_config_update(
                mc,
                RadioConfigUpdate(repeat_enabled=True),
                path_hash_mode_supported=True,
                set_path_hash_mode=MagicMock(),
                repeat_supported=True,
                repeat_enabled=False,
                allowed_repeat_freqs=[(910.525, 910.525), (433.0, 434.5)],
                set_repeat_enabled=MagicMock(),
                sync_radio_time_fn=AsyncMock(),
            )

        assert str(exc.value) == (
            "Firmware rejected the radio settings (ERR_CODE_ILLEGAL_ARG). "
            "Repeat mode is only allowed on: 910.525, 433.000–434.500 MHz"
        )

    @pytest.mark.asyncio
    async def test_rejects_repeat_when_firmware_does_not_support_it(self):
        mc = _mock_meshcore_with_info()

        with pytest.raises(RepeatModeUnsupportedError):
            await apply_radio_config_update(
                mc,
                RadioConfigUpdate(repeat_enabled=True),
                path_hash_mode_supported=True,
                set_path_hash_mode=MagicMock(),
                repeat_supported=False,
                repeat_enabled=False,
                allowed_repeat_freqs=[],
                set_repeat_enabled=MagicMock(),
                sync_radio_time_fn=AsyncMock(),
            )

        mc.commands.set_radio.assert_not_awaited()


class TestImportPrivateKeyAndRefreshKeystore:
    @pytest.mark.asyncio
    async def test_rejects_radio_error(self):
        mc = _mock_meshcore_with_info()
        mc.commands.import_private_key = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"error": "failed"})
        )
        export_fn = AsyncMock(return_value=True)

        with pytest.raises(RadioCommandRejectedError):
            await import_private_key_and_refresh_keystore(
                mc,
                b"\xaa" * 64,
                export_and_store_private_key_fn=export_fn,
            )

        export_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retries_keystore_refresh_once(self):
        mc = _mock_meshcore_with_info()
        export_fn = AsyncMock(side_effect=[False, True])

        await import_private_key_and_refresh_keystore(
            mc,
            b"\xaa" * 64,
            export_and_store_private_key_fn=export_fn,
        )

        mc.commands.import_private_key.assert_awaited_once_with(b"\xaa" * 64)
        assert export_fn.await_count == 2

    @pytest.mark.asyncio
    async def test_raises_when_keystore_refresh_fails_twice(self):
        mc = _mock_meshcore_with_info()
        export_fn = AsyncMock(return_value=False)

        with pytest.raises(KeystoreRefreshError):
            await import_private_key_and_refresh_keystore(
                mc,
                b"\xaa" * 64,
                export_and_store_private_key_fn=export_fn,
            )

        assert export_fn.await_count == 2
