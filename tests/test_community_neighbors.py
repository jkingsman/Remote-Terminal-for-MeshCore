"""Focused contract tests for Community MQTT neighbor reporting."""

from __future__ import annotations

import hmac
import json
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from Crypto.Cipher import AES
from fastapi import HTTPException

from app.decoder import PayloadType, derive_public_key, derive_shared_secret
from app.fanout.community_neighbors import (
    CommunityNeighborReporter,
    ScopeQueryEntry,
    _format_snapshot_timestamp,
    normalize_self_scopes,
)
from app.fanout.mqtt_community import (
    _DEFAULT_NEIGHBOR_TOPIC_TEMPLATE as DEFAULT_NEIGHBOR_TOPIC,
)
from app.fanout.mqtt_community import (
    MqttCommunityModule,
    _render_neighbor_topic,
)

# These are fixed valid MeshCore-format Ed25519 key materials from the packet
# pipeline tests.  They keep response-overlay encryption deterministic.
OUR_PRIVATE_KEY = bytes.fromhex(
    "58BA1940E97099CBB4357C62CE9C7F4B245C94C90D722E67201B989F9FEACF7B"
    "77ACADDB84438514022BDB0FC3140C2501859BE1772AC7B8C7E41DC0F40490A1"
)
PEER_PUBLIC_KEY = bytes.fromhex("A1B2C3D3BA9F5FA8705B9845FE11CC6F01D1D49CAAF4D122AC7121663C5BEEC7")
OUR_PUBLIC_KEY = derive_public_key(OUR_PRIVATE_KEY)


class _Module:
    def __init__(self, config_id: str, config: dict, *, connected: bool = True) -> None:
        self.config_id = config_id
        self.config = config
        self.neighbor_publisher_connected = connected
        self.published: list[str] = []

    async def publish_neighbor_snapshot(self, serialized_snapshot: str) -> bool:
        self.published.append(serialized_snapshot)
        return True


def _response_packet(*, tag: bytes, scopes: bytes) -> bytes:
    """Build one authenticated direct PAYLOAD_TYPE_RESPONSE packet."""
    plaintext = tag + (1_700_000_000).to_bytes(4, "little") + scopes
    plaintext += bytes((-len(plaintext)) % 16)
    secret = derive_shared_secret(OUR_PRIVATE_KEY, PEER_PUBLIC_KEY)
    ciphertext = AES.new(secret[:16], AES.MODE_ECB).encrypt(plaintext)
    mac = hmac.new(secret, ciphertext, sha256).digest()[:2]
    payload = bytes((OUR_PUBLIC_KEY[0], PEER_PUBLIC_KEY[0])) + mac + ciphertext
    # Header: direct route + response payload type, followed by zero hops.
    return bytes(((int(PayloadType.RESPONSE) << 2) | 0x02, 0)) + payload


class TestNeighborCache:
    @pytest.mark.asyncio
    async def test_passive_direct_signed_repeater_is_cached(self):
        reporter = CommunityNeighborReporter()
        peer = "11" * 32
        envelope = SimpleNamespace(
            payload_type=int(PayloadType.ADVERT),
            hop_count=0,
            transport_codes=None,
            payload=b"advert",
        )
        advert = SimpleNamespace(public_key=peer, timestamp=123, device_role=2)

        with (
            patch("app.fanout.community_neighbors.parse_packet_envelope", return_value=envelope),
            patch("app.fanout.community_neighbors.parse_advertisement", return_value=advert),
            patch("app.fanout.community_neighbors.verify_advert_signature", return_value=True),
            patch("app.keystore.get_public_key", return_value=None),
        ):
            await reporter.observe_packet(b"raw", timestamp=456, measured_snr=2.62)

        cached = reporter._cache[peer]
        assert cached.advert_timestamp == 123
        assert cached.heard_timestamp == 456
        assert cached.snr_q4 == 10

    @pytest.mark.asyncio
    async def test_share_advert_is_not_a_neighbor(self):
        reporter = CommunityNeighborReporter()
        envelope = SimpleNamespace(
            payload_type=int(PayloadType.ADVERT),
            hop_count=0,
            transport_codes=(0, 0),
            payload=b"advert",
        )
        with patch("app.fanout.community_neighbors.parse_packet_envelope", return_value=envelope):
            await reporter.observe_packet(b"raw", timestamp=456, measured_snr=2.5)

        assert reporter._cache == {}

    @pytest.mark.asyncio
    async def test_oldest_heard_entry_is_evicted_at_capacity(self):
        reporter = CommunityNeighborReporter(cache_limit=2)
        await reporter.put_neighbor(
            "01" * 32, advert_timestamp=1, measured_snr=1, heard_timestamp=10
        )
        await reporter.put_neighbor(
            "02" * 32, advert_timestamp=2, measured_snr=2, heard_timestamp=20
        )
        await reporter.put_neighbor(
            "03" * 32, advert_timestamp=3, measured_snr=3, heard_timestamp=30
        )

        assert set(reporter._cache) == {"02" * 32, "03" * 32}


class TestScopeResponseOverlay:
    @pytest.mark.asyncio
    async def test_authenticated_raw_response_completes_matching_non_contact_peer(self):
        reporter = CommunityNeighborReporter()
        tag = bytes.fromhex("A1B2C3D4")
        entry = ScopeQueryEntry(
            public_key=PEER_PUBLIC_KEY.hex(),
            heard_timestamp=100,
            snr_q4=8,
            request_tag=tag.hex().lower(),
        )
        reporter._scope_active = True
        reporter._scope_entries = [entry]
        reporter._queries_by_tag[entry.request_tag] = entry

        with (
            patch("app.keystore.get_private_key", return_value=OUR_PRIVATE_KEY),
            patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY),
        ):
            await reporter.observe_packet(_response_packet(tag=tag, scopes=b"*,Europe\0"))

        assert entry.status == "responded"
        assert entry.scopes == "*,Europe"

    @pytest.mark.asyncio
    async def test_wrong_tag_does_not_complete_overlay_entry(self):
        reporter = CommunityNeighborReporter()
        entry = ScopeQueryEntry(
            public_key=PEER_PUBLIC_KEY.hex(),
            heard_timestamp=100,
            snr_q4=8,
            request_tag="01020304",
        )
        reporter._scope_active = True
        reporter._scope_entries = [entry]
        reporter._queries_by_tag[entry.request_tag] = entry

        with (
            patch("app.keystore.get_private_key", return_value=OUR_PRIVATE_KEY),
            patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY),
        ):
            await reporter.observe_packet(
                _response_packet(tag=bytes.fromhex("05060708"), scopes=b"X")
            )

        assert entry.status == "pending"
        assert entry.scopes == ""

    @pytest.mark.asyncio
    async def test_regions_command_uses_full_identity_and_zero_hop_reply_path(self):
        reporter = CommunityNeighborReporter()
        mc = MagicMock()
        mc.commands.send = AsyncMock(return_value=SimpleNamespace())

        await reporter._send_regions_request(mc, PEER_PUBLIC_KEY.hex())

        command = mc.commands.send.await_args.args[0]
        assert command == b"\x39" + PEER_PUBLIC_KEY + b"\x01\x00"


class TestSnapshotContract:
    def test_timestamp_uses_six_digits_and_explicit_utc_offset(self):
        assert _format_snapshot_timestamp(1_700_000_000).endswith(".000000+00:00")

    def test_self_scope_normalization_preserves_wildcard_and_dollar(self):
        assert normalize_self_scopes(" #*, #$private, Sweden ") == "*,$private,Sweden"

    def test_snapshot_orders_by_age_then_snr_then_key(self, monkeypatch):
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module(
            "a",
            {"neighbor_origin": "' Observer '", "neighbor_self_scopes": "#*, #Sweden"},
        )
        entries = [
            ScopeQueryEntry("CC" * 32, heard_timestamp=90, snr_q4=4, status="timeout"),
            ScopeQueryEntry("BB" * 32, heard_timestamp=95, snr_q4=4, status="send_failed"),
            ScopeQueryEntry(
                "AA" * 32, heard_timestamp=95, snr_q4=12, scopes="Europe", status="responded"
            ),
        ]
        monkeypatch.setattr("app.fanout.community_neighbors._wall_time", lambda: 100)

        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            serialized = reporter._build_snapshot(entries, {"a"})

        assert serialized is not None
        snapshot = json.loads(serialized)
        assert snapshot["origin"] == "Observer"
        assert snapshot["origin_id"] == OUR_PUBLIC_KEY.hex().upper()
        assert snapshot["self"] == {"scopes": "*,Sweden"}
        assert [neighbor["pubkey"] for neighbor in snapshot["neighbors"]] == [
            "AA" * 32,
            "BB" * 32,
            "CC" * 32,
        ]

    def test_snapshot_never_includes_observer_identity(self, monkeypatch):
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module("a", {})
        monkeypatch.setattr("app.fanout.community_neighbors._wall_time", lambda: 100)

        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            serialized = reporter._build_snapshot(
                [
                    ScopeQueryEntry(
                        OUR_PUBLIC_KEY.hex(),
                        heard_timestamp=90,
                        snr_q4=4,
                        status="timeout",
                    )
                ],
                {"a"},
            )

        assert serialized is not None
        assert json.loads(serialized)["neighbors"] == []

    def test_snapshot_buffer_boundary_is_not_accepted(self, monkeypatch):
        reporter = CommunityNeighborReporter()
        payload = {"x": "a"}
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        monkeypatch.setattr("app.fanout.community_neighbors.MAX_SNAPSHOT_BYTES", len(serialized))

        assert reporter._serialize_snapshot(payload) is None

    def test_due_status_distinguishes_waiting_for_a_broker(self):
        reporter = CommunityNeighborReporter()
        reporter._next_periodic_at = 0

        assert reporter.status_for("missing")["phase"] == "due"

    def test_status_reports_serialized_publish_handoff(self):
        reporter = CommunityNeighborReporter()
        reporter._publishing_snapshot = True

        assert reporter.status_for("missing")["phase"] == "publishing"


class TestSnapshotHandoff:
    @pytest.mark.asyncio
    async def test_manual_snapshot_does_not_overtake_an_in_flight_publish(self):
        reporter = CommunityNeighborReporter()
        reporter._modules["one"] = _Module("one", {}, connected=True)
        reporter._publishing_snapshot = True

        result = await reporter.start_manual_snapshot("one")

        assert result["status"] == "active"
        assert "still publishing" in result["message"]

    def test_reconnecting_slot_stays_eligible_until_publication(self):
        reporter = CommunityNeighborReporter()
        reporter._modules["connected"] = _Module("connected", {}, connected=True)
        reporter._modules["reconnecting"] = _Module("reconnecting", {}, connected=False)

        assert reporter._eligible_publish_target_ids(
            {"connected", "reconnecting"}, include_manual=True
        ) == {"connected", "reconnecting"}


class TestMqttNeighborDelivery:
    @pytest.mark.asyncio
    async def test_module_publishes_snapshot_qos_one_to_neighbor_topic(self):
        module = MqttCommunityModule(
            "one",
            {
                "iata": "STO",
                "neighbor_topic_template": DEFAULT_NEIGHBOR_TOPIC,
                "neighbor_retain": True,
            },
        )
        module._publisher.connected = True
        module._publisher._settings = SimpleNamespace()
        module._publisher.publish = AsyncMock(return_value=True)

        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            assert await module.publish_neighbor_snapshot('{"neighbors":[]}')

        module._publisher.publish.assert_awaited_once_with(
            f"meshcore/STO/{OUR_PUBLIC_KEY.hex().upper()}/neighbors",
            '{"neighbors":[]}',
            retain=True,
            qos=1,
        )

    def test_neighbor_template_renders_type_placeholder(self):
        assert (
            _render_neighbor_topic("mesh/{iata}/{device}/{type}", iata="STO", public_key="AB" * 32)
            == f"mesh/STO/{'AB' * 32}/neighbors"
        )

    def test_neighbor_template_requires_neighbors_suffix(self):
        with pytest.raises(ValueError, match="must end with /neighbors or /\\{TYPE\\}"):
            _render_neighbor_topic("mesh/{iata}/{device}/packets", iata="STO", public_key="AB" * 32)


class TestNeighborConfigValidation:
    def test_defaults_are_persisted_for_existing_community_configs(self):
        from app.routers.fanout import _validate_mqtt_community_config

        config = {"iata": "sto"}
        _validate_mqtt_community_config(config)

        assert config["neighbor_reporting_enabled"] is False
        assert config["neighbor_reporting_interval_hours"] == 24
        assert config["neighbor_topic_template"] == DEFAULT_NEIGHBOR_TOPIC
        assert config["neighbor_retain"] is False

    def test_interval_range_is_enforced(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException, match="between 12 and 336"):
            _validate_mqtt_community_config(
                {"iata": "STO", "neighbor_reporting_interval_hours": 11}
            )

    def test_neighbor_topic_must_be_a_string(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException, match="neighbor_topic_template must be a string"):
            _validate_mqtt_community_config({"iata": "STO", "neighbor_topic_template": 42})

    def test_neighbor_topic_must_keep_the_neighbors_suffix(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException, match="must end with /neighbors or /\\{TYPE\\}"):
            _validate_mqtt_community_config(
                {"iata": "STO", "neighbor_topic_template": "mesh/{IATA}/{PUBLIC_KEY}/packets"}
            )
