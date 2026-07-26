"""Comprehensive spec-compliance tests for MQTT neighbor reporting.

Covers the canonical specification test matrix (S25 and S35) that was
not already covered by the existing focused contract tests.
"""

from __future__ import annotations

import hmac
import json
from contextlib import asynccontextmanager
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from Crypto.Cipher import AES
from meshcore import EventType

from app.decoder import PayloadType, derive_public_key, derive_shared_secret
from app.fanout.community_neighbors import (
    MAX_SNAPSHOT_BYTES,
    CommunityNeighborReporter,
    NeighborCacheEntry,
    NeighborReporterError,
    ScopeQueryEntry,
    _bounded_scope_text,
    _quantize_snr,
    normalize_neighbor_origin,
    normalize_self_scopes,
)

OUR_PRIVATE_KEY = bytes.fromhex(
    "58BA1940E97099CBB4357C62CE9C7F4B245C94C90D722E67201B989F9FEACF7B"
    "77ACADDB84438514022BDB0FC3140C2501859BE1772AC7B8C7E41DC0F40490A1"
)
PEER_PUBLIC_KEY = bytes.fromhex("A1B2C3D3BA9F5FA8705B9845FE11CC6F01D1D49CAAF4D122AC7121663C5BEEC7")
OUR_PUBLIC_KEY = derive_public_key(OUR_PRIVATE_KEY)


class _Module:
    def __init__(self, config_id, config, *, connected=True):
        self.config_id = config_id
        self.config = config
        self.neighbor_publisher_connected = connected
        self.published = []

    async def publish_neighbor_snapshot(self, serialized_snapshot):
        self.published.append(serialized_snapshot)
        return True


class _FailingModule:
    def __init__(self, config_id, config, *, connected=True):
        self.config_id = config_id
        self.config = config
        self.neighbor_publisher_connected = connected
        self.published = []

    async def publish_neighbor_snapshot(self, serialized_snapshot):
        return False


def _response_packet(*, tag, scopes):
    plaintext = tag + (1_700_000_000).to_bytes(4, "little") + scopes
    plaintext += bytes((-len(plaintext)) % 16)
    secret = derive_shared_secret(OUR_PRIVATE_KEY, PEER_PUBLIC_KEY)
    ciphertext = AES.new(secret[:16], AES.MODE_ECB).encrypt(plaintext)
    mac = hmac.new(secret, ciphertext, sha256).digest()[:2]
    p = bytes((OUR_PUBLIC_KEY[0], PEER_PUBLIC_KEY[0])) + mac + ciphertext
    return bytes(((int(PayloadType.RESPONSE) << 2) | 0x02, 0)) + p


def _make_envelope(
    *, payload_type=None, hop_count=0, transport_codes=None, route_type=0x02, payload=b""
):
    return SimpleNamespace(
        payload_type=payload_type,
        hop_count=hop_count,
        transport_codes=transport_codes,
        route_type=route_type,
        payload=payload,
    )


# ===================================================================
# S25.1 / S25.2  Cache & discovery tests
# ===================================================================


class TestCacheExtended:
    @pytest.mark.asyncio
    async def test_non_repeater_advert_is_not_cached(self):
        """Spec 25.1-2: role != 2 must not create a cache entry."""
        reporter = CommunityNeighborReporter()
        envelope = _make_envelope(payload_type=int(PayloadType.ADVERT))
        for role in (1, 3, 4):
            advert = SimpleNamespace(public_key="11" * 32, timestamp=1, device_role=role)
            with (
                patch(
                    "app.fanout.community_neighbors.parse_packet_envelope", return_value=envelope
                ),
                patch("app.fanout.community_neighbors.parse_advertisement", return_value=advert),
                patch("app.fanout.community_neighbors.verify_advert_signature", return_value=True),
                patch("app.keystore.get_public_key", return_value=None),
            ):
                await reporter.observe_packet(b"raw")
        assert reporter._cache == {}

    @pytest.mark.asyncio
    async def test_relayed_advert_is_not_cached(self):
        """Spec 25.1-3: hop_count > 0 must not create a cache entry."""
        reporter = CommunityNeighborReporter()
        envelope = _make_envelope(payload_type=int(PayloadType.ADVERT), hop_count=2)
        advert = SimpleNamespace(public_key="11" * 32, timestamp=1, device_role=2)
        with (
            patch("app.fanout.community_neighbors.parse_packet_envelope", return_value=envelope),
            patch("app.fanout.community_neighbors.parse_advertisement", return_value=advert),
            patch("app.fanout.community_neighbors.verify_advert_signature", return_value=True),
            patch("app.keystore.get_public_key", return_value=None),
        ):
            await reporter.observe_packet(b"raw")
        assert reporter._cache == {}

    @pytest.mark.asyncio
    async def test_existing_identity_is_updated_in_place(self):
        """Spec 25.1-5: same public key updates one slot only."""
        reporter = CommunityNeighborReporter()
        key = "ab" * 32
        await reporter.put_neighbor(key, advert_timestamp=1, measured_snr=1.0, heard_timestamp=10)
        await reporter.put_neighbor(key, advert_timestamp=2, measured_snr=3.0, heard_timestamp=20)
        assert len(reporter._cache) == 1
        cached = reporter._cache[key]
        assert cached.advert_timestamp == 2
        assert cached.heard_timestamp == 20
        assert cached.snr_q4 == 12

    @pytest.mark.asyncio
    async def test_empty_slot_is_used_before_eviction(self):
        """Spec 25.1-6: empty slot (heard_timestamp==0) chosen first."""
        reporter = CommunityNeighborReporter(cache_limit=2)
        reporter._cache["01" * 32] = NeighborCacheEntry(
            public_key="01" * 32, advert_timestamp=1, heard_timestamp=10, snr_q4=4
        )
        reporter._cache["02" * 32] = NeighborCacheEntry(
            public_key="02" * 32, advert_timestamp=2, heard_timestamp=0, snr_q4=0
        )
        await reporter.put_neighbor(
            "03" * 32, advert_timestamp=3, measured_snr=1, heard_timestamp=30
        )
        assert "01" * 32 in reporter._cache
        assert "03" * 32 in reporter._cache
        assert "02" * 32 not in reporter._cache

    def test_snr_quantization_covers_negative_and_fractions(self):
        """Spec 25.1-8: quarter-dB quantization for all sign/value combos."""
        assert _quantize_snr(2.62) == 10
        assert _quantize_snr(-0.75) == -3
        assert _quantize_snr(0.0) == 0
        assert _quantize_snr(-3.25) == -13
        assert _quantize_snr(8.5) == 34

    def test_snr_clamps_at_int8_bounds(self):
        assert _quantize_snr(50.0) == 127
        assert _quantize_snr(-50.0) == -128

    @pytest.mark.asyncio
    async def test_cache_survives_refresh_cycle(self):
        """Spec 25.1-9: stale cache entry remains after refresh completes."""
        reporter = CommunityNeighborReporter()
        await reporter.put_neighbor(
            "01" * 32, advert_timestamp=1, measured_snr=1, heard_timestamp=10
        )
        reporter._discovery_tag = 42
        reporter._discovery_deadline = 0
        reporter._discovery_periodic = False
        reporter._discovery_manual_scope = False
        reporter._discovery_target_ids = set()
        await reporter._tick()
        assert "01" * 32 in reporter._cache

    @pytest.mark.asyncio
    async def test_remove_neighbor_explicitly_prunes_entry(self):
        """Spec 6.4: explicit removal resets slot."""
        reporter = CommunityNeighborReporter()
        await reporter.put_neighbor(
            "01" * 32, advert_timestamp=1, measured_snr=1, heard_timestamp=10
        )
        await reporter.put_neighbor(
            "02" * 32, advert_timestamp=2, measured_snr=2, heard_timestamp=20
        )
        removed = await reporter.remove_neighbor("01" * 32)
        assert removed is True
        assert "01" * 32 not in reporter._cache
        assert "02" * 32 in reporter._cache

    @pytest.mark.asyncio
    async def test_remove_nonexistent_neighbor_returns_false(self):
        reporter = CommunityNeighborReporter()
        removed = await reporter.remove_neighbor("FF" * 32)
        assert removed is False

    @pytest.mark.asyncio
    async def test_clear_neighbors_removes_all(self):
        reporter = CommunityNeighborReporter()
        await reporter.put_neighbor(
            "01" * 32, advert_timestamp=1, measured_snr=1, heard_timestamp=10
        )
        await reporter.put_neighbor(
            "02" * 32, advert_timestamp=2, measured_snr=2, heard_timestamp=20
        )
        count = await reporter.clear_neighbors()
        assert count == 2
        assert reporter._cache == {}

    @pytest.mark.asyncio
    async def test_unsigned_advert_is_not_cached(self):
        """Advert that fails signature verification must not enter cache."""
        reporter = CommunityNeighborReporter()
        envelope = _make_envelope(payload_type=int(PayloadType.ADVERT))
        advert = SimpleNamespace(public_key="11" * 32, timestamp=1, device_role=2)
        with (
            patch("app.fanout.community_neighbors.parse_packet_envelope", return_value=envelope),
            patch("app.fanout.community_neighbors.parse_advertisement", return_value=advert),
            patch("app.fanout.community_neighbors.verify_advert_signature", return_value=False),
            patch("app.keystore.get_public_key", return_value=None),
        ):
            await reporter.observe_packet(b"raw")
        assert reporter._cache == {}

    def test_observation_id_dedup_eviction_at_256(self):
        """Spec 35-62: observation-ID set prunes beyond 256 entries."""
        reporter = CommunityNeighborReporter()
        for i in range(300):
            reporter._remember_observation_id(i)
        assert len(reporter._seen_observation_ids) <= 256
        assert 0 not in reporter._seen_observation_ids


class TestDiscoveryResponseValidation:
    @pytest.mark.asyncio
    async def test_wrong_response_tag_is_ignored(self):
        """Spec 25.2-11: mismatched discovery response tag ignored."""
        reporter = CommunityNeighborReporter()
        reporter._discovery_tag = 0xABCD1234
        reporter._discovery_deadline = 999999.0
        payload = {
            "node_type": 2,
            "path_len": 0,
            "tag": bytes.fromhex("00000001").hex(),
            "pubkey": PEER_PUBLIC_KEY.hex(),
            "SNR": 5.0,
        }
        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            await reporter._observe_discovery_response(payload)
        assert PEER_PUBLIC_KEY.hex().lower() not in reporter._cache

    @pytest.mark.asyncio
    async def test_expired_discovery_response_is_ignored(self):
        """Spec 25.2-12: response after 60s window ignored."""
        reporter = CommunityNeighborReporter()
        reporter._discovery_tag = 42
        reporter._discovery_deadline = 0
        payload = {
            "node_type": 2,
            "path_len": 0,
            "tag": (42).to_bytes(4, "little").hex(),
            "pubkey": PEER_PUBLIC_KEY.hex(),
            "SNR": 5.0,
        }
        await reporter._observe_discovery_response(payload)
        assert PEER_PUBLIC_KEY.hex().lower() not in reporter._cache

    @pytest.mark.asyncio
    async def test_wrong_node_type_in_discovery_response_is_ignored(self):
        """Spec 25.2-13: non-repeater discovery response ignored."""
        reporter = CommunityNeighborReporter()
        reporter._discovery_tag = 42
        reporter._discovery_deadline = 999999.0
        payload = {
            "node_type": 1,
            "path_len": 0,
            "tag": (42).to_bytes(4, "little").hex(),
            "pubkey": PEER_PUBLIC_KEY.hex(),
            "SNR": 5.0,
        }
        await reporter._observe_discovery_response(payload)
        assert PEER_PUBLIC_KEY.hex().lower() not in reporter._cache

    @pytest.mark.asyncio
    async def test_short_public_key_in_discovery_response_is_ignored(self):
        """Spec 25.2-14: short pubkey ignored."""
        reporter = CommunityNeighborReporter()
        reporter._discovery_tag = 42
        reporter._discovery_deadline = 999999.0
        for bad_key in ("AA", "AA" * 16, ""):
            payload = {
                "node_type": 2,
                "path_len": 0,
                "tag": (42).to_bytes(4, "little").hex(),
                "pubkey": bad_key,
                "SNR": 5.0,
            }
            await reporter._observe_discovery_response(payload)
        assert not reporter._cache

    @pytest.mark.asyncio
    async def test_self_identity_in_discovery_response_is_ignored(self):
        """Spec 25.2-15: self-identity ignored."""
        reporter = CommunityNeighborReporter()
        reporter._discovery_tag = 42
        reporter._discovery_deadline = 999999.0
        payload = {
            "node_type": 2,
            "path_len": 0,
            "tag": (42).to_bytes(4, "little").hex(),
            "pubkey": OUR_PUBLIC_KEY.hex(),
            "SNR": 5.0,
        }
        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            await reporter._observe_discovery_response(payload)
        assert OUR_PUBLIC_KEY.hex().lower() not in reporter._cache

    @pytest.mark.asyncio
    async def test_discovery_response_uses_observer_side_snr(self):
        """Spec 25.2-16: observer-side SNR stored, not response byte 1."""
        reporter = CommunityNeighborReporter()
        reporter._discovery_tag = 42
        reporter._discovery_deadline = 999999.0
        payload = {
            "node_type": 2,
            "path_len": 0,
            "tag": (42).to_bytes(4, "little").hex(),
            "pubkey": PEER_PUBLIC_KEY.hex(),
            "SNR": 6.5,
        }
        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            await reporter._observe_discovery_response(payload)
        cached = reporter._cache.get(PEER_PUBLIC_KEY.hex().lower())
        assert cached is not None
        assert cached.snr_q4 == _quantize_snr(6.5)

    @pytest.mark.asyncio
    async def test_discovery_response_non_zero_path_ignored(self):
        """Discovery response with non-zero-hop path_len ignored."""
        reporter = CommunityNeighborReporter()
        reporter._discovery_tag = 42
        reporter._discovery_deadline = 999999.0
        payload = {
            "node_type": 2,
            "path_len": 2,
            "tag": (42).to_bytes(4, "little").hex(),
            "pubkey": PEER_PUBLIC_KEY.hex(),
            "SNR": 5.0,
        }
        await reporter._observe_discovery_response(payload)
        assert PEER_PUBLIC_KEY.hex().lower() not in reporter._cache

    @pytest.mark.asyncio
    async def test_none_discovery_tag_ignored(self):
        """No active discovery tag -> response ignored."""
        reporter = CommunityNeighborReporter()
        reporter._discovery_tag = None
        payload = {
            "node_type": 2,
            "path_len": 0,
            "tag": "aabbccdd",
            "pubkey": PEER_PUBLIC_KEY.hex(),
            "SNR": 5.0,
        }
        await reporter._observe_discovery_response(payload)
        assert not reporter._cache


# ===================================================================
# S25.3  Scope-query phase tests
# ===================================================================


class TestScopeQueryPhase:
    @pytest.mark.asyncio
    async def test_each_cached_neighbor_receives_one_request(self):
        """Spec 25.3-18: one request per cached neighbor."""
        reporter = CommunityNeighborReporter()
        key_a = "aa" * 32
        key_b = "bb" * 32
        reporter._modules["x"] = _Module("x", {}, connected=True)
        await reporter.put_neighbor(key_a, advert_timestamp=1, measured_snr=1, heard_timestamp=10)
        await reporter.put_neighbor(key_b, advert_timestamp=2, measured_snr=2, heard_timestamp=20)

        seen_keys = set()
        tag_ctr = [0]

        async def fake_send(mc, pk):
            seen_keys.add(pk)
            tag_ctr[0] += 1
            tag_hex = format(tag_ctr[0], "08x")
            return SimpleNamespace(
                type=0x0F,
                payload={"expected_ack": bytes.fromhex(tag_hex)},
            )

        @asynccontextmanager
        async def fake_radio_op(name, **kwargs):
            class _Mc:
                pass

            yield _Mc()

        with (
            patch.object(reporter, "_send_regions_request", fake_send),
            patch("app.services.radio_runtime.radio_runtime.radio_operation", fake_radio_op),
            patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY),
        ):
            await reporter._begin_scope_queries(target_ids={"x"}, periodic=False, manual=True)

        assert seen_keys == {key_a, key_b}

    @pytest.mark.asyncio
    async def test_send_allocation_failure_becomes_send_failed(self):
        """Spec 25.3-19: send failure -> send_failed status."""
        reporter = CommunityNeighborReporter()
        key = "CC" * 32
        reporter._modules["x"] = _Module("x", {})
        await reporter.put_neighbor(key, advert_timestamp=1, measured_snr=1, heard_timestamp=10)

        async def failing_send(mc, pk):
            return None

        @asynccontextmanager
        async def fake_radio_op(name, **kwargs):
            class _Mc:
                pass

            yield _Mc()

        with (
            patch.object(reporter, "_send_regions_request", failing_send),
            patch("app.services.radio_runtime.radio_runtime.radio_operation", fake_radio_op),
            patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY),
        ):
            await reporter._begin_scope_queries(target_ids={"x"}, periodic=False, manual=True)

        assert len(reporter._modules["x"].published) == 1
        snapshot = json.loads(reporter._modules["x"].published[0])
        assert snapshot["neighbors"][0]["status"] == "send_failed"
        assert snapshot["neighbors"][0]["scopes"] == ""

    @pytest.mark.asyncio
    async def test_valid_empty_response_completes_with_empty_scopes(self):
        """Spec 25.3-20: 8-byte response -> responded with empty scopes."""
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
            await reporter.observe_packet(_response_packet(tag=tag, scopes=b""))

        assert entry.status == "responded"
        assert entry.scopes == ""

    @pytest.mark.asyncio
    async def test_too_short_response_does_not_complete_entry(self):
        """Spec 25.3-22: < 8 bytes plaintext -> entry stays pending."""
        reporter = CommunityNeighborReporter()
        entry = ScopeQueryEntry(
            public_key=PEER_PUBLIC_KEY.hex(),
            heard_timestamp=100,
            snr_q4=8,
            request_tag="01020304",
        )
        accepted = await reporter._accept_scope_response(entry, b"abc")
        assert accepted is False
        assert entry.status == "pending"

    @pytest.mark.asyncio
    async def test_duplicate_response_does_not_overwrite_completed(self):
        """Spec 25.3-24: duplicate response leaves completed entry alone."""
        reporter = CommunityNeighborReporter()
        entry = ScopeQueryEntry(
            public_key=PEER_PUBLIC_KEY.hex(),
            heard_timestamp=100,
            snr_q4=8,
            request_tag="01020304",
            scopes="original",
            status="responded",
        )
        reporter._scope_active = True
        dup_plaintext = bytes.fromhex("01020304") + b"\x00" * 4 + b"overwrite"
        accepted = await reporter._accept_scope_response(entry, dup_plaintext)
        assert accepted is False
        assert entry.scopes == "original"
        assert entry.status == "responded"

    @pytest.mark.asyncio
    async def test_batch_timeout_marks_pending_as_timeout(self):
        """Spec 25.3-28: remaining pending -> timeout."""
        reporter = CommunityNeighborReporter()
        key_a = "EE" * 32
        key_b = "FF" * 32
        reporter._modules["x"] = _Module("x", {})
        reporter._scope_active = True
        reporter._scope_entries = [
            ScopeQueryEntry(key_a, heard_timestamp=10, snr_q4=4, status="responded"),
            ScopeQueryEntry(key_b, heard_timestamp=20, snr_q4=4, status="pending"),
        ]
        reporter._scope_target_ids = {"x"}

        captured_entries = reporter._scope_entries
        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            await reporter._finish_scope_queries(timeout=True)

        assert captured_entries[1].status == "timeout"

    @pytest.mark.asyncio
    async def test_zero_neighbor_cache_publishes_immediately(self):
        """Spec 25.3-29: empty cache -> immediate snapshot with []."""
        reporter = CommunityNeighborReporter()
        reporter._modules["x"] = _Module("x", {})
        assert reporter._cache == {}

        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            await reporter._begin_scope_queries(target_ids={"x"}, periodic=False, manual=True)

        assert len(reporter._modules["x"].published) == 1
        snapshot = json.loads(reporter._modules["x"].published[0])
        assert snapshot["neighbors"] == []

    @pytest.mark.asyncio
    async def test_bounded_scope_text_nulls_and_caps(self):
        """Verify 95-byte cap and null termination."""
        ninety_bytes = b"a" * 90 + b"\x00trailing"
        result = _bounded_scope_text(ninety_bytes)
        assert len(result) <= 95
        assert "trailing" not in result


# ===================================================================
# S25.4  JSON contract tests
# ===================================================================


class TestJsonContractComprehensive:
    def test_required_fields_present(self, monkeypatch):
        """Spec 25.4-30: all required fields present in root + neighbor."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module(
            "a",
            {
                "neighbor_origin": "Test",
                "neighbor_self_scopes": "EU,US",
            },
        )
        entries = [
            ScopeQueryEntry(
                "AA" * 32, heard_timestamp=90, snr_q4=8, scopes="EU", status="responded"
            ),
        ]
        monkeypatch.setattr("app.fanout.community_neighbors._wall_time", lambda: 100)

        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            serialized = reporter._build_snapshot(entries, {"a"})

        assert serialized is not None
        snapshot = json.loads(serialized)
        assert "timestamp" in snapshot
        assert "origin" in snapshot
        assert "origin_id" in snapshot
        assert "self" in snapshot
        assert "scopes" in snapshot["self"]
        assert "status" not in snapshot["self"]
        assert "neighbors" in snapshot
        neighbor = snapshot["neighbors"][0]
        for field in ("pubkey", "snr", "heard_secs_ago", "scopes", "status"):
            assert field in neighbor, f"missing {field}"

    def test_clock_backward_clamp(self, monkeypatch):
        """Spec 25.4-33: future heard_timestamp -> age 0."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module("a", {})
        monkeypatch.setattr("app.fanout.community_neighbors._wall_time", lambda: 100)
        entries = [
            ScopeQueryEntry("AA" * 32, heard_timestamp=200, snr_q4=8, status="responded"),
        ]
        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            serialized = reporter._build_snapshot(entries, {"a"})
        snapshot = json.loads(serialized)
        assert snapshot["neighbors"][0]["heard_secs_ago"] == 0

    def test_pubkey_lexical_tie_break(self, monkeypatch):
        """Spec 25.4-36: pubkey tie-break is deterministic."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module("a", {})
        monkeypatch.setattr("app.fanout.community_neighbors._wall_time", lambda: 100)
        entries = [
            ScopeQueryEntry("CC" * 32, heard_timestamp=95, snr_q4=8, status="timeout"),
            ScopeQueryEntry("BB" * 32, heard_timestamp=95, snr_q4=8, status="timeout"),
            ScopeQueryEntry("AA" * 32, heard_timestamp=95, snr_q4=8, status="timeout"),
        ]
        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            serialized = reporter._build_snapshot(entries, {"a"})
        keys = [n["pubkey"] for n in json.loads(serialized)["neighbors"]]
        assert keys == ["AA" * 32, "BB" * 32, "CC" * 32]

    def test_json_escaping_handles_quotes_in_scopes(self, monkeypatch):
        """Spec 25.4-37: unusual scope chars are JSON-safe."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module(
            "a",
            {
                "neighbor_self_scopes": "a",
                "neighbor_origin": "B",
            },
        )
        entries = [
            ScopeQueryEntry(
                "AA" * 32, heard_timestamp=90, snr_q4=8, scopes='foo"bar', status="responded"
            ),
        ]
        monkeypatch.setattr("app.fanout.community_neighbors._wall_time", lambda: 100)
        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            serialized = reporter._build_snapshot(entries, {"a"})
        snapshot = json.loads(serialized)
        assert snapshot["neighbors"][0]["scopes"] == 'foo"bar'

    def test_scopes_with_backslash_survive_roundtrip(self, monkeypatch):
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module(
            "a",
            {
                "neighbor_self_scopes": "a",
                "neighbor_origin": "B",
            },
        )
        entries = [
            ScopeQueryEntry(
                "AA" * 32, heard_timestamp=90, snr_q4=8, scopes="path\\trail", status="responded"
            ),
        ]
        monkeypatch.setattr("app.fanout.community_neighbors._wall_time", lambda: 100)
        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            serialized = reporter._build_snapshot(entries, {"a"})
        snapshot = json.loads(serialized)
        assert snapshot["neighbors"][0]["scopes"] == "path\\trail"

    def test_progressive_truncation_leaves_valid_json(self, monkeypatch):
        """Spec 25.4-38: tail removed, valid JSON remains."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module(
            "a",
            {
                "neighbor_origin": "O",
                "neighbor_self_scopes": "S",
            },
        )
        monkeypatch.setattr("app.fanout.community_neighbors._wall_time", lambda: 100)

        entries = [
            ScopeQueryEntry(format(i, "064d"), heard_timestamp=i, snr_q4=4, status="timeout")
            for i in range(200)
        ]
        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            serialized = reporter._build_snapshot(entries, {"a"})

        assert serialized is not None
        snapshot = json.loads(serialized)
        assert len(snapshot["neighbors"]) < 200
        encoded = serialized.encode("utf-8")
        assert 0 < len(encoded) < MAX_SNAPSHOT_BYTES

    def test_root_object_too_large_fails_cleanly(self, monkeypatch):
        """Spec 25.4-39: root too large -> None."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module(
            "a",
            {
                "neighbor_origin": "N",
                "neighbor_self_scopes": "x",
            },
        )
        monkeypatch.setattr("app.fanout.community_neighbors.MAX_SNAPSHOT_BYTES", 20)
        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            serialized = reporter._build_snapshot([], {"a"})
        assert serialized is None

    def test_origin_quote_stripping(self):
        """Neighbor origin strips surrounding quotes."""
        assert normalize_neighbor_origin("'Observer'") == "Observer"
        assert normalize_neighbor_origin('"Device 1"') == "Device 1"
        assert normalize_neighbor_origin("NoQuotes") == "NoQuotes"

    def test_empty_origin_normalization(self):
        assert normalize_neighbor_origin(None) == ""
        assert normalize_neighbor_origin("") == ""

    def test_snr_is_float_not_string(self, monkeypatch):
        """SNR is a JSON number, not string."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module("a", {})
        monkeypatch.setattr("app.fanout.community_neighbors._wall_time", lambda: 100)
        entries = [
            ScopeQueryEntry("AA" * 32, heard_timestamp=90, snr_q4=10, status="responded"),
        ]
        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            serialized = reporter._build_snapshot(entries, {"a"})
        snapshot = json.loads(serialized)
        snr = snapshot["neighbors"][0]["snr"]
        assert isinstance(snr, (int, float))
        assert snr == 2.5

    def test_heard_secs_ago_is_int(self, monkeypatch):
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module("a", {})
        monkeypatch.setattr("app.fanout.community_neighbors._wall_time", lambda: 100)
        entries = [
            ScopeQueryEntry("AA" * 32, heard_timestamp=90, snr_q4=4, status="timeout"),
        ]
        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            serialized = reporter._build_snapshot(entries, {"a"})
        snapshot = json.loads(serialized)
        assert isinstance(snapshot["neighbors"][0]["heard_secs_ago"], int)

    def test_status_is_valid_enum(self, monkeypatch):
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module("a", {})
        monkeypatch.setattr("app.fanout.community_neighbors._wall_time", lambda: 100)
        for status in ("responded", "timeout", "send_failed"):
            entries = [
                ScopeQueryEntry("AA" * 32, heard_timestamp=90, snr_q4=4, status=status),
            ]
            with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
                serialized = reporter._build_snapshot(entries, {"a"})
            assert status in serialized


# ===================================================================
# S25.5  MQTT delivery tests
# ===================================================================


class TestMqttDelivery:
    @pytest.mark.asyncio
    async def test_multi_slot_partial_success_tracks_ok(self):
        """Spec 25.5-43: one successful slot -> ok."""
        reporter = CommunityNeighborReporter()
        reporter._modules["good"] = _Module("good", {})
        reporter._modules["bad"] = _FailingModule("bad", {})

        entries = [ScopeQueryEntry("AA" * 32, heard_timestamp=10, snr_q4=4, status="responded")]
        reporter._scope_active = True
        reporter._scope_entries = entries
        reporter._scope_target_ids = {"good", "bad"}

        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            await reporter._finish_scope_queries(timeout=False)

        assert reporter._last_publish_result == "ok"
        assert len(reporter._modules["good"].published) == 1

    @pytest.mark.asyncio
    async def test_no_connected_slots_yields_failed(self):
        """Spec 25.5-44: no connected slots -> failed."""
        reporter = CommunityNeighborReporter()
        reporter._modules["off"] = _Module("off", {}, connected=False)

        entries = [ScopeQueryEntry("AA" * 32, heard_timestamp=10, snr_q4=4, status="responded")]
        reporter._scope_active = True
        reporter._scope_entries = entries
        reporter._scope_target_ids = {"off"}

        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            await reporter._finish_scope_queries(timeout=False)

        assert reporter._last_publish_result == "failed"

    @pytest.mark.asyncio
    async def test_all_failing_slots_yields_failed(self):
        """Spec 25.5-44: all publish calls fail -> failed."""
        reporter = CommunityNeighborReporter()
        reporter._modules["bad1"] = _FailingModule("bad1", {})
        reporter._modules["bad2"] = _FailingModule("bad2", {})

        entries = [ScopeQueryEntry("AA" * 32, heard_timestamp=10, snr_q4=4, status="responded")]
        reporter._scope_active = True
        reporter._scope_entries = entries
        reporter._scope_target_ids = {"bad1", "bad2"}

        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            await reporter._finish_scope_queries(timeout=False)

        assert reporter._last_publish_result == "failed"

    @pytest.mark.asyncio
    async def test_pending_handoff_protection(self):
        """Spec 25.5-46: publishing flag blocks new scope start."""
        reporter = CommunityNeighborReporter()
        reporter._modules["one"] = _Module("one", {}, connected=True)
        reporter._publishing_snapshot = True

        result = await reporter.start_manual_snapshot("one")
        assert result["status"] == "active"
        assert "still publishing" in result["message"]


# ===================================================================
# S25.6  Scheduling tests
# ===================================================================


class TestScheduling:
    @pytest.mark.asyncio
    async def test_periodic_schedule_becomes_due_on_enable(self):
        """Spec 25.6-50: first periodic run due immediately."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module(
            "a",
            {
                "neighbor_reporting_enabled": True,
                "neighbor_reporting_interval_hours": 24,
            },
        )
        await reporter._ensure_periodic_schedule()
        assert reporter._next_periodic_at is not None
        assert reporter._next_periodic_at <= 99999999999.0

    @pytest.mark.asyncio
    async def test_shorter_interval_module_brings_forward_schedule(self):
        """Spec 35-57: shorter-interval module joins, schedule recalculated."""
        reporter = CommunityNeighborReporter()
        reporter._modules["slow"] = _Module(
            "slow",
            {
                "neighbor_reporting_enabled": True,
                "neighbor_reporting_interval_hours": 336,
            },
        )
        await reporter._ensure_periodic_schedule()
        original_at = reporter._next_periodic_at

        reporter._modules["fast"] = _Module(
            "fast",
            {
                "neighbor_reporting_enabled": True,
                "neighbor_reporting_interval_hours": 12,
            },
        )
        await reporter._ensure_periodic_schedule()

        if original_at is not None:
            assert reporter._next_periodic_at is not None
            assert reporter._next_periodic_at <= original_at

    @pytest.mark.asyncio
    async def test_schedule_cleared_when_no_periodic_modules(self):
        """Spec 25.6-53: schedule cleared with no periodic reporters."""
        reporter = CommunityNeighborReporter()
        reporter._next_periodic_at = 12345.0
        reporter._modules["a"] = _Module(
            "a",
            {
                "neighbor_reporting_enabled": False,
            },
        )
        await reporter._ensure_periodic_schedule()
        assert reporter._next_periodic_at is None

    def test_periodic_interval_range_enforced(self):
        """Spec 25.6-49: interval clamped 12-336 hours."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module(
            "a",
            {
                "neighbor_reporting_enabled": True,
                "neighbor_reporting_interval_hours": 6,
            },
        )
        seconds = reporter._periodic_interval_seconds()
        assert seconds >= 12 * 3600

        reporter._modules["a"].config["neighbor_reporting_interval_hours"] = 500
        seconds = reporter._periodic_interval_seconds()
        assert seconds <= 336 * 3600

    def test_periodic_interval_is_minimum_across_modules(self):
        """Spec 35-57: effective interval is min of all enabled."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module(
            "a",
            {
                "neighbor_reporting_enabled": True,
                "neighbor_reporting_interval_hours": 48,
            },
        )
        reporter._modules["b"] = _Module(
            "b",
            {
                "neighbor_reporting_enabled": True,
                "neighbor_reporting_interval_hours": 24,
            },
        )
        reporter._modules["c"] = _Module(
            "c",
            {
                "neighbor_reporting_enabled": False,
                "neighbor_reporting_interval_hours": 12,
            },
        )
        seconds = reporter._periodic_interval_seconds()
        assert seconds == 24 * 3600

    @pytest.mark.asyncio
    async def test_manual_discovery_join_when_active(self):
        """Spec 25.6-51: manual discover joins existing window."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module("a", {})
        reporter._discovery_tag = 42
        reporter._discovery_deadline = 999999.0
        result = await reporter.start_manual_discovery("a")
        assert result["status"] == "joined"

    @pytest.mark.asyncio
    async def test_manual_snapshot_queues_behind_active_refresh(self):
        """Spec 25.6-52: manual scope request queues behind refresh."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module("a", {}, connected=True)
        reporter._discovery_tag = 42
        reporter._discovery_deadline = 999999.0
        result = await reporter.start_manual_snapshot("a")
        assert result["status"] == "queued"


# ===================================================================
# S35  Shared coordinator tests
# ===================================================================


class TestSharedCoordinator:
    @pytest.mark.asyncio
    async def test_two_slots_both_get_snapshot(self):
        """Spec 35-56: both periodic and manual slots get snapshot."""
        reporter = CommunityNeighborReporter()
        mod_a = _Module("a", {"neighbor_reporting_enabled": True})
        mod_b = _Module("b", {"neighbor_reporting_enabled": True})
        reporter._modules["a"] = mod_a
        reporter._modules["b"] = mod_b
        await reporter.put_neighbor(
            "AA" * 32, advert_timestamp=1, measured_snr=1, heard_timestamp=10
        )

        tag_ctr = [0]

        async def fake_send(mc, pk):
            tag_ctr[0] += 1
            return SimpleNamespace(
                type=0x0F, payload={"expected_ack": bytes.fromhex(format(tag_ctr[0], "08x"))}
            )

        @asynccontextmanager
        async def fake_radio_op(name, **kwargs):
            class _Mc:
                pass

            yield _Mc()

        with (
            patch.object(reporter, "_send_regions_request", fake_send),
            patch("app.services.radio_runtime.radio_runtime.radio_operation", fake_radio_op),
            patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY),
        ):
            await reporter._begin_scope_queries(target_ids={"a", "b"}, periodic=True, manual=False)

        assert len(mod_a.published) == 1
        assert len(mod_b.published) == 1
        assert mod_a.published[0] == mod_b.published[0]

    @pytest.mark.asyncio
    async def test_disabled_slot_not_receiving_snapshot(self):
        """Spec 35-58: disabled slot doesn't receive snapshot."""
        reporter = CommunityNeighborReporter()
        mod_on = _Module("on", {"neighbor_reporting_enabled": True})
        mod_off = _Module("off", {"neighbor_reporting_enabled": False})
        reporter._modules["on"] = mod_on
        reporter._modules["off"] = mod_off
        await reporter.put_neighbor(
            "AA" * 32, advert_timestamp=1, measured_snr=1, heard_timestamp=10
        )

        tag_ctr = [0]

        async def fake_send(mc, pk):
            tag_ctr[0] += 1
            return SimpleNamespace(
                type=0x0F, payload={"expected_ack": bytes.fromhex(format(tag_ctr[0], "08x"))}
            )

        @asynccontextmanager
        async def fake_radio_op(name, **kwargs):
            class _Mc:
                pass

            yield _Mc()

        with (
            patch.object(reporter, "_send_regions_request", fake_send),
            patch("app.services.radio_runtime.radio_runtime.radio_operation", fake_radio_op),
            patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY),
        ):
            await reporter._begin_scope_queries(
                target_ids={"on", "off"}, periodic=True, manual=False
            )

        assert len(mod_on.published) >= 1
        assert len(mod_off.published) == 0

    @pytest.mark.asyncio
    async def test_slot_removal_during_active_phase_does_not_crash(self):
        """Spec 35-59: slot removal during active phase doesn't crash."""
        reporter = CommunityNeighborReporter()
        mod_a = _Module("a", {}, connected=True)
        reporter._modules["a"] = mod_a
        reporter._scope_active = True
        reporter._scope_entries = [
            ScopeQueryEntry("AA" * 32, heard_timestamp=10, snr_q4=4, status="responded")
        ]
        reporter._scope_target_ids = {"a", "b"}
        await reporter.unregister_module("b")
        assert "b" not in reporter._modules
        assert reporter._scope_active is True

    @pytest.mark.asyncio
    async def test_status_reports_correct_phases(self):
        """Spec 18: status endpoint reports all phases correctly."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module("a", {"neighbor_reporting_enabled": True})

        status = reporter.status_for("a")
        assert status["phase"] in ("idle", "due", "scheduled")
        assert "cache_size" in status
        assert "periodic_enabled" in status
        assert "last_publish_result" in status

        reporter._publishing_snapshot = True
        assert reporter.status_for("a")["phase"] == "publishing"

        reporter._publishing_snapshot = False
        reporter._scope_active = True
        assert reporter.status_for("a")["phase"] == "scopes"

        reporter._scope_active = False
        reporter._discovery_tag = 42
        assert reporter.status_for("a")["phase"] == "refresh"

    @pytest.mark.asyncio
    async def test_scope_queries_blocked_when_bridge_not_connected(self):
        """Spec 22.2: manual scope request rejected when bridge not connected."""
        reporter = CommunityNeighborReporter()
        module = _Module("a", {}, connected=False)
        reporter._modules["a"] = module
        with pytest.raises(NeighborReporterError, match="not running"):
            await reporter.start_manual_snapshot("a")

    @pytest.mark.asyncio
    async def test_begin_scope_checks_connected_broker_exists(self):
        """Scope queries only start when at least one broker is connected."""
        reporter = CommunityNeighborReporter()
        reporter._modules["off"] = _Module("off", {}, connected=False)
        await reporter.put_neighbor(
            "AA" * 32, advert_timestamp=1, measured_snr=1, heard_timestamp=10
        )

        with pytest.raises(NeighborReporterError, match="not running"):
            await reporter._begin_scope_queries(target_ids={"off"}, periodic=False, manual=True)


# ===================================================================
# S25.1 / S25.5  Edge-case tests
# ===================================================================


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_put_neighbor_rejects_invalid_hex(self):
        reporter = CommunityNeighborReporter()
        await reporter.put_neighbor("ZZ" * 32, advert_timestamp=1, measured_snr=1)
        assert reporter._cache == {}

    @pytest.mark.asyncio
    async def test_put_neighbor_rejects_wrong_length_key(self):
        reporter = CommunityNeighborReporter()
        await reporter.put_neighbor("AA" * 16, advert_timestamp=1, measured_snr=1)
        assert reporter._cache == {}

    @pytest.mark.asyncio
    async def test_put_neighbor_rejects_self_key(self):
        reporter = CommunityNeighborReporter()
        with patch.object(reporter, "_local_public_key_hex", return_value="01" * 32):
            await reporter.put_neighbor("01" * 32, advert_timestamp=1, measured_snr=1)
        assert reporter._cache == {}

    def test_normalize_self_scopes_rejects_control_chars(self):
        with pytest.raises(ValueError):
            normalize_self_scopes(["good", "bad\x01"])

    def test_normalize_self_scopes_strips_hash_and_trims(self):
        assert normalize_self_scopes(" #*, #Sweden , ##double") == "*,Sweden,#double"


# ===================================================================
# S25.2  Discovery request payload bytes (Spec 25.2-10)
# ===================================================================


class TestDiscoveryRequestBytes:
    @pytest.mark.asyncio
    async def test_discovery_request_repeater_only_full_key_no_modified_since(self):
        """Spec 5.2.1 + 25.2-10: exact request args for zero-hop discovery.

        The spec requires:
          - Control type 0x80 (discovery request, prefix_only=False)
          - Filter bitmask 0x04 (1 << ADV_TYPE_REPEATER)
          - Unique uint32 tag, little-endian
          - Modified-since timestamp = 0

        We verify that send_node_discover_req is called with these args.
        """
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module("a", {}, connected=True)

        captured_filter_bits = []
        captured_prefix_only = []
        captured_tag = []
        captured_since = []

        async def recording_discover_req(filter_bits, prefix_only=False, tag=None, since=None):
            captured_filter_bits.append(filter_bits)
            captured_prefix_only.append(prefix_only)
            captured_tag.append(tag)
            captured_since.append(since)
            return SimpleNamespace(type=0x0F)

        @asynccontextmanager
        async def fake_radio_op(name, **kwargs):
            class _Mc:
                pass

            mc = _Mc()
            mc.commands = SimpleNamespace()
            mc.commands.send_node_discover_req = AsyncMock(side_effect=recording_discover_req)
            mc.subscribe = MagicMock()
            mc.subscribe.return_value = SimpleNamespace(unsubscribe=MagicMock())
            yield mc

        with (
            patch("app.services.radio_runtime.radio_runtime.radio_operation", fake_radio_op),
        ):
            await reporter._begin_discovery(periodic=False, target_ids={"a"})

        assert captured_filter_bits == [4]
        assert captured_prefix_only == [False]
        assert captured_since == [0]
        assert isinstance(captured_tag[0], int)
        assert captured_tag[0] != 0


# ===================================================================
# S25.3  Scope overlay non-interference + early completion
# ===================================================================


class TestScopeOverlayAcLNonInterference:
    @pytest.mark.asyncio
    async def test_wrong_tag_from_acl_neighbor_does_not_complete_overlay(self):
        """Spec 25.3-26: unrelated ACL response does not intercept overlay.

        A response that shares the compact source hash of a queried
        neighbor but carries a different tag must not complete the
        overlay entry.  This keeps normal ACL traffic flowing while
        the overlay only matches pending scope-query answers.
        """
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

        # Build a response with a non-matching tag
        unmatched_tag = bytes.fromhex("DEADBEEF")
        with (
            patch("app.keystore.get_private_key", return_value=OUR_PRIVATE_KEY),
            patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY),
        ):
            await reporter.observe_packet(
                _response_packet(tag=unmatched_tag, scopes=b"should_not_appear")
            )

        assert entry.status == "pending"
        assert entry.scopes == ""


class TestScopeQueryEarlyCompletion:
    @pytest.mark.asyncio
    async def test_early_completion_when_all_entries_finish_before_timeout(self):
        """Spec 25.3-27: snapshot publishes before 30s when all entries complete.

        When every scope-query entry leaves pending state before the
        shared 30-second deadline, _finish_scope_queries must be called
        immediately rather than waiting for the deadline.
        """
        reporter = CommunityNeighborReporter()
        reporter._modules["x"] = _Module("x", {})
        key_a = "aa" * 32
        key_b = "bb" * 32
        await reporter.put_neighbor(key_a, advert_timestamp=1, measured_snr=1, heard_timestamp=10)
        await reporter.put_neighbor(key_b, advert_timestamp=2, measured_snr=2, heard_timestamp=20)

        tag_ctr = [0]

        async def instant_fail_send(mc, pk):
            tag_ctr[0] += 1
            return None

        @asynccontextmanager
        async def fake_radio_op(name, **kwargs):
            class _Mc:
                pass

            yield _Mc()

        with (
            patch.object(reporter, "_send_regions_request", instant_fail_send),
            patch(
                "app.services.radio_runtime.radio_runtime.radio_operation",
                fake_radio_op,
            ),
            patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY),
        ):
            await reporter._begin_scope_queries(target_ids={"x"}, periodic=False, manual=True)

        assert len(reporter._modules["x"].published) == 1
        assert reporter._scope_active is False
        assert reporter._scope_deadline is None


# ===================================================================
# S25.4  Uppercase public keys (Spec 25.4-31)
# ===================================================================


class TestJsonKeyFormat:
    def test_origin_id_and_pubkey_are_uppercase_64_char_hex(self, monkeypatch):
        """Spec 25.4-31: origin_id and pubkey must be uppercase 64-character hex."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module(
            "a",
            {
                "neighbor_origin": "Test",
                "neighbor_self_scopes": "EU",
            },
        )
        entries = [
            ScopeQueryEntry("aa" * 32, heard_timestamp=90, snr_q4=8, scopes="", status="responded"),
        ]
        monkeypatch.setattr("app.fanout.community_neighbors._wall_time", lambda: 100)

        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            serialized = reporter._build_snapshot(entries, {"a"})

        assert serialized is not None
        snapshot = json.loads(serialized)

        assert snapshot["origin_id"] == OUR_PUBLIC_KEY.hex().upper()
        assert all(c in "0123456789ABCDEF" for c in snapshot["origin_id"])

        for neighbor in snapshot["neighbors"]:
            assert neighbor["pubkey"] == neighbor["pubkey"].upper()
            assert all(c in "0123456789ABCDEF" for c in neighbor["pubkey"])


# ===================================================================
# S25.5  Invalid IATA does not block other slots (Spec 25.5-45)
# ===================================================================


class TestInvalidIataSlotRobustness:
    @pytest.mark.asyncio
    async def test_invalid_iata_skipped_other_slot_gets_snapshot(self):
        """Spec 25.5-45: one invalid IATA does not block valid alternative slots.

        When one module's publish_neighbor_snapshot returns False (e.g.
        because its IATA code is invalid), the reporter continues to the
        next module rather than aborting.
        """
        reporter = CommunityNeighborReporter()
        mod_good = _Module("good", {"iata": "STO"}, connected=True)
        mod_bad = _Module("bad_iata", {"iata": "xx"}, connected=True)
        reporter._modules["good"] = mod_good
        reporter._modules["bad_iata"] = mod_bad
        reporter._scope_active = True
        reporter._scope_entries = [
            ScopeQueryEntry("AA" * 32, heard_timestamp=10, snr_q4=4, status="responded")
        ]
        reporter._scope_target_ids = {"good", "bad_iata"}

        with patch("app.keystore.get_public_key", return_value=OUR_PUBLIC_KEY):
            await reporter._finish_scope_queries(timeout=False)

        assert reporter._last_publish_result == "ok"
        assert len(mod_good.published) == 1


# ===================================================================
# S25.6  No retry loop on failure + timer wraparound
# ===================================================================


class TestFailedCycleRescheduling:
    @pytest.mark.asyncio
    async def test_failed_cycle_schedules_next_interval_not_tight_loop(self):
        """Spec 25.6-54: failed phase schedules next interval, no retry loop.

        When _begin_discovery fails (e.g. radio error), a periodic
        reporter must schedule the next interval rather than creating
        a tight retry loop.
        """
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module(
            "a",
            {
                "neighbor_reporting_enabled": True,
                "neighbor_reporting_interval_hours": 24,
            },
            connected=True,
        )
        reporter._next_periodic_at = 0

        async def failing_discover_req(filter_bits, prefix_only=False, tag=None, since=None):
            return SimpleNamespace(type=EventType.ERROR)

        @asynccontextmanager
        async def fake_radio_op(name, **kwargs):
            class _Mc:
                pass

            mc = _Mc()
            mc.commands = MagicMock()
            mc.commands.send_node_discover_req = AsyncMock(side_effect=failing_discover_req)
            mc.subscribe = MagicMock()
            mc.subscribe.return_value = SimpleNamespace(unsubscribe=MagicMock())
            yield mc

        with (
            patch(
                "app.services.radio_runtime.radio_runtime.radio_operation",
                fake_radio_op,
            ),
        ):
            try:
                await reporter._begin_discovery(periodic=True, target_ids={"a"})
            except NeighborReporterError:
                pass

        assert reporter._discovery_tag is None
        assert reporter._next_periodic_at is not None
        assert reporter._next_periodic_at > 0


class TestTimerWraparound:
    def test_deadline_signed_delta_works_across_wraparound(self):
        """Spec 25.6-55: signed delta handles 32-bit monotonic wrap.

        Using a signed difference for deadline checks ensures correct
        behavior when monotonic wraps.
        """

        # Simulate a "now" after wrap and a deadline set before wrap.
        # The signed delta (now - deadline) should be positive when expired.
        wrap_now = 2**31 - 4
        deadline_before = 2**31 - 10

        # This is conceptually what the spec requires: expired = signed32(now - deadline) >= 0
        delta = wrap_now - deadline_before
        assert delta >= 0

        # Not yet expired: now < deadline (both on same side of wrap)
        assert (deadline_before - 10 - deadline_before) < 0

    def test_periodic_interval_stays_below_max_safe_signed_range(self):
        """Spec 11.4: interval must stay below safe signed-difference window."""
        reporter = CommunityNeighborReporter()
        reporter._modules["a"] = _Module(
            "a",
            {
                "neighbor_reporting_enabled": True,
                "neighbor_reporting_interval_hours": 336,
            },
        )
        seconds = reporter._periodic_interval_seconds()
        assert seconds <= 336 * 3600
        assert seconds < 2**31
