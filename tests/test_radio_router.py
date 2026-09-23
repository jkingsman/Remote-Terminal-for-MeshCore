"""Tests for radio router endpoint logic."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType
from pydantic import ValidationError

from app.models import (
    CONTACT_TYPE_REPEATER,
    Contact,
    RadioRegionDiscoveryRequest,
    RadioTraceHopRequest,
    RadioTraceRequest,
)
from app.radio import RadioManager, radio_manager
from app.routers.radio import (
    PrivateKeyUpdate,
    RadioAdvertiseRequest,
    RadioConfigResponse,
    RadioConfigUpdate,
    RadioDiscoveryRequest,
    RadioSettings,
    _dedupe_region_names,
    disconnect_radio,
    discover_mesh,
    discover_regions,
    get_private_key,
    get_radio_config,
    reboot_radio,
    reconnect_radio,
    send_advertisement,
    set_private_key,
    trace_path,
    update_radio_config,
)
from app.services.radio_runtime import RadioRuntime


def _radio_result(event_type=EventType.OK, payload=None):
    result = MagicMock()
    result.type = event_type
    result.payload = payload or {}
    return result


def _noop_radio_operation(mc=None):
    """Factory for a no-op radio_operation context manager that yields mc."""

    @asynccontextmanager
    async def _ctx(*_args, **_kwargs):
        yield mc

    return _ctx


def _runtime(manager):
    return RadioRuntime(lambda: manager)


@pytest.fixture(autouse=True)
def _reset_radio_state():
    """Save/restore radio_manager state so tests don't leak."""
    prev = radio_manager._meshcore
    prev_lock = radio_manager._operation_lock
    yield
    radio_manager._meshcore = prev
    radio_manager._operation_lock = prev_lock


def _mock_meshcore_with_info():
    mc = MagicMock()
    mc.self_info = {
        "public_key": "aa" * 32,
        "name": "NodeA",
        "adv_lat": 10.0,
        "adv_lon": 20.0,
        "tx_power": 17,
        "max_tx_power": 22,
        "radio_freq": 910.525,
        "radio_bw": 62.5,
        "radio_sf": 7,
        "radio_cr": 5,
        "adv_loc_policy": 2,
        "multi_acks": 0,
    }
    mc.commands = MagicMock()
    mc.commands.set_name = AsyncMock()
    mc.commands.set_coords = AsyncMock()
    mc.commands.set_tx_power = AsyncMock()
    mc.commands.set_radio = AsyncMock()
    mc.commands.set_advert_loc_policy = AsyncMock(return_value=_radio_result())
    mc.commands.set_multi_acks = AsyncMock(return_value=_radio_result())
    mc.commands.send_appstart = AsyncMock()
    mc.commands.import_private_key = AsyncMock(return_value=_radio_result())
    mc.commands.send_node_discover_req = AsyncMock(return_value=_radio_result())
    mc.stop_auto_message_fetching = AsyncMock()
    mc.start_auto_message_fetching = AsyncMock()
    return mc


class TestGetRadioConfig:
    @pytest.mark.asyncio
    async def test_maps_self_info_to_response(self):
        mc = _mock_meshcore_with_info()
        with patch("app.routers.radio.radio_manager.require_connected", return_value=mc):
            response = await get_radio_config()

        assert response.public_key == "aa" * 32
        assert response.name == "NodeA"
        assert response.lat == 10.0
        assert response.lon == 20.0
        assert response.radio.freq == 910.525
        assert response.radio.cr == 5
        assert response.advert_location_source == "current"
        assert response.multi_acks_enabled is False

    @pytest.mark.asyncio
    async def test_maps_multi_acks_to_response(self):
        mc = _mock_meshcore_with_info()
        mc.self_info["multi_acks"] = 1

        with patch("app.routers.radio.radio_manager.require_connected", return_value=mc):
            response = await get_radio_config()

        assert response.multi_acks_enabled is True

    @pytest.mark.asyncio
    async def test_maps_repeat_state_to_response(self):
        mc = _mock_meshcore_with_info()

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "repeat_enabled", True),
            patch.object(radio_manager, "repeat_supported", True),
            patch.object(radio_manager, "allowed_repeat_freqs", [(433.0, 433.0), (910.0, 915.0)]),
        ):
            response = await get_radio_config()

        assert response.repeat_enabled is True
        assert response.repeat_supported is True
        assert [(r.min_mhz, r.max_mhz) for r in response.allowed_repeat_freqs] == [
            (433.0, 433.0),
            (910.0, 915.0),
        ]

    @pytest.mark.asyncio
    async def test_maps_any_nonzero_advert_location_policy_to_current(self):
        mc = _mock_meshcore_with_info()
        mc.self_info["adv_loc_policy"] = 1

        with patch("app.routers.radio.radio_manager.require_connected", return_value=mc):
            response = await get_radio_config()

        assert response.advert_location_source == "current"

    @pytest.mark.asyncio
    async def test_returns_423_when_self_info_missing(self):
        mc = MagicMock()
        mc.self_info = None
        with patch("app.routers.radio.radio_manager.require_connected", return_value=mc):
            with pytest.raises(HTTPException) as exc:
                await get_radio_config()

        assert exc.value.status_code == 423


class TestUpdateRadioConfig:
    @pytest.mark.asyncio
    async def test_updates_only_requested_fields_and_refreshes_info(self):
        mc = _mock_meshcore_with_info()
        expected = RadioConfigResponse(
            public_key="aa" * 32,
            name="NodeUpdated",
            lat=1.23,
            lon=20.0,
            tx_power=17,
            max_tx_power=22,
            radio=RadioSettings(freq=910.525, bw=62.5, sf=7, cr=5),
        )

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.radio.sync_radio_time", new_callable=AsyncMock) as mock_sync_time,
            patch(
                "app.routers.radio.get_radio_config", new_callable=AsyncMock, return_value=expected
            ),
        ):
            result = await update_radio_config(RadioConfigUpdate(name="NodeUpdated", lat=1.23))

        mc.commands.set_name.assert_awaited_once_with("NodeUpdated")
        mc.commands.set_coords.assert_awaited_once_with(lat=1.23, lon=20.0)
        mc.commands.set_tx_power.assert_not_awaited()
        mc.commands.set_radio.assert_not_awaited()
        mc.commands.send_appstart.assert_awaited_once()
        mock_sync_time.assert_awaited_once()
        assert result == expected

    @pytest.mark.asyncio
    async def test_updates_advert_location_source(self):
        mc = _mock_meshcore_with_info()
        expected = RadioConfigResponse(
            public_key="aa" * 32,
            name="NodeA",
            lat=10.0,
            lon=20.0,
            tx_power=17,
            max_tx_power=22,
            radio=RadioSettings(freq=910.525, bw=62.5, sf=7, cr=5),
            path_hash_mode=0,
            path_hash_mode_supported=False,
            advert_location_source="current",
            multi_acks_enabled=False,
        )

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.radio.sync_radio_time", new_callable=AsyncMock),
            patch(
                "app.routers.radio.get_radio_config", new_callable=AsyncMock, return_value=expected
            ),
        ):
            result = await update_radio_config(RadioConfigUpdate(advert_location_source="current"))

        mc.commands.set_advert_loc_policy.assert_awaited_once_with(1)
        assert result == expected

    @pytest.mark.asyncio
    async def test_updates_multi_acks_enabled(self):
        mc = _mock_meshcore_with_info()
        expected = RadioConfigResponse(
            public_key="aa" * 32,
            name="NodeA",
            lat=10.0,
            lon=20.0,
            tx_power=17,
            max_tx_power=22,
            radio=RadioSettings(freq=910.525, bw=62.5, sf=7, cr=5),
            path_hash_mode=0,
            path_hash_mode_supported=False,
            advert_location_source="current",
            multi_acks_enabled=True,
        )

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.radio.sync_radio_time", new_callable=AsyncMock),
            patch(
                "app.routers.radio.get_radio_config", new_callable=AsyncMock, return_value=expected
            ),
        ):
            result = await update_radio_config(RadioConfigUpdate(multi_acks_enabled=True))

        mc.commands.set_multi_acks.assert_awaited_once_with(1)
        assert result == expected

    def test_model_rejects_negative_path_hash_mode(self):
        with pytest.raises(ValidationError):
            RadioConfigUpdate(path_hash_mode=-1)

    def test_model_rejects_too_large_path_hash_mode(self):
        with pytest.raises(ValidationError):
            RadioConfigUpdate(path_hash_mode=3)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path_hash_mode", [-1, 3, 999])
    async def test_endpoint_rejects_invalid_path_hash_mode(self, client, path_hash_mode):
        response = await client.patch("/api/radio/config", json={"path_hash_mode": path_hash_mode})

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_path_hash_mode_when_firmware_does_not_support_it(self):
        mc = _mock_meshcore_with_info()

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch.object(radio_manager, "path_hash_mode_supported", False),
        ):
            with pytest.raises(HTTPException) as exc:
                await update_radio_config(RadioConfigUpdate(path_hash_mode=1))

        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_propagates_radio_error_when_setting_path_hash_mode(self):
        mc = _mock_meshcore_with_info()
        mc.commands.set_path_hash_mode = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"error": "nope"})
        )

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch.object(radio_manager, "path_hash_mode_supported", True),
            patch.object(radio_manager, "path_hash_mode", 0),
        ):
            with pytest.raises(HTTPException) as exc:
                await update_radio_config(RadioConfigUpdate(path_hash_mode=1))

        assert exc.value.status_code == 422
        assert "Failed to set path hash mode" in str(exc.value.detail)
        assert radio_manager.path_hash_mode == 0
        mc.commands.send_appstart.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_propagates_radio_error_when_enabling_repeat(self):
        mc = _mock_meshcore_with_info()
        mc.commands.set_radio = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"error_code": 6})
        )

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch.object(radio_manager, "repeat_supported", True),
            patch.object(radio_manager, "repeat_enabled", False),
            patch.object(radio_manager, "allowed_repeat_freqs", [(918.0, 918.0)]),
        ):
            with pytest.raises(HTTPException) as exc:
                await update_radio_config(RadioConfigUpdate(repeat_enabled=True))

        assert exc.value.status_code == 422
        assert "only allowed on: 918.000 MHz" in str(exc.value.detail)
        mc.commands.set_radio.assert_awaited_once_with(
            freq=910.525, bw=62.5, sf=7, cr=5, repeat=True
        )
        assert radio_manager.repeat_enabled is False
        mc.commands.send_appstart.assert_not_awaited()


class TestPrivateKeyExport:
    @pytest.mark.asyncio
    async def test_returns_403_when_export_disabled(self):
        with patch("app.config.settings") as mock_settings:
            mock_settings.enable_local_private_key_export = False
            with pytest.raises(HTTPException) as exc:
                await get_private_key()
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_404_when_no_key_available(self):
        with (
            patch("app.config.settings") as mock_settings,
            patch("app.keystore.get_private_key", return_value=None),
        ):
            mock_settings.enable_local_private_key_export = True
            with pytest.raises(HTTPException) as exc:
                await get_private_key()
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_key_hex_when_enabled_and_available(self):
        key_bytes = bytes.fromhex("ab" * 64)
        with (
            patch("app.config.settings") as mock_settings,
            patch("app.keystore.get_private_key", return_value=key_bytes),
        ):
            mock_settings.enable_local_private_key_export = True
            result = await get_private_key()
        assert result == {"private_key": "ab" * 64}


class TestPrivateKeyImport:
    @pytest.mark.asyncio
    async def test_rejects_invalid_hex(self):
        mc = _mock_meshcore_with_info()
        with patch("app.routers.radio.radio_manager.require_connected", return_value=mc):
            with pytest.raises(HTTPException) as exc:
                await set_private_key(PrivateKeyUpdate(private_key="not-hex"))

        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_500_on_radio_error(self):
        mc = _mock_meshcore_with_info()
        mc.commands.import_private_key = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"error": "failed"})
        )
        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await set_private_key(PrivateKeyUpdate(private_key="aa" * 64))

        assert exc.value.status_code == 422


class TestDiscoverMesh:
    @pytest.mark.asyncio
    async def test_discovers_repeaters_and_deduplicates_by_pubkey(self):
        mc = _mock_meshcore_with_info()
        callbacks = {}

        def _subscribe(event_type, callback, attribute_filters=None):
            callbacks["event_type"] = event_type
            callbacks["callback"] = callback
            callbacks["filters"] = attribute_filters
            subscription = MagicMock()
            subscription.unsubscribe = MagicMock()
            callbacks["subscription"] = subscription
            return subscription

        async def _send_node_discover_req(filter_bits, prefix_only=True, tag=None, since=None):
            assert filter_bits == (1 << 2)
            assert prefix_only is False
            assert since is None
            callbacks["callback"](
                _radio_result(
                    payload={
                        "pubkey": "11" * 32,
                        "node_type": 2,
                        "SNR": 7.5,
                        "RSSI": -101,
                        "SNR_in": 4.0,
                    }
                )
            )
            callbacks["callback"](
                _radio_result(
                    payload={
                        "pubkey": "11" * 32,
                        "node_type": 2,
                        "SNR": 9.0,
                        "RSSI": -99,
                        "SNR_in": 3.0,
                    }
                )
            )
            callbacks["callback"](
                _radio_result(
                    payload={
                        "pubkey": "22" * 32,
                        "node_type": 2,
                        "SNR": 2.5,
                        "RSSI": -110,
                        "SNR_in": 1.0,
                    }
                )
            )
            return _radio_result()

        mc.subscribe = MagicMock(side_effect=_subscribe)
        mc.commands.send_node_discover_req = AsyncMock(side_effect=_send_node_discover_req)

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.radio.DISCOVERY_WINDOW_SECONDS", 0.01),
            patch(
                "app.routers.radio.ContactRepository.get_by_key",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.routers.radio.ContactRepository.upsert", new_callable=AsyncMock),
            patch(
                "app.routers.radio.promote_prefix_contacts_for_contact",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.routers.radio.reconcile_contact_messages",
                new_callable=AsyncMock,
                return_value=(0, 0),
            ),
            patch("app.routers.radio.broadcast_event"),
        ):
            response = await discover_mesh(RadioDiscoveryRequest(target="repeaters"))

        assert response.target == "repeaters"
        assert len(response.results) == 2
        assert response.results[0].public_key == "11" * 32
        assert response.results[0].node_type == "repeater"
        assert response.results[0].heard_count == 2
        assert response.results[0].local_snr == 9.0
        assert response.results[0].local_rssi == -99
        assert response.results[0].remote_snr == 4.0
        assert callbacks["event_type"] == EventType.DISCOVER_RESPONSE
        assert callbacks["subscription"].unsubscribe.called
        mc.stop_auto_message_fetching.assert_awaited_once()
        mc.start_auto_message_fetching.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_persists_newly_discovered_nodes_and_broadcasts_contact_updates(self):
        mc = _mock_meshcore_with_info()
        created_contact = Contact(
            public_key="44" * 32,
            name=None,
            type=2,
            flags=0,
            direct_path=None,
            direct_path_len=-1,
            direct_path_hash_mode=-1,
            last_advert=None,
            lat=None,
            lon=None,
            last_seen=123,
            on_radio=False,
            last_contacted=None,
            last_read_at=None,
            first_seen=123,
        )

        def _subscribe(_event_type, callback, _attribute_filters=None):
            callback(
                _radio_result(
                    payload={
                        "pubkey": "44" * 32,
                        "node_type": 2,
                        "SNR": 6.0,
                        "RSSI": -100,
                        "SNR_in": 2.5,
                    }
                )
            )
            return MagicMock(unsubscribe=MagicMock())

        mc.subscribe = MagicMock(side_effect=_subscribe)

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.radio.DISCOVERY_WINDOW_SECONDS", 0.01),
            patch(
                "app.routers.radio.ContactRepository.get_by_key",
                new_callable=AsyncMock,
                # 1st: _persist check (not found), 2nd: _persist re-fetch (created),
                # 3rd: _attach_known_names lookup
                side_effect=[None, created_contact, created_contact],
            ) as mock_get_by_key,
            patch(
                "app.routers.radio.ContactRepository.upsert", new_callable=AsyncMock
            ) as mock_upsert,
            patch(
                "app.routers.radio.promote_prefix_contacts_for_contact",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_promote,
            patch(
                "app.routers.radio.reconcile_contact_messages",
                new_callable=AsyncMock,
                return_value=(0, 0),
            ),
            patch("app.routers.radio.broadcast_event") as mock_broadcast,
        ):
            response = await discover_mesh(RadioDiscoveryRequest(target="repeaters"))

        assert len(response.results) == 1
        assert response.results[0].name is None  # created_contact has no name
        mock_get_by_key.assert_awaited()
        mock_upsert.assert_awaited_once()
        mock_promote.assert_awaited_once_with(public_key="44" * 32, log=ANY)
        upsert_arg = mock_upsert.await_args.args[0]
        assert upsert_arg.public_key == "44" * 32
        assert upsert_arg.type == 2
        assert upsert_arg.on_radio is False
        mock_broadcast.assert_called_once_with("contact", created_contact.model_dump())

    @pytest.mark.asyncio
    async def test_does_not_reinsert_existing_discovered_nodes(self):
        mc = _mock_meshcore_with_info()
        existing_contact = Contact(
            public_key="55" * 32,
            name="Known",
            type=4,
            flags=0,
            direct_path=None,
            direct_path_len=-1,
            direct_path_hash_mode=-1,
            last_advert=None,
            lat=None,
            lon=None,
            last_seen=123,
            on_radio=False,
            last_contacted=None,
            last_read_at=None,
            first_seen=123,
        )

        def _subscribe(_event_type, callback, _attribute_filters=None):
            callback(
                _radio_result(
                    payload={
                        "pubkey": "55" * 32,
                        "node_type": 4,
                        "SNR": 5.0,
                        "RSSI": -102,
                        "SNR_in": 1.5,
                    }
                )
            )
            return MagicMock(unsubscribe=MagicMock())

        mc.subscribe = MagicMock(side_effect=_subscribe)

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.radio.DISCOVERY_WINDOW_SECONDS", 0.01),
            patch(
                "app.routers.radio.ContactRepository.get_by_key",
                new_callable=AsyncMock,
                return_value=existing_contact,
            ),
            patch(
                "app.routers.radio.ContactRepository.upsert", new_callable=AsyncMock
            ) as mock_upsert,
            patch("app.routers.radio.broadcast_event") as mock_broadcast,
        ):
            await discover_mesh(RadioDiscoveryRequest(target="sensors"))

        mock_upsert.assert_not_awaited()
        mock_broadcast.assert_not_called()


class TestTracePath:
    @pytest.mark.asyncio
    async def test_returns_resolved_nodes_for_multi_hop_trace(self):
        mc = _mock_meshcore_with_info()
        repeater_a = Contact(
            public_key="11" * 32,
            name="Relay Alpha",
            type=CONTACT_TYPE_REPEATER,
            flags=0,
            direct_path=None,
            direct_path_len=-1,
            direct_path_hash_mode=-1,
            last_advert=None,
            lat=None,
            lon=None,
            last_seen=None,
            on_radio=False,
            last_contacted=None,
            last_read_at=None,
            first_seen=None,
        )
        repeater_b = Contact(
            public_key="22" * 32,
            name="Relay Beta",
            type=CONTACT_TYPE_REPEATER,
            flags=0,
            direct_path=None,
            direct_path_len=-1,
            direct_path_hash_mode=-1,
            last_advert=None,
            lat=None,
            lon=None,
            last_seen=None,
            on_radio=False,
            last_contacted=None,
            last_read_at=None,
            first_seen=None,
        )
        mc.commands.send_trace = AsyncMock(
            return_value=_radio_result(EventType.MSG_SENT, {"suggested_timeout": 4000})
        )
        mc.wait_for_event = AsyncMock(
            return_value=MagicMock(
                payload={
                    "path_len": 2,
                    "path": [
                        {"hash": "11111111", "snr": 7.5},
                        {"hash": "22222222", "snr": 3.25},
                        {"snr": 5.0},
                    ],
                }
            )
        )

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.routers.radio.ContactRepository.get_by_key", new_callable=AsyncMock
            ) as mock_get,
            patch("app.routers.radio.radio_manager") as mock_rm,
        ):
            mock_get.side_effect = [repeater_a, repeater_b]
            mock_rm.radio_operation = _noop_radio_operation(mc)
            response = await trace_path(
                RadioTraceRequest(
                    hop_hash_bytes=4,
                    hops=[
                        RadioTraceHopRequest(public_key=repeater_a.public_key),
                        RadioTraceHopRequest(public_key=repeater_b.public_key),
                    ],
                )
            )

        mc.commands.send_trace.assert_awaited_once_with(
            path="11111111,22222222",
            tag=ANY,
            flags=2,
        )
        mc.wait_for_event.assert_awaited_once()
        assert response.path_len == 2
        assert response.nodes[0].name == "Relay Alpha"
        assert response.nodes[0].snr == 7.5
        assert response.nodes[1].name == "Relay Beta"
        assert response.nodes[1].observed_hash == "22222222"
        assert response.nodes[2].role == "local"
        assert response.nodes[2].public_key == "aa" * 32
        assert response.nodes[2].observed_hash is None
        assert response.nodes[2].snr == 5.0

    @pytest.mark.asyncio
    async def test_rejects_non_repeater_nodes(self):
        mc = _mock_meshcore_with_info()
        non_repeater = Contact(
            public_key="33" * 32,
            name="Client",
            type=1,
            flags=0,
            direct_path=None,
            direct_path_len=-1,
            direct_path_hash_mode=-1,
            last_advert=None,
            lat=None,
            lon=None,
            last_seen=None,
            on_radio=False,
            last_contacted=None,
            last_read_at=None,
            first_seen=None,
        )

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch(
                "app.routers.radio.ContactRepository.get_by_key", new_callable=AsyncMock
            ) as mock_get,
        ):
            mock_get.return_value = non_repeater
            with pytest.raises(HTTPException) as exc:
                await trace_path(
                    RadioTraceRequest(
                        hop_hash_bytes=4,
                        hops=[RadioTraceHopRequest(public_key=non_repeater.public_key)],
                    )
                )

        assert exc.value.status_code == 400
        assert "not a repeater" in exc.value.detail

    @pytest.mark.asyncio
    async def test_returns_408_when_no_trace_response_is_heard(self):
        mc = _mock_meshcore_with_info()
        repeater = Contact(
            public_key="44" * 32,
            name="Relay",
            type=CONTACT_TYPE_REPEATER,
            flags=0,
            direct_path=None,
            direct_path_len=-1,
            direct_path_hash_mode=-1,
            last_advert=None,
            lat=None,
            lon=None,
            last_seen=None,
            on_radio=False,
            last_contacted=None,
            last_read_at=None,
            first_seen=None,
        )
        mc.commands.send_trace = AsyncMock(
            return_value=_radio_result(EventType.MSG_SENT, {"suggested_timeout": 1000})
        )
        mc.wait_for_event = AsyncMock(return_value=None)

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.routers.radio.ContactRepository.get_by_key", new_callable=AsyncMock
            ) as mock_get,
            patch("app.routers.radio.radio_manager") as mock_rm,
        ):
            mock_get.return_value = repeater
            mock_rm.radio_operation = _noop_radio_operation(mc)
            with pytest.raises(HTTPException) as exc:
                await trace_path(
                    RadioTraceRequest(
                        hop_hash_bytes=4,
                        hops=[RadioTraceHopRequest(public_key=repeater.public_key)],
                    )
                )

        assert exc.value.status_code == 408
        assert "No trace response heard" in exc.value.detail

    @pytest.mark.asyncio
    async def test_supports_custom_hops_with_shorter_hash_width(self):
        mc = _mock_meshcore_with_info()
        mc.commands.send_trace = AsyncMock(
            return_value=_radio_result(EventType.MSG_SENT, {"suggested_timeout": 2500})
        )
        mc.wait_for_event = AsyncMock(
            return_value=MagicMock(
                payload={
                    "path_len": 2,
                    "path": [
                        {"hash": "ae", "snr": 4.0},
                        {"hash": "bf", "snr": 2.5},
                        {"snr": 3.0},
                    ],
                }
            )
        )

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.radio.radio_manager") as mock_rm,
        ):
            mock_rm.radio_operation = _noop_radio_operation(mc)
            response = await trace_path(
                RadioTraceRequest(
                    hop_hash_bytes=1,
                    hops=[
                        RadioTraceHopRequest(hop_hex="ae"),
                        RadioTraceHopRequest(hop_hex="bf"),
                    ],
                )
            )

        mc.commands.send_trace.assert_awaited_once_with(path="ae,bf", tag=ANY, flags=0)
        assert response.nodes[0].role == "custom"
        assert response.nodes[0].observed_hash == "ae"
        assert response.nodes[1].role == "custom"
        assert response.nodes[1].observed_hash == "bf"

    @pytest.mark.asyncio
    async def test_discovers_all_supported_types(self):
        mc = _mock_meshcore_with_info()

        def _subscribe(_event_type, callback, _attribute_filters=None):
            callback(
                _radio_result(
                    payload={
                        "pubkey": "33" * 32,
                        "node_type": 4,
                        "SNR": 5.0,
                        "RSSI": -100,
                        "SNR_in": 2.0,
                    }
                )
            )
            subscription = MagicMock()
            subscription.unsubscribe = MagicMock()
            return subscription

        mc.subscribe = MagicMock(side_effect=_subscribe)

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.radio.DISCOVERY_WINDOW_SECONDS", 0.01),
            patch(
                "app.routers.radio.ContactRepository.get_by_key",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.routers.radio.ContactRepository.upsert", new_callable=AsyncMock),
            patch(
                "app.routers.radio.promote_prefix_contacts_for_contact",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.routers.radio.reconcile_contact_messages",
                new_callable=AsyncMock,
                return_value=(0, 0),
            ),
            patch("app.routers.radio.broadcast_event"),
        ):
            response = await discover_mesh(RadioDiscoveryRequest(target="all"))

        mc.commands.send_node_discover_req.assert_awaited_once()
        assert mc.commands.send_node_discover_req.await_args.args[0] == (1 << 2) | (1 << 4)
        assert response.results[0].node_type == "sensor"

    @pytest.mark.asyncio
    async def test_raises_when_discovery_request_fails(self):
        mc = _mock_meshcore_with_info()
        mc.subscribe = MagicMock(return_value=MagicMock(unsubscribe=MagicMock()))
        mc.commands.send_node_discover_req = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"error": "nope"})
        )

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await discover_mesh(RadioDiscoveryRequest(target="sensors"))

        assert exc.value.status_code == 422
        assert exc.value.detail == "Failed to start mesh discovery"

    @pytest.mark.asyncio
    async def test_successful_import_refreshes_keystore(self):
        mc = _mock_meshcore_with_info()
        mc.commands.import_private_key = AsyncMock(return_value=_radio_result())
        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.keystore.export_and_store_private_key",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_export,
        ):
            result = await set_private_key(PrivateKeyUpdate(private_key="aa" * 64))

        assert result == {"status": "ok"}
        mock_export.assert_awaited_once_with(mc)

    @pytest.mark.asyncio
    async def test_import_ok_but_keystore_refresh_fails_returns_500(self):
        mc = _mock_meshcore_with_info()
        mc.commands.import_private_key = AsyncMock(return_value=_radio_result())
        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.keystore.export_and_store_private_key",
                new_callable=AsyncMock,
                return_value=False,
            ) as mock_export,
        ):
            with pytest.raises(HTTPException) as exc:
                await set_private_key(PrivateKeyUpdate(private_key="aa" * 64))

        assert exc.value.status_code == 422
        assert "keystore" in exc.value.detail.lower()
        # Called twice: initial attempt + one retry
        assert mock_export.await_count == 2

    @pytest.mark.asyncio
    async def test_keystore_refresh_succeeds_on_retry(self):
        mc = _mock_meshcore_with_info()
        mc.commands.import_private_key = AsyncMock(return_value=_radio_result())
        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.keystore.export_and_store_private_key",
                new_callable=AsyncMock,
                side_effect=[False, True],
            ) as mock_export,
        ):
            result = await set_private_key(PrivateKeyUpdate(private_key="aa" * 64))

        assert result == {"status": "ok"}
        assert mock_export.await_count == 2


class TestAdvertise:
    @pytest.mark.asyncio
    async def test_raises_when_send_fails(self):
        radio_manager._meshcore = MagicMock()
        with (
            patch("app.routers.radio.radio_manager.require_connected"),
            patch(
                "app.routers.radio.do_send_advertisement",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await send_advertisement()

        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_defaults_to_flood_mode(self):
        radio_manager._meshcore = MagicMock()
        with (
            patch("app.routers.radio.radio_manager.require_connected"),
            patch(
                "app.routers.radio.do_send_advertisement",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_send,
        ):
            result = await send_advertisement()

        assert result == {"status": "ok"}
        mock_send.assert_awaited_once()
        assert mock_send.await_args.kwargs["force"] is True
        assert mock_send.await_args.kwargs["mode"] == "flood"

    @pytest.mark.asyncio
    async def test_accepts_zero_hop_mode(self):
        radio_manager._meshcore = MagicMock()
        with (
            patch("app.routers.radio.radio_manager.require_connected"),
            patch(
                "app.routers.radio.do_send_advertisement",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_send,
        ):
            result = await send_advertisement(RadioAdvertiseRequest(mode="zero_hop"))

        assert result == {"status": "ok"}
        mock_send.assert_awaited_once()
        assert mock_send.await_args.kwargs["force"] is True
        assert mock_send.await_args.kwargs["mode"] == "zero_hop"

    @pytest.mark.asyncio
    async def test_concurrent_advertise_calls_are_serialized(self):
        active = 0
        max_active = 0

        async def fake_send(mc, *, force: bool, mode: str):
            nonlocal active, max_active
            assert force is True
            assert mode == "flood"
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            active -= 1
            return True

        isolated_manager = RadioManager()
        isolated_manager._meshcore = MagicMock()
        with (
            patch("app.routers.radio.radio_manager.require_connected"),
            patch("app.routers.radio.radio_manager", _runtime(isolated_manager)),
            patch(
                "app.routers.radio.do_send_advertisement",
                new_callable=AsyncMock,
                side_effect=fake_send,
            ),
        ):
            await asyncio.gather(send_advertisement(), send_advertisement())

        assert max_active == 1


class TestRebootAndReconnect:
    @pytest.mark.asyncio
    async def test_reboot_connected_sends_reboot_command(self):
        mock_mc = MagicMock()
        mock_mc.commands.reboot = AsyncMock()

        mock_rm = MagicMock()
        mock_rm.is_connected = True
        mock_rm.meshcore = mock_mc
        mock_rm.radio_operation = _noop_radio_operation(mock_mc)

        with patch("app.routers.radio.radio_manager", _runtime(mock_rm)):
            result = await reboot_radio()

        assert result["status"] == "ok"
        mock_mc.commands.reboot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reboot_returns_pending_when_reconnect_in_progress(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = False
        mock_rm.meshcore = None
        mock_rm.is_reconnecting = True
        mock_rm.radio_operation = _noop_radio_operation()

        with patch("app.routers.radio.radio_manager", _runtime(mock_rm)):
            result = await reboot_radio()

        assert result["status"] == "pending"
        assert result["connected"] is False

    @pytest.mark.asyncio
    async def test_reboot_attempts_reconnect_when_disconnected(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = False
        mock_rm.meshcore = None
        mock_rm.is_reconnecting = False
        mock_rm.reconnect = AsyncMock(return_value=True)
        mock_rm.post_connect_setup = AsyncMock()
        mock_rm.radio_operation = _noop_radio_operation()
        mock_rm.connection_info = "TCP: test:4000"

        with patch("app.routers.radio.radio_manager", _runtime(mock_rm)):
            result = await reboot_radio()

        assert result["status"] == "ok"
        assert result["connected"] is True
        mock_rm.reconnect.assert_awaited_once()
        mock_rm.post_connect_setup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconnect_returns_already_connected(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = True
        mock_rm.radio_operation = _noop_radio_operation()
        mock_rm.is_setup_complete = True

        with patch("app.routers.radio.radio_manager", _runtime(mock_rm)):
            result = await reconnect_radio()

        assert result["status"] == "ok"
        assert result["connected"] is True

    @pytest.mark.asyncio
    async def test_reconnect_raises_423_on_failure(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = False
        mock_rm.is_reconnecting = False
        mock_rm.reconnect = AsyncMock(return_value=False)
        mock_rm.radio_operation = _noop_radio_operation()

        with patch("app.routers.radio.radio_manager", _runtime(mock_rm)):
            with pytest.raises(HTTPException) as exc:
                await reconnect_radio()

        assert exc.value.status_code == 423

    @pytest.mark.asyncio
    async def test_disconnect_pauses_connection_attempts_and_broadcasts_health(self):
        mock_rm = MagicMock()
        mock_rm.pause_connection = AsyncMock()
        mock_rm.connection_info = "BLE: AA:BB:CC:DD:EE:FF"

        with (
            patch("app.routers.radio.radio_manager", _runtime(mock_rm)),
            patch("app.routers.radio.broadcast_health") as mock_broadcast,
        ):
            result = await disconnect_radio()

        assert result["status"] == "ok"
        assert result["connected"] is False
        assert result["paused"] is True
        mock_rm.pause_connection.assert_awaited_once()
        mock_broadcast.assert_called_once_with(False, "BLE: AA:BB:CC:DD:EE:FF")


def _repeater(public_key: str, name: str | None = "RPT") -> Contact:
    return Contact(public_key=public_key, name=name, type=2)


class TestDedupeRegionNames:
    def test_drops_wildcard_blanks_and_case_insensitive_dupes(self):
        assert _dedupe_region_names(["*", "us", "US", "", "  ", "ca", "Us"]) == ["us", "ca"]

    def test_empty(self):
        assert _dedupe_region_names([]) == []


class TestDiscoverRegions:
    @pytest.mark.asyncio
    async def test_sweeps_explicit_repeaters_and_aggregates_union(self):
        mc = _mock_meshcore_with_info()
        key_a, key_b = "aa" * 32, "bb" * 32
        contacts = {key_a: _repeater(key_a, "Alpha"), key_b: _repeater(key_b, "Bravo")}
        region_map = {key_a: ["us", "ca"], key_b: ["ca", "de"]}

        async def _anon(_mc, contact):
            return region_map[contact.public_key]

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.routers.radio.ContactRepository.get_by_key",
                new_callable=AsyncMock,
                side_effect=lambda key: contacts.get(key),
            ),
            patch("app.routers.radio.request_anon_region_names", side_effect=_anon),
        ):
            response = await discover_regions(
                RadioRegionDiscoveryRequest(public_keys=[key_a, key_b])
            )

        assert response.repeaters_queried == 2
        assert response.repeaters_answered == 2
        assert response.regions == ["us", "ca", "de"]
        assert [(r.public_key, r.name, r.answered, r.regions) for r in response.results] == [
            (key_a, "Alpha", True, ["us", "ca"]),
            (key_b, "Bravo", True, ["ca", "de"]),
        ]
        # One radio_operation held for the whole sweep -> auto-fetch toggled once.
        mc.stop_auto_message_fetching.assert_awaited_once()
        mc.start_auto_message_fetching.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unreachable_repeater_is_reported_and_excluded_from_union(self):
        mc = _mock_meshcore_with_info()
        key_a, key_b = "aa" * 32, "bb" * 32
        contacts = {key_a: _repeater(key_a), key_b: _repeater(key_b)}

        async def _anon(_mc, contact):
            return ["us"] if contact.public_key == key_a else None

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.routers.radio.ContactRepository.get_by_key",
                new_callable=AsyncMock,
                side_effect=lambda key: contacts.get(key),
            ),
            patch("app.routers.radio.request_anon_region_names", side_effect=_anon),
        ):
            response = await discover_regions(
                RadioRegionDiscoveryRequest(public_keys=[key_a, key_b])
            )

        assert response.repeaters_queried == 2
        assert response.repeaters_answered == 1
        assert response.regions == ["us"]
        assert response.results[1].answered is False
        assert response.results[1].regions == []

    @pytest.mark.asyncio
    async def test_wildcard_and_dupes_stripped_per_repeater(self):
        mc = _mock_meshcore_with_info()
        key_a = "aa" * 32

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.routers.radio.ContactRepository.get_by_key",
                new_callable=AsyncMock,
                return_value=_repeater(key_a),
            ),
            patch(
                "app.routers.radio.request_anon_region_names",
                new=AsyncMock(return_value=["*", "US", "us", "ca"]),
            ),
        ):
            response = await discover_regions(RadioRegionDiscoveryRequest(public_keys=[key_a]))

        assert response.results[0].regions == ["US", "ca"]
        assert response.regions == ["US", "ca"]

    @pytest.mark.asyncio
    async def test_auto_targets_recent_repeaters_when_no_keys(self):
        mc = _mock_meshcore_with_info()
        key_a = "aa" * 32
        recent = AsyncMock(return_value=[_repeater(key_a, "Recent")])

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.radio.ContactRepository.get_repeaters_by_recent", new=recent),
            patch(
                "app.routers.radio.request_anon_region_names",
                new=AsyncMock(return_value=["eu"]),
            ),
        ):
            response = await discover_regions(RadioRegionDiscoveryRequest(max_repeaters=5))

        recent.assert_awaited_once_with(limit=5)
        assert response.repeaters_queried == 1
        assert response.regions == ["eu"]

    @pytest.mark.asyncio
    async def test_no_targets_returns_empty_without_touching_radio(self):
        mc = _mock_meshcore_with_info()
        anon = AsyncMock(return_value=["us"])

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.routers.radio.ContactRepository.get_repeaters_by_recent",
                new=AsyncMock(return_value=[]),
            ),
            patch("app.routers.radio.request_anon_region_names", new=anon),
        ):
            response = await discover_regions(RadioRegionDiscoveryRequest())

        assert response.repeaters_queried == 0
        assert response.repeaters_answered == 0
        assert response.regions == []
        assert response.results == []
        anon.assert_not_awaited()
        # No radio_operation should have been entered.
        mc.stop_auto_message_fetching.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_repeater_keys_are_filtered_out(self):
        mc = _mock_meshcore_with_info()
        key_a = "aa" * 32
        client = Contact(public_key=key_a, name="Client", type=1)

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.routers.radio.ContactRepository.get_by_key",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch(
                "app.routers.radio.request_anon_region_names",
                new=AsyncMock(return_value=["us"]),
            ),
        ):
            response = await discover_regions(RadioRegionDiscoveryRequest(public_keys=[key_a]))

        assert response.repeaters_queried == 0
        assert response.results == []

    @pytest.mark.asyncio
    async def test_explicit_keys_capped_at_max_repeaters(self):
        mc = _mock_meshcore_with_info()
        keys = ["aa" * 32, "bb" * 32, "cc" * 32]
        contacts = {k: _repeater(k) for k in keys}
        anon = AsyncMock(return_value=["us"])

        with (
            patch("app.routers.radio.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.routers.radio.ContactRepository.get_by_key",
                new_callable=AsyncMock,
                side_effect=lambda key: contacts.get(key),
            ),
            patch("app.routers.radio.request_anon_region_names", new=anon),
        ):
            response = await discover_regions(
                RadioRegionDiscoveryRequest(public_keys=keys, max_repeaters=2)
            )

        assert response.repeaters_queried == 2
        assert anon.await_count == 2
