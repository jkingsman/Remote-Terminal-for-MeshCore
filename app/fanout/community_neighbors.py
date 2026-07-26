"""Shared MQTT Community Broker neighbor-reporting coordinator.

The Community MQTT fanout can have several broker configurations, but the
radio can only perform one useful zero-hop survey at a time.  This coordinator
therefore owns one bounded neighbour cache and one active report workflow, then
hands the completed snapshot to every opted-in Community MQTT module.

The radio's companion protocol creates the authenticated anonymous regions
request for ``CMD_SEND_ANON_REQ``. The coordinator supplies the target identity
and an empty path (zero hop), then uses the firmware-generated tag from
``MSG_SENT`` to match direct encrypted raw responses. Raw matching is required
because the companion exposes only one host-side pending binary response while
a neighbor snapshot queries several peers as one concurrent batch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from meshcore import EventType

from app.decoder import (
    PayloadType,
    decrypt_response_payload,
    derive_shared_secret,
    parse_advertisement,
    verify_advert_signature,
)
from app.path_utils import parse_packet_envelope

logger = logging.getLogger(__name__)

MAX_NEIGHBORS = 50
MAX_SCOPE_BYTES = 95
MAX_SNAPSHOT_BYTES = 10_240
DISCOVERY_WINDOW_SECONDS = 60.0
SCOPE_QUERY_TIMEOUT_SECONDS = 30.0
DEFAULT_PERIODIC_INTERVAL_HOURS = 24
MIN_PERIODIC_INTERVAL_HOURS = 12
MAX_PERIODIC_INTERVAL_HOURS = 336

_ANON_REGIONS_REQUEST = 0x01
_SEND_ANON_REQUEST_COMMAND = 0x39

QueryStatus = Literal["pending", "responded", "timeout", "send_failed"]


class NeighborReporterError(RuntimeError):
    """An operator-actionable neighbor workflow error."""


class CommunityNeighborModule(Protocol):
    """Minimal Community MQTT module contract used by the coordinator."""

    config_id: str
    config: dict[str, Any]

    @property
    def neighbor_publisher_connected(self) -> bool: ...

    async def publish_neighbor_snapshot(self, serialized_snapshot: str) -> bool: ...


@dataclass(slots=True)
class NeighborCacheEntry:
    """One directly heard repeater identity retained across reporting cycles."""

    public_key: str
    advert_timestamp: int
    heard_timestamp: int
    snr_q4: int


@dataclass(slots=True)
class ScopeQueryEntry:
    """Frozen cache data and one anonymous region-query outcome."""

    public_key: str
    heard_timestamp: int
    snr_q4: int
    request_tag: str | None = None
    scopes: str = ""
    status: QueryStatus = "pending"


def _wall_time() -> int:
    """Wrapper around wall time for deterministic tests."""
    return int(time.time())


def _monotonic() -> float:
    """Wrapper around monotonic time for deterministic tests."""
    return time.monotonic()


def _quantize_snr(snr: object) -> int:
    """Return signed quarter-dB SNR with truncation toward zero."""
    try:
        value = float(cast(Any, snr))
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(value):
        return 0
    return max(-128, min(127, int(value * 4)))


def _bounded_scope_text(data: bytes) -> str:
    """Decode one peer scope string safely using the canonical 95-byte cap."""
    bounded = data[:MAX_SCOPE_BYTES]
    text = bounded.decode("utf-8", errors="ignore").split("\x00", 1)[0]
    # Scope strings are data, never commands.  Keep normal printable Unicode
    # but reject remaining control characters before JSON serialization.
    return "".join(char for char in text if char >= " ")


def normalize_self_scopes(value: object) -> str:
    """Normalize explicit local flood-allowed scopes into canonical CSV text.

    The companion protocol does not expose the local firmware's entire region
    permission map.  Community MQTT configs therefore carry this explicit
    operator-maintained export instead of incorrectly treating ``known_regions``
    (a decoder candidate list) as a permission list.
    """
    raw_values: list[str]
    if isinstance(value, str):
        raw_values = value.split(",")
    elif isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise ValueError("neighbor_self_scopes entries must be strings")
        raw_values = value
    elif value is None:
        raw_values = []
    else:
        raise ValueError("neighbor_self_scopes must be a comma-separated string or list")

    scopes: list[str] = []
    for raw in raw_values:
        scope = raw.strip()
        if any(ord(char) < 32 for char in scope):
            raise ValueError("neighbor_self_scopes cannot contain control characters")
        if scope.startswith("#"):
            scope = scope[1:]
        if not scope:
            continue
        scopes.append(scope)

    return ",".join(scopes)


def normalize_neighbor_origin(value: object) -> str:
    """Normalize the optional configured snapshot origin name."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("neighbor_origin must be a string")
    origin = value.strip()
    if len(origin) >= 2 and origin[0] == origin[-1] and origin[0] in {'"', "'"}:
        origin = origin[1:-1].strip()
    if any(ord(char) < 32 for char in origin):
        raise ValueError("neighbor_origin cannot contain control characters")
    return origin


def _format_snapshot_timestamp(timestamp: int) -> str:
    """Return the strict neighbor-schema UTC timestamp (not the packet ``Z`` form)."""
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="microseconds")


class CommunityNeighborReporter:
    """One bounded cache / state machine shared by Community MQTT modules."""

    def __init__(self, *, cache_limit: int = MAX_NEIGHBORS) -> None:
        self._cache_limit = max(1, cache_limit)
        self._cache: dict[str, NeighborCacheEntry] = {}
        self._modules: dict[str, CommunityNeighborModule] = {}

        self._transition_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

        self._last_tag = 0
        self._discovery_tag: int | None = None
        self._discovery_deadline: float | None = None
        self._discovery_subscription: Any = None
        # A radio can report a direct discovery response immediately after its
        # host command is accepted.  The window is deliberately armed only
        # after transmit, so retain matching events received in that tiny
        # handoff interval and replay them once the deadline exists.
        self._early_discovery_responses: list[dict[str, Any]] = []
        self._discovery_periodic = False
        self._discovery_manual = False
        self._discovery_manual_scope = False
        self._discovery_target_ids: set[str] = set()

        self._scope_active = False
        self._scope_deadline: float | None = None
        self._scope_entries: list[ScopeQueryEntry] = []
        # A very fast zero-hop response can be logged before the companion
        # host reports MSG_SENT and reveals its request tag. Keep a bounded
        # overlay buffer so that race is retried as soon as the tag exists.
        self._early_scope_responses: list[bytes] = []
        self._scope_periodic = False
        self._scope_target_ids: set[str] = set()
        # A completed scope phase hands one immutable document to MQTT.  Keep
        # that handoff serialized so a second manual request cannot overtake
        # or mutate an earlier snapshot while it is being published.
        self._publishing_snapshot = False

        self._next_periodic_at: float | None = None
        self._last_publish_result: Literal["ok", "failed"] | None = None

    # ── Module lifecycle ──────────────────────────────────────────────

    async def register_module(self, module: CommunityNeighborModule) -> None:
        """Make a live Community MQTT module eligible for shared reporting."""
        self._modules[module.config_id] = module
        self._ensure_task()
        await self._ensure_periodic_schedule()

    async def unregister_module(self, config_id: str) -> None:
        """Remove a module without discarding cache state for other brokers."""
        self._modules.pop(config_id, None)
        if self._modules:
            await self._ensure_periodic_schedule()
            return

        await self._reset_when_unused()

    def _ensure_task(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="community-mqtt-neighbors")

    async def _reset_when_unused(self) -> None:
        async with self._transition_lock:
            self._clear_discovery_subscription()
            self._discovery_tag = None
            self._discovery_deadline = None
            self._early_discovery_responses = []
            self._discovery_periodic = False
            self._discovery_manual = False
            self._discovery_manual_scope = False
            self._discovery_target_ids.clear()
            self._scope_active = False
            self._scope_deadline = None
            self._scope_entries = []
            self._early_scope_responses = []
            self._scope_target_ids.clear()
            self._scope_periodic = False
            self._publishing_snapshot = False
            self._next_periodic_at = None
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    # ── Passive cache population ───────────────────────────────────────

    async def observe_packet(
        self,
        raw: bytes,
        *,
        timestamp: object = None,
        measured_snr: object = None,
    ) -> None:
        """Observe one raw RF packet for passive adverts or active responses."""
        envelope = parse_packet_envelope(raw)
        if envelope is None:
            return
        if envelope.payload_type == int(PayloadType.RESPONSE):
            await self._observe_scope_response_packet(
                envelope.payload,
                envelope.hop_count,
                envelope.route_type,
            )
            return
        if envelope.payload_type != int(PayloadType.ADVERT):
            return
        if envelope.hop_count != 0:
            return
        # A local Share advert uses zero transport codes and does not establish
        # a radio neighbor relationship.
        if envelope.transport_codes == (0, 0):
            return

        advert = parse_advertisement(envelope.payload, raw_packet=raw)
        if advert is None or advert.device_role != 2:
            return
        if not verify_advert_signature(envelope.payload):
            return

        heard_timestamp = int(timestamp) if isinstance(timestamp, (int, float)) else _wall_time()
        await self.put_neighbor(
            advert.public_key,
            advert_timestamp=advert.timestamp,
            measured_snr=measured_snr,
            heard_timestamp=heard_timestamp,
        )

    async def put_neighbor(
        self,
        public_key: str,
        *,
        advert_timestamp: int,
        measured_snr: object,
        heard_timestamp: int | None = None,
    ) -> None:
        """Insert/update one cache entry, evicting the least recently heard entry."""
        normalized_key = public_key.lower()
        if len(normalized_key) != 64:
            return
        try:
            bytes.fromhex(normalized_key)
        except ValueError:
            return
        if normalized_key == self._local_public_key_hex():
            return

        entry = NeighborCacheEntry(
            public_key=normalized_key,
            advert_timestamp=max(0, int(advert_timestamp)),
            heard_timestamp=max(
                0, int(_wall_time() if heard_timestamp is None else heard_timestamp)
            ),
            snr_q4=_quantize_snr(measured_snr),
        )
        async with self._transition_lock:
            if normalized_key not in self._cache and len(self._cache) >= self._cache_limit:
                victim = min(
                    self._cache.values(),
                    key=lambda existing: (existing.heard_timestamp, existing.public_key),
                )
                self._cache.pop(victim.public_key, None)
            self._cache[normalized_key] = entry

    async def remove_neighbor(self, public_key: str) -> bool:
        """Explicitly remove a cached neighbor by full 64-char hex public key.

        Returns ``True`` when the key was present and removed, ``False`` when
        the key was not in the cache.
        """
        normalized_key = public_key.lower()
        async with self._transition_lock:
            return self._cache.pop(normalized_key, None) is not None

    async def clear_neighbors(self) -> int:
        """Remove all cached neighbors and return the count cleared."""
        async with self._transition_lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    @staticmethod
    def _local_public_key_hex() -> str | None:
        """Return the local identity when key export has completed, if any."""
        try:
            from app.keystore import get_public_key

            public_key = get_public_key()
        except Exception:
            logger.debug("Could not resolve local identity for neighbor reporting", exc_info=True)
            return None
        if public_key is None or len(public_key) != 32:
            return None
        return public_key.hex().lower()

    # ── Manual operations ──────────────────────────────────────────────

    async def start_manual_discovery(self, config_id: str) -> dict[str, Any]:
        """Start (or join) the canonical 60-second zero-hop discovery window."""
        self._require_registered_module(config_id)
        async with self._transition_lock:
            if self._scope_active:
                return {"status": "active", "message": "A scope snapshot is already in progress"}
            if self._discovery_tag is not None:
                self._discovery_manual = True
                return self._refresh_status("joined")

        await self._begin_discovery(periodic=False, target_ids=set(self._modules))
        return self._refresh_status("started")

    async def start_manual_snapshot(self, config_id: str) -> dict[str, Any]:
        """Publish a scope snapshot now, or queue it behind an active refresh."""
        self._require_registered_module(config_id)
        if not self._has_connected_target(set(self._modules)):
            raise NeighborReporterError("Community MQTT bridge is not running")

        async with self._transition_lock:
            if self._scope_active:
                self._scope_target_ids.update(
                    self._eligible_publish_target_ids(set(self._modules), include_manual=True)
                )
                return {"status": "active", "message": "Scope snapshot already in progress"}
            if self._publishing_snapshot:
                return {
                    "status": "active",
                    "message": "Previous scope snapshot is still publishing",
                }
            if self._discovery_tag is not None:
                self._discovery_manual = True
                self._discovery_manual_scope = True
                self._discovery_target_ids.update(set(self._modules))
                return self._refresh_status("queued")

        await self._begin_scope_queries(target_ids=set(self._modules), periodic=False, manual=True)
        return {"status": "started", "message": "Scope snapshot started"}

    def _require_registered_module(self, config_id: str) -> CommunityNeighborModule:
        module = self._modules.get(config_id)
        if module is None:
            raise NeighborReporterError("Community MQTT integration is disabled or not running")
        return module

    def _refresh_status(self, status: str) -> dict[str, Any]:
        remaining = 0
        if self._discovery_deadline is not None:
            remaining = max(0, int(self._discovery_deadline - _monotonic() + 0.999))
        return {
            "status": status,
            "message": f"Zero-hop discovery active ({remaining}s remaining)",
            "remaining_seconds": remaining,
        }

    # ── Active zero-hop discovery ──────────────────────────────────────

    def _next_tag(self) -> int:
        candidate = _wall_time() & 0xFFFFFFFF
        if candidate == 0 or candidate <= self._last_tag:
            candidate = (self._last_tag + 1) & 0xFFFFFFFF
            if candidate == 0:
                candidate = 1
        self._last_tag = candidate
        return candidate

    async def _begin_discovery(self, *, periodic: bool, target_ids: set[str]) -> None:
        async with self._transition_lock:
            if self._discovery_tag is not None or self._scope_active:
                return
            if periodic and not self._scope_crypto_available():
                self._last_publish_result = "failed"
                self._schedule_next_periodic_cycle()
                raise NeighborReporterError(
                    "Periodic neighbor reporting requires the radio private key"
                )
            tag = self._next_tag()
            self._discovery_tag = tag
            # Reserve the workflow while waiting for the shared radio lock,
            # but do not consume the canonical 60-second RF collection window
            # before the request has actually been handed to the radio.
            self._discovery_deadline = None
            self._early_discovery_responses = []
            self._discovery_periodic = periodic
            self._discovery_manual = not periodic
            self._discovery_target_ids = set(target_ids)
            self._discovery_manual_scope = False

        try:
            from app.services.radio_runtime import radio_runtime

            async with radio_runtime.radio_operation(
                "community_neighbor_discovery", pause_polling=True, suspend_auto_fetch=True
            ) as mc:
                subscription = mc.subscribe(
                    EventType.DISCOVER_RESPONSE, self._on_discovery_response
                )
                async with self._transition_lock:
                    # The workflow may have been cancelled while waiting for the
                    # shared radio lock.  Avoid leaving a stray listener behind.
                    if self._discovery_tag != tag:
                        subscription.unsubscribe()
                        return
                    self._discovery_subscription = subscription

                result = await mc.commands.send_node_discover_req(
                    1 << 2,
                    prefix_only=False,
                    tag=tag,
                    since=0,
                )
                if result is None or result.type == EventType.ERROR:
                    raise NeighborReporterError("Failed to start zero-hop neighbor discovery")

                async with self._transition_lock:
                    if self._discovery_tag != tag:
                        return
                    self._discovery_deadline = _monotonic() + DISCOVERY_WINDOW_SECONDS
                    early_responses = list(self._early_discovery_responses)
                    self._early_discovery_responses = []

                # Responses can arrive between transmit and the host command's
                # completion event.  They are still part of this collection
                # window, so validate them after atomically arming it.
                for payload in early_responses:
                    await self._observe_discovery_response(payload)
        except Exception as exc:
            async with self._transition_lock:
                if self._discovery_tag == tag:
                    self._clear_discovery_subscription()
                    self._discovery_tag = None
                    self._discovery_deadline = None
                    self._early_discovery_responses = []
                    self._discovery_periodic = False
                    self._discovery_manual = False
                    self._discovery_manual_scope = False
                    self._discovery_target_ids.clear()
                    if periodic:
                        self._schedule_next_periodic_cycle()
            if isinstance(exc, NeighborReporterError):
                raise
            raise NeighborReporterError(
                f"Failed to start zero-hop neighbor discovery: {exc}"
            ) from exc

    async def _on_discovery_response(self, event: Any) -> None:
        """Validate and merge a matching direct repeater discovery response."""
        payload = getattr(event, "payload", {})
        if not isinstance(payload, dict):
            return
        await self._observe_discovery_response(payload)

    async def _observe_discovery_response(self, payload: dict[str, Any]) -> None:
        """Process one discovery payload, buffering only the send handoff race."""
        public_key: str | None = None

        async with self._transition_lock:
            tag = self._discovery_tag
            deadline = self._discovery_deadline
            if tag is None:
                return
            path_byte = payload.get("path_len")
            if (
                payload.get("node_type") != 2
                or isinstance(path_byte, bool)
                or not isinstance(path_byte, int)
                or not 0 <= path_byte <= 0xFF
                or path_byte >> 6 == 3
                or path_byte & 0x3F
            ):
                return
            event_tag = payload.get("tag")
            expected_tag = tag.to_bytes(4, "little", signed=False).hex()
            if not isinstance(event_tag, str) or event_tag.lower() != expected_tag:
                return
            candidate_key = payload.get("pubkey")
            if not isinstance(candidate_key, str) or len(candidate_key) != 64:
                return
            try:
                if len(bytes.fromhex(candidate_key)) != 32:
                    return
            except ValueError:
                return
            if deadline is None:
                self._remember_early_discovery_response(payload)
                return
            if _monotonic() >= deadline:
                return
            public_key = candidate_key

        try:
            from app.keystore import get_public_key

            if public_key is None:
                return
            own_key = get_public_key()
            if own_key is not None and public_key.lower() == own_key.hex().lower():
                return
        except Exception:
            logger.debug("Could not resolve local identity for discovery response", exc_info=True)

        await self.put_neighbor(
            public_key,
            advert_timestamp=_wall_time(),
            measured_snr=payload.get("SNR"),
        )

    def _remember_early_discovery_response(self, payload: dict[str, Any]) -> None:
        """Keep a bounded copy of matching events received during transmit."""
        self._early_discovery_responses.append(dict(payload))
        del self._early_discovery_responses[:-64]

    async def _observe_scope_response_packet(
        self, payload: bytes, hop_count: int, route_type: int
    ) -> None:
        """Match one raw encrypted direct response through the temporary overlay.

        The normal contact table deliberately remains untouched: cached direct
        repeaters are not necessarily contacts and adding them would consume
        finite radio slots.  Instead, while a scope batch is active we try only
        in-flight identities whose compact source hash matches the packet, then
        accept it only after authenticated decryption and an exact request-tag
        match.  Normal ACL traffic is merely observed, never intercepted.
        """
        if route_type != 0x02 or hop_count != 0 or len(payload) < 20:
            return

        try:
            from app.keystore import get_private_key, get_public_key

            private_key = get_private_key()
            public_key = get_public_key()
        except Exception:
            logger.debug("Could not load local key for neighbor response overlay", exc_info=True)
            return
        if private_key is None or public_key is None or len(public_key) != 32:
            return
        if payload[0] != public_key[0]:
            return

        source_hash = payload[1]
        candidates: list[ScopeQueryEntry] = []
        has_unassigned_candidate = False
        async with self._transition_lock:
            if not self._scope_active:
                return
            for entry in self._scope_entries:
                if entry.status != "pending":
                    continue
                try:
                    candidate_key = bytes.fromhex(entry.public_key)
                except ValueError:
                    continue
                if candidate_key[0] != source_hash:
                    continue
                if entry.request_tag is None:
                    has_unassigned_candidate = True
                else:
                    candidates.append(entry)
            if not candidates and not has_unassigned_candidate:
                return

        for entry in candidates:
            try:
                shared_secret = derive_shared_secret(private_key, bytes.fromhex(entry.public_key))
            except Exception:
                continue
            plaintext = decrypt_response_payload(payload, shared_secret)
            if plaintext is None:
                continue
            if await self._accept_scope_response(entry, plaintext):
                return

        # Do not lose a response that arrived while its host-side MSG_SENT
        # acknowledgement was still being delivered.  It will be retried once
        # that request's expected-ack tag is known.
        if has_unassigned_candidate:
            async with self._transition_lock:
                if self._scope_active:
                    self._remember_early_scope_response(payload)

    async def _accept_scope_response(self, entry: ScopeQueryEntry, plaintext: bytes) -> bool:
        """Validate and record a decrypted response, returning whether it matched."""
        if len(plaintext) < 8:
            return False

        should_finish = False
        async with self._transition_lock:
            if (
                not self._scope_active
                or entry.status != "pending"
                or entry.request_tag is None
                or plaintext[:4].hex().lower() != entry.request_tag
            ):
                return False
            entry.scopes = _bounded_scope_text(plaintext[8:])
            entry.status = "responded"
            should_finish = all(item.status != "pending" for item in self._scope_entries)

        if should_finish:
            await self._finish_scope_queries(timeout=False)
        return True

    def _remember_early_scope_response(self, payload: bytes) -> None:
        if payload in self._early_scope_responses:
            return
        self._early_scope_responses.append(payload)
        del self._early_scope_responses[:-64]

    async def _replay_early_scope_responses(self) -> None:
        """Retry raw responses buffered during command/MSG_SENT handoff."""
        async with self._transition_lock:
            if not self._scope_active or not self._early_scope_responses:
                return
            packets = list(self._early_scope_responses)
            self._early_scope_responses.clear()
        for payload in packets:
            await self._observe_scope_response_packet(payload, 0, 0x02)

    # ── Anonymous scope query batch ────────────────────────────────────

    async def _begin_scope_queries(
        self,
        *,
        target_ids: set[str],
        periodic: bool,
        manual: bool = False,
    ) -> None:
        local_key = self._local_public_key_hex()
        async with self._transition_lock:
            if self._scope_active:
                self._scope_target_ids.update(target_ids)
                return
            if self._publishing_snapshot:
                if periodic:
                    self._next_periodic_at = _monotonic()
                    return
                raise NeighborReporterError("Previous scope snapshot is still publishing")

            eligible_targets = self._eligible_publish_target_ids(target_ids, include_manual=manual)
            if not self._has_connected_target(eligible_targets):
                if manual:
                    self._last_publish_result = "failed"
                if periodic:
                    # A periodic run remains due until a broker is available.
                    # Do not postpone it for a full reporting interval because
                    # the connection dropped during the discovery window.
                    self._next_periodic_at = _monotonic()
                if manual:
                    raise NeighborReporterError("Community MQTT bridge is not running")
                return

            # Packets may arrive before radio key export completes.  Once the
            # local identity is available, prune any such transient self-advert
            # before freezing query entries so the observer can never query or
            # publish itself as a neighbor.
            if local_key is not None:
                self._cache.pop(local_key, None)

            entries = [
                ScopeQueryEntry(
                    public_key=entry.public_key,
                    heard_timestamp=entry.heard_timestamp,
                    snr_q4=entry.snr_q4,
                )
                for entry in self._cache.values()
                if entry.heard_timestamp > 0
            ]
            if entries and not self._scope_crypto_available():
                self._last_publish_result = "failed"
                if periodic:
                    self._schedule_next_periodic_cycle()
                if manual:
                    raise NeighborReporterError(
                        "Neighbor scope discovery requires the radio private key"
                    )
                return
            self._scope_active = True
            # Reserve entries before the radio lock is acquired so raw replies
            # can be matched as soon as their host-side tags arrive.  The
            # shared 30-second response deadline starts only after this one
            # pass has handed every request to the radio.
            self._scope_deadline = None
            self._scope_entries = entries
            self._early_scope_responses = []
            self._scope_periodic = periodic
            # Keep every enabled eligible slot in the frozen handoff.  A slot
            # that reconnects during the shared 30-second RF phase must still
            # receive this snapshot if it is connected when publication starts.
            self._scope_target_ids = set(eligible_targets)

        # Zero neighbours is a valid immediate snapshot; do not wait for a
        # deadline or touch the radio.
        if not entries:
            await self._finish_scope_queries(timeout=False)
            return

        try:
            from app.services.radio_runtime import radio_runtime

            async with radio_runtime.radio_operation(
                "community_neighbor_scopes", pause_polling=True, suspend_auto_fetch=True
            ) as mc:
                # The radio host protocol acknowledges command queueing one at
                # a time.  Complete this one pass first; only then begin the
                # shared 30-second response window, so waiting for the radio
                # lock or host queue never steals RF reply time.
                for entry in entries:
                    result = await self._send_regions_request(mc, entry.public_key)
                    if result is None or result.type != EventType.MSG_SENT:
                        entry.status = "send_failed"
                        continue
                    expected_ack = (
                        result.payload.get("expected_ack")
                        if isinstance(result.payload, dict)
                        else None
                    )
                    if not isinstance(expected_ack, (bytes, bytearray)) or len(expected_ack) != 4:
                        entry.status = "send_failed"
                        continue
                    entry.request_tag = bytes(expected_ack).hex().lower()
                    async with self._transition_lock:
                        if not self._scope_active:
                            return
                    # A tag only has to identify the request for this peer. The
                    # authenticated sender identity disambiguates equal tags
                    # used by different neighbors, so no global tag map is
                    # needed.
                    await self._replay_early_scope_responses()
                    async with self._transition_lock:
                        if not self._scope_active:
                            return

                async with self._transition_lock:
                    if self._scope_active and any(entry.status == "pending" for entry in entries):
                        self._scope_deadline = _monotonic() + SCOPE_QUERY_TIMEOUT_SECONDS
        except Exception:
            logger.warning("Community neighbor scope request batch failed", exc_info=True)
            for entry in entries:
                if entry.status == "pending":
                    entry.status = "send_failed"

        if all(entry.status != "pending" for entry in entries):
            await self._finish_scope_queries(timeout=False)

    async def _send_regions_request(self, mc: Any, public_key: str) -> Any:
        """Queue one firmware-authenticated zero-hop anonymous regions request.

        ``CMD_SEND_ANON_REQ`` receives the full target identity and an empty
        reverse-path descriptor.  Firmware derives the pairwise secret, writes
        the canonical six-byte inner payload, and later returns the request tag
        as ``expected_ack`` in ``MSG_SENT``.
        """
        try:
            target = bytes.fromhex(public_key)
        except ValueError:
            return None
        command = bytes((_SEND_ANON_REQUEST_COMMAND,)) + target + bytes((_ANON_REGIONS_REQUEST, 0))
        try:
            return await mc.commands.send(
                command,
                [EventType.MSG_SENT, EventType.ERROR],
                timeout=1.0,
            )
        except Exception:
            logger.debug(
                "Could not queue anonymous regions request for %s", public_key[:12], exc_info=True
            )
            return None

    async def _finish_scope_queries(self, *, timeout: bool) -> None:
        """Freeze outcomes, build canonical JSON, and publish to all targets."""
        async with self._transition_lock:
            if not self._scope_active:
                return
            if not timeout and any(entry.status == "pending" for entry in self._scope_entries):
                return
            if timeout:
                for entry in self._scope_entries:
                    if entry.status == "pending":
                        entry.status = "timeout"

            entries = list(self._scope_entries)
            target_ids = set(self._scope_target_ids)
            periodic = self._scope_periodic
            self._scope_active = False
            self._scope_deadline = None
            self._scope_entries = []
            self._early_scope_responses = []
            self._scope_target_ids.clear()
            self._scope_periodic = False
            self._publishing_snapshot = True
            if periodic:
                self._schedule_next_periodic_cycle()

        accepted = False
        try:
            serialized = self._build_snapshot(entries, target_ids)
            if serialized is None:
                return

            for config_id in sorted(target_ids):
                module = self._modules.get(config_id)
                if module is None or not module.neighbor_publisher_connected:
                    continue
                try:
                    accepted = (await module.publish_neighbor_snapshot(serialized)) or accepted
                except Exception:
                    logger.warning(
                        "Community neighbor publish failed for %s", config_id, exc_info=True
                    )
        except Exception:
            logger.exception("Community neighbor snapshot handoff failed")
        finally:
            async with self._transition_lock:
                self._last_publish_result = "ok" if accepted else "failed"
                self._publishing_snapshot = False

    # ── Snapshot construction ──────────────────────────────────────────

    def _build_snapshot(self, entries: list[ScopeQueryEntry], target_ids: set[str]) -> str | None:
        try:
            from app.keystore import get_public_key
            from app.services.radio_runtime import radio_runtime

            public_key = get_public_key()
            if public_key is None or len(public_key) != 32:
                logger.warning(
                    "Cannot build Community MQTT neighbor snapshot without local public key"
                )
                return None

            metadata_module = self._metadata_module(target_ids)
            config = metadata_module.config if metadata_module is not None else {}
            origin = normalize_neighbor_origin(config.get("neighbor_origin"))
            if not origin and radio_runtime.meshcore and radio_runtime.meshcore.self_info:
                origin = normalize_neighbor_origin(radio_runtime.meshcore.self_info.get("name"))
            origin = origin or "MeshCore Device"
            self_scopes = normalize_self_scopes(config.get("neighbor_self_scopes", ""))
        except (ValueError, TypeError):
            logger.warning(
                "Cannot build Community MQTT neighbor snapshot: invalid local scope config",
                exc_info=True,
            )
            return None
        except Exception:
            logger.warning("Cannot build Community MQTT neighbor snapshot", exc_info=True)
            return None

        now = _wall_time()
        local_key_hex = public_key.hex().lower()
        neighbor_data = [
            {
                "pubkey": entry.public_key.upper(),
                "snr": entry.snr_q4 / 4.0,
                "heard_secs_ago": max(0, now - entry.heard_timestamp),
                "scopes": entry.scopes,
                "status": entry.status,
            }
            for entry in entries
            if entry.public_key.lower() != local_key_hex
        ]
        neighbor_data.sort(
            key=lambda item: (
                item["heard_secs_ago"],
                -float(item["snr"]),
                str(item["pubkey"]),
            )
        )

        root: dict[str, Any] = {
            "timestamp": _format_snapshot_timestamp(now),
            "origin": origin,
            "origin_id": public_key.hex().upper(),
            "self": {"scopes": self_scopes},
            "neighbors": [],
        }

        serialized = self._serialize_snapshot(root)
        if serialized is None:
            return None
        for neighbor in neighbor_data:
            root["neighbors"].append(neighbor)
            candidate = self._serialize_snapshot(root)
            if candidate is None:
                root["neighbors"].pop()
                break
            serialized = candidate
        return serialized

    @staticmethod
    def _serialize_snapshot(snapshot: dict[str, Any]) -> str | None:
        try:
            serialized = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
            encoded = serialized.encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            return None
        # The fixed-buffer interoperability profile reserves no byte for a
        # document that reaches the buffer boundary: accepted JSON must be
        # strictly smaller than the 10,240-byte snapshot buffer.
        if not encoded or len(encoded) >= MAX_SNAPSHOT_BYTES:
            return None
        return serialized

    def _metadata_module(self, target_ids: set[str]) -> CommunityNeighborModule | None:
        for config_id in sorted(target_ids):
            module = self._modules.get(config_id)
            if module is not None:
                return module
        for config_id in sorted(self._modules):
            return self._modules[config_id]
        return None

    @staticmethod
    def _scope_crypto_available() -> bool:
        """Whether host-side response authentication can be performed."""
        try:
            from app.keystore import get_private_key, get_public_key

            private_key = get_private_key()
            public_key = get_public_key()
        except Exception:
            logger.debug("Could not resolve local keys for neighbor scope discovery", exc_info=True)
            return False
        return (
            isinstance(private_key, bytes)
            and len(private_key) == 64
            and isinstance(public_key, bytes)
            and len(public_key) == 32
        )

    # ── Scheduling ─────────────────────────────────────────────────────

    async def _run(self) -> None:
        while self._modules:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Community MQTT neighbor reporter tick failed")
            await asyncio.sleep(0.5)

    async def _tick(self) -> None:
        now = _monotonic()
        start_scope: tuple[set[str], bool, bool] | None = None
        start_periodic = False
        scope_timed_out = False
        async with self._transition_lock:
            periodic_ids = self._periodic_target_ids()
            if not periodic_ids:
                self._next_periodic_at = None
                if self._discovery_tag is not None and self._discovery_periodic:
                    if self._discovery_manual:
                        # Keep a manually owned discovery window alive, but
                        # remove periodic ownership so disabling reporting does
                        # not turn a manual discover-only request into a scope
                        # query and MQTT publication at expiry.
                        self._discovery_periodic = False
                    else:
                        self._clear_discovery_subscription()
                        self._discovery_tag = None
                        self._discovery_deadline = None
                        self._early_discovery_responses = []
                        self._discovery_periodic = False
                        self._discovery_manual = False
                        self._discovery_manual_scope = False
                        self._discovery_target_ids.clear()

            if (
                self._discovery_tag is not None
                and self._discovery_deadline is not None
                and now >= self._discovery_deadline
            ):
                target_ids = set(self._discovery_target_ids)
                periodic = self._discovery_periodic
                manual_scope = self._discovery_manual_scope
                self._clear_discovery_subscription()
                self._discovery_tag = None
                self._discovery_deadline = None
                self._early_discovery_responses = []
                self._discovery_periodic = False
                self._discovery_manual = False
                self._discovery_manual_scope = False
                self._discovery_target_ids.clear()
                if manual_scope or periodic:
                    start_scope = (target_ids, periodic, manual_scope)

            # A due periodic cycle joins a manually started collection window
            # rather than wasting an additional discovery broadcast.  The
            # existing window still has the canonical tag/deadline; only its
            # completion ownership changes to include the periodic snapshot.
            if (
                self._discovery_tag is not None
                and not self._discovery_periodic
                and periodic_ids
                and self._next_periodic_at is not None
                and now >= self._next_periodic_at
                and self._has_connected_target(periodic_ids)
            ):
                self._discovery_periodic = True
                self._discovery_target_ids.update(periodic_ids)

            scope_timed_out = (
                self._scope_active
                and self._scope_deadline is not None
                and now >= self._scope_deadline
            )
            if not scope_timed_out and (
                self._discovery_tag is None
                and not self._scope_active
                and not self._publishing_snapshot
                and periodic_ids
                and self._next_periodic_at is not None
                and now >= self._next_periodic_at
                and self._has_connected_target(periodic_ids)
            ):
                start_periodic = True

        if scope_timed_out:
            await self._finish_scope_queries(timeout=True)
            return
        if start_scope is not None:
            target_ids, periodic, manual = start_scope
            try:
                await self._begin_scope_queries(
                    target_ids=target_ids,
                    periodic=periodic,
                    manual=manual,
                )
            except NeighborReporterError:
                logger.debug("Skipping unavailable Community MQTT scope phase", exc_info=True)
            return
        if start_periodic:
            try:
                await self._begin_discovery(periodic=True, target_ids=self._periodic_target_ids())
            except NeighborReporterError:
                logger.debug(
                    "Periodic Community MQTT neighbor refresh could not start", exc_info=True
                )
                async with self._transition_lock:
                    self._schedule_next_periodic_cycle()

    async def _ensure_periodic_schedule(self) -> None:
        async with self._transition_lock:
            if self._periodic_target_ids():
                # A newly enabled periodic reporter is due immediately once a
                # broker connection exists; it does not wait a full day first.
                # When a shorter-interval module joins alongside an existing one,
                # bring the next-run forward so the min-interval policy is
                # honoured from the first cycle onward.
                desired = _monotonic()
                if self._next_periodic_at is None or desired < self._next_periodic_at:
                    self._next_periodic_at = desired
            else:
                self._next_periodic_at = None

    def _periodic_target_ids(self) -> set[str]:
        return {
            config_id
            for config_id, module in self._modules.items()
            if bool(module.config.get("neighbor_reporting_enabled", False))
        }

    def _eligible_publish_target_ids(
        self, target_ids: set[str], *, include_manual: bool
    ) -> set[str]:
        """Return enabled Community slots eligible for this snapshot handoff.

        Connection state is checked only to decide whether a new RF workflow is
        useful and again at publish time.  It must not remove a temporarily
        disconnected target from an already-started snapshot.
        """
        if include_manual:
            candidates = set(target_ids)
        else:
            candidates = self._periodic_target_ids() & set(target_ids)
        return {config_id for config_id in candidates if self._modules.get(config_id) is not None}

    def _has_connected_target(self, target_ids: set[str]) -> bool:
        return any(
            module.neighbor_publisher_connected
            for config_id, module in self._modules.items()
            if config_id in target_ids
        )

    def _periodic_interval_seconds(self) -> float:
        intervals: list[int] = []
        for config_id in self._periodic_target_ids():
            module = self._modules.get(config_id)
            if module is None:
                continue
            try:
                hours = int(
                    module.config.get(
                        "neighbor_reporting_interval_hours", DEFAULT_PERIODIC_INTERVAL_HOURS
                    )
                )
            except (TypeError, ValueError):
                hours = DEFAULT_PERIODIC_INTERVAL_HOURS
            intervals.append(
                max(MIN_PERIODIC_INTERVAL_HOURS, min(MAX_PERIODIC_INTERVAL_HOURS, hours))
            )
        return min(intervals, default=DEFAULT_PERIODIC_INTERVAL_HOURS) * 3600.0

    def _schedule_next_periodic_cycle(self) -> None:
        if self._periodic_target_ids():
            self._next_periodic_at = _monotonic() + self._periodic_interval_seconds()
        else:
            self._next_periodic_at = None

    # ── Diagnostics and cleanup ────────────────────────────────────────

    def status_for(self, config_id: str) -> dict[str, Any]:
        """Return lightweight operational state for the config's UI/API surface."""
        now = _monotonic()
        if self._publishing_snapshot:
            phase = "publishing"
            remaining = None
        elif self._scope_active:
            phase = "scopes"
            remaining = max(0, int((self._scope_deadline or now) - now + 0.999))
        elif self._discovery_tag is not None:
            phase = "refresh"
            remaining = max(0, int((self._discovery_deadline or now) - now + 0.999))
        elif self._next_periodic_at is not None:
            remaining = max(0, int(self._next_periodic_at - now + 0.999))
            # A periodic reporter stays due until a connected MQTT slot lets
            # it open its canonical discovery window.  Keeping this distinct
            # from a future scheduled run makes a disconnected bridge visible
            # to the operator without changing the workflow.
            phase = "due" if remaining == 0 else "scheduled"
        else:
            phase = "idle"
            remaining = None
        module = self._modules.get(config_id)
        return {
            "phase": phase,
            "cache_size": len(self._cache),
            "remaining_seconds": remaining,
            "periodic_enabled": bool(
                module and module.config.get("neighbor_reporting_enabled", False)
            ),
            "last_publish_result": self._last_publish_result,
        }

    def _clear_discovery_subscription(self) -> None:
        if self._discovery_subscription is not None:
            try:
                self._discovery_subscription.unsubscribe()
            except Exception:
                logger.debug("Could not remove neighbor discovery listener", exc_info=True)
        self._discovery_subscription = None


# One coordinator is deliberately shared across every Community MQTT fanout
# config.  See module docstring for why this is not module-local state.
community_neighbor_reporter = CommunityNeighborReporter()
