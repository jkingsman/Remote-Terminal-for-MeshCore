import asyncio
import logging
import random
import time
from contextlib import suppress
from typing import Literal, TypeAlias

from fastapi import APIRouter, HTTPException
from meshcore import EventType
from pydantic import BaseModel, Field

from app.models import (
    CONTACT_TYPE_REPEATER,
    Contact,
    ContactUpsert,
    RadioDiscoveryRequest,
    RadioDiscoveryResponse,
    RadioDiscoveryResult,
    RadioRegionDiscoveryRepeater,
    RadioRegionDiscoveryRequest,
    RadioRegionDiscoveryResponse,
    RadioTraceHopRequest,
    RadioTraceNode,
    RadioTraceRequest,
    RadioTraceResponse,
)
from app.radio_sync import send_advertisement as do_send_advertisement
from app.radio_sync import sync_radio_time
from app.repository import ContactRepository
from app.routers.repeaters import request_anon_region_names
from app.routers.server_control import _monotonic
from app.services.contact_reconciliation import (
    promote_prefix_contacts_for_contact,
    reconcile_contact_messages,
)
from app.services.radio_commands import (
    KeystoreRefreshError,
    PathHashModeUnsupportedError,
    RadioCommandRejectedError,
    RepeatModeUnsupportedError,
    apply_radio_config_update,
    import_private_key_and_refresh_keystore,
)
from app.services.radio_runtime import radio_runtime as radio_manager
from app.websocket import broadcast_event, broadcast_health

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/radio", tags=["radio"])

AdvertLocationSource = Literal["off", "current"]
RadioAdvertMode = Literal["flood", "zero_hop"]
DiscoveryNodeType: TypeAlias = Literal["repeater", "sensor"]
DISCOVERY_WINDOW_SECONDS = 8.0
_DISCOVERY_TARGET_BITS = {
    "repeaters": 1 << 2,
    "sensors": 1 << 4,
    "all": (1 << 2) | (1 << 4),
}
_DISCOVERY_NODE_TYPES: dict[int, DiscoveryNodeType] = {
    2: "repeater",
    4: "sensor",
}
TRACE_WAIT_TIMEOUT_SECONDS = 45.0
TRACE_DEFAULT_TIMEOUT_SECONDS = 15.0
TRACE_TIMEOUT_MIN_SECONDS = 5.0
TRACE_TIMEOUT_MAX_SECONDS = 30.0
TRACE_TIMEOUT_MARGIN = 1.2
TRACE_HASH_FLAGS = {1: 0, 2: 1, 4: 2}


async def _prepare_connected(*, broadcast_on_success: bool) -> bool:
    return await radio_manager.prepare_connected(broadcast_on_success=broadcast_on_success)


async def _reconnect_and_prepare(*, broadcast_on_success: bool) -> bool:
    return await radio_manager.reconnect_and_prepare(
        broadcast_on_success=broadcast_on_success,
    )


class RadioSettings(BaseModel):
    freq: float = Field(description="Frequency in MHz")
    bw: float = Field(description="Bandwidth in kHz")
    sf: int = Field(description="Spreading factor (7-12)")
    cr: int = Field(description="Coding rate (1-4)")


class FreqRange(BaseModel):
    min_mhz: float
    max_mhz: float


class RadioConfigResponse(BaseModel):
    public_key: str = Field(description="Public key (64-char hex)")
    name: str
    lat: float
    lon: float
    tx_power: int = Field(description="Transmit power in dBm")
    max_tx_power: int = Field(description="Maximum transmit power in dBm")
    radio: RadioSettings
    path_hash_mode: int = Field(
        default=0, description="Path hash mode (0=1-byte, 1=2-byte, 2=3-byte)"
    )
    path_hash_mode_supported: bool = Field(
        default=False, description="Whether firmware supports path hash mode setting"
    )
    repeat_enabled: bool = Field(
        default=False,
        description="Whether the companion rebroadcasts other nodes' packets (repeat mode)",
    )
    repeat_supported: bool = Field(
        default=False, description="Whether firmware supports the repeat mode setting"
    )
    allowed_repeat_freqs: list[FreqRange] = Field(
        default_factory=list,
        description="Frequency ranges (MHz) where firmware allows repeat mode; compiled into firmware",
    )
    advert_location_source: AdvertLocationSource = Field(
        default="current",
        description="Whether adverts include the node's current location state",
    )
    multi_acks_enabled: bool = Field(
        default=False,
        description="Whether the radio sends an extra direct ACK transmission",
    )
    telemetry_mode_base: int = Field(
        default=0,
        description="Base telemetry sharing mode (0=deny, 1=per-contact, 2=allow-all)",
    )
    telemetry_mode_loc: int = Field(
        default=0,
        description="Location telemetry sharing mode (0=deny, 1=per-contact, 2=allow-all)",
    )
    telemetry_mode_env: int = Field(
        default=0,
        description="Environment sensor sharing mode (0=deny, 1=per-contact, 2=allow-all)",
    )


class RadioConfigUpdate(BaseModel):
    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    tx_power: int | None = Field(default=None, description="Transmit power in dBm")
    radio: RadioSettings | None = None
    path_hash_mode: int | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Path hash mode (0=1-byte, 1=2-byte, 2=3-byte)",
    )
    repeat_enabled: bool | None = Field(
        default=None,
        description="Whether the companion rebroadcasts other nodes' packets (repeat mode)",
    )
    advert_location_source: AdvertLocationSource | None = Field(
        default=None,
        description="Whether adverts include the node's current location state",
    )
    multi_acks_enabled: bool | None = Field(
        default=None,
        description="Whether the radio sends an extra direct ACK transmission",
    )
    telemetry_mode_base: int | None = Field(
        default=None, ge=0, le=2, description="Base telemetry sharing mode"
    )
    telemetry_mode_loc: int | None = Field(
        default=None, ge=0, le=2, description="Location telemetry sharing mode"
    )
    telemetry_mode_env: int | None = Field(
        default=None, ge=0, le=2, description="Environment sensor sharing mode"
    )


class PrivateKeyUpdate(BaseModel):
    private_key: str = Field(description="Private key as hex string")


class RadioAdvertiseRequest(BaseModel):
    mode: RadioAdvertMode = Field(
        default="flood",
        description="Advertisement mode: flood through repeaters or zero-hop local only",
    )


def _better_signal(first: float | None, second: float | None) -> float | None:
    if first is None:
        return second
    if second is None:
        return first
    return second if second > first else first


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _merge_discovery_result(
    existing: RadioDiscoveryResult | None, event_payload: dict[str, object]
) -> RadioDiscoveryResult | None:
    public_key = event_payload.get("pubkey")
    node_type_code = event_payload.get("node_type")
    if not isinstance(public_key, str) or not public_key:
        return existing
    if not isinstance(node_type_code, int):
        return existing

    node_type = _DISCOVERY_NODE_TYPES.get(node_type_code)
    if node_type is None:
        return existing

    if existing is None:
        return RadioDiscoveryResult(
            public_key=public_key,
            node_type=node_type,
            heard_count=1,
            local_snr=_coerce_float(event_payload.get("SNR")),
            local_rssi=_coerce_int(event_payload.get("RSSI")),
            remote_snr=_coerce_float(event_payload.get("SNR_in")),
        )

    existing.heard_count += 1
    existing.local_snr = _better_signal(existing.local_snr, _coerce_float(event_payload.get("SNR")))
    current_rssi = _coerce_int(event_payload.get("RSSI"))
    if existing.local_rssi is None or (
        current_rssi is not None and current_rssi > existing.local_rssi
    ):
        existing.local_rssi = current_rssi
    existing.remote_snr = _better_signal(
        existing.remote_snr,
        _coerce_float(event_payload.get("SNR_in")),
    )
    return existing


async def _persist_new_discovery_contacts(results: list[RadioDiscoveryResult]) -> None:
    now = int(time.time())
    for result in results:
        existing = await ContactRepository.get_by_key(result.public_key)
        if existing is not None:
            continue

        contact = ContactUpsert(
            public_key=result.public_key,
            type=2 if result.node_type == "repeater" else 4,
            last_seen=now,
            first_seen=now,
            on_radio=False,
        )
        await ContactRepository.upsert(contact)
        promoted_keys = await promote_prefix_contacts_for_contact(
            public_key=result.public_key,
            log=logger,
        )
        await reconcile_contact_messages(
            public_key=result.public_key,
            contact_name=result.name,
            log=logger,
        )
        created = await ContactRepository.get_by_key(result.public_key)
        if created is not None:
            broadcast_event("contact", created.model_dump())
            for old_key in promoted_keys:
                broadcast_event(
                    "contact_resolved",
                    {"previous_public_key": old_key, "contact": created.model_dump()},
                )


async def _attach_known_names(results: list[RadioDiscoveryResult]) -> None:
    """Resolve known contact names for discovery results from the DB."""
    for result in results:
        contact = await ContactRepository.get_by_key(result.public_key)
        if contact is not None and contact.name:
            result.name = contact.name


def _trace_hash_for_key(public_key: str, hop_hash_bytes: int) -> str:
    return public_key[: hop_hash_bytes * 2].lower()


def _trace_timeout_seconds(send_result: object) -> float:
    payload = getattr(send_result, "payload", None) or {}
    suggested_timeout = payload.get("suggested_timeout")
    try:
        if suggested_timeout is None:
            raise TypeError
        timeout_seconds = float(suggested_timeout) / 1000.0 * TRACE_TIMEOUT_MARGIN
    except (TypeError, ValueError):
        timeout_seconds = TRACE_DEFAULT_TIMEOUT_SECONDS
    return max(TRACE_TIMEOUT_MIN_SECONDS, min(TRACE_TIMEOUT_MAX_SECONDS, timeout_seconds))


async def _resolve_trace_hops(
    hops: list[RadioTraceHopRequest], hop_hash_bytes: int
) -> tuple[list[RadioTraceNode], list[str]]:
    trace_nodes: list[RadioTraceNode] = []
    requested_hashes: list[str] = []
    expected_hex_len = hop_hash_bytes * 2

    for hop in hops:
        public_key = hop.public_key.strip().lower() if isinstance(hop.public_key, str) else None
        hop_hex = hop.hop_hex.strip().lower() if isinstance(hop.hop_hex, str) else None
        if bool(public_key) == bool(hop_hex):
            raise HTTPException(
                status_code=400,
                detail="Each trace hop must provide exactly one of public_key or hop_hex",
            )

        if public_key:
            if len(public_key) != 64:
                raise HTTPException(
                    status_code=400,
                    detail="Trace repeater keys must be full 64-character public keys",
                )
            try:
                bytes.fromhex(public_key)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Trace repeater keys must be valid hex public keys",
                ) from exc

            contact = await ContactRepository.get_by_key(public_key)
            if contact is None:
                raise HTTPException(
                    status_code=404, detail=f"Trace repeater not found: {public_key}"
                )
            if contact.type != CONTACT_TYPE_REPEATER:
                raise HTTPException(
                    status_code=400,
                    detail=f"Trace node is not a repeater: {public_key[:12]}",
                )
            requested_hashes.append(_trace_hash_for_key(contact.public_key, hop_hash_bytes))
            trace_nodes.append(
                RadioTraceNode(
                    role="repeater",
                    public_key=contact.public_key,
                    name=contact.name,
                    observed_hash=None,
                    snr=None,
                )
            )
            continue

        assert hop_hex is not None
        if len(hop_hex) != expected_hex_len:
            raise HTTPException(
                status_code=400,
                detail=f"Custom trace hops must be exactly {expected_hex_len} hex characters",
            )
        try:
            bytes.fromhex(hop_hex)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Custom trace hops must be valid hex",
            ) from exc
        requested_hashes.append(hop_hex)
        trace_nodes.append(
            RadioTraceNode(
                role="custom",
                public_key=None,
                name=None,
                observed_hash=hop_hex,
                snr=None,
            )
        )

    return trace_nodes, requested_hashes


@router.get("/config", response_model=RadioConfigResponse)
async def get_radio_config() -> RadioConfigResponse:
    """Get the current radio configuration."""
    mc = radio_manager.require_connected()

    info = mc.self_info
    if not info:
        raise HTTPException(status_code=423, detail="Radio info not available")

    adv_loc_policy = info.get("adv_loc_policy", 1)
    advert_location_source: AdvertLocationSource = "off" if adv_loc_policy == 0 else "current"

    return RadioConfigResponse(
        public_key=info.get("public_key", ""),
        name=info.get("name", ""),
        lat=info.get("adv_lat", 0.0),
        lon=info.get("adv_lon", 0.0),
        tx_power=info.get("tx_power", 0),
        max_tx_power=info.get("max_tx_power", 0),
        radio=RadioSettings(
            freq=info.get("radio_freq", 0.0),
            bw=info.get("radio_bw", 0.0),
            sf=info.get("radio_sf", 0),
            cr=info.get("radio_cr", 0),
        ),
        path_hash_mode=radio_manager.path_hash_mode,
        path_hash_mode_supported=radio_manager.path_hash_mode_supported,
        repeat_enabled=radio_manager.repeat_enabled,
        repeat_supported=radio_manager.repeat_supported,
        allowed_repeat_freqs=[
            FreqRange(min_mhz=lo, max_mhz=hi) for lo, hi in radio_manager.allowed_repeat_freqs
        ],
        advert_location_source=advert_location_source,
        multi_acks_enabled=bool(info.get("multi_acks", 0)),
        telemetry_mode_base=info.get("telemetry_mode_base", 0),
        telemetry_mode_loc=info.get("telemetry_mode_loc", 0),
        telemetry_mode_env=info.get("telemetry_mode_env", 0),
    )


@router.patch("/config", response_model=RadioConfigResponse)
async def update_radio_config(update: RadioConfigUpdate) -> RadioConfigResponse:
    """Update radio configuration. Only provided fields will be updated."""
    radio_manager.require_connected()

    async with radio_manager.radio_operation("update_radio_config") as mc:
        try:
            await apply_radio_config_update(
                mc,
                update,
                path_hash_mode_supported=radio_manager.path_hash_mode_supported,
                set_path_hash_mode=lambda mode: setattr(radio_manager, "path_hash_mode", mode),
                repeat_supported=radio_manager.repeat_supported,
                repeat_enabled=radio_manager.repeat_enabled,
                allowed_repeat_freqs=radio_manager.allowed_repeat_freqs,
                set_repeat_enabled=lambda enabled: setattr(
                    radio_manager, "repeat_enabled", enabled
                ),
                sync_radio_time_fn=sync_radio_time,
            )
        except (PathHashModeUnsupportedError, RepeatModeUnsupportedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RadioCommandRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return await get_radio_config()


@router.get("/private-key")
async def get_private_key() -> dict:
    """Return the in-memory private key (exported from radio on startup).

    Gated behind MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT=true.
    """
    from app.config import settings
    from app.keystore import get_private_key as ks_get

    if not settings.enable_local_private_key_export:
        raise HTTPException(
            status_code=403,
            detail="Private key export is disabled (set MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT=true)",
        )

    key = ks_get()
    if key is None:
        raise HTTPException(
            status_code=404,
            detail="Private key not available (not exported from radio)",
        )
    return {"private_key": key.hex()}


@router.put("/private-key")
async def set_private_key(update: PrivateKeyUpdate) -> dict:
    """Set the radio's private key. This is write-only."""
    radio_manager.require_connected()

    try:
        key_bytes = bytes.fromhex(update.private_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex string for private key") from None

    logger.info("Importing private key")
    async with radio_manager.radio_operation("import_private_key") as mc:
        from app.keystore import export_and_store_private_key

        try:
            await import_private_key_and_refresh_keystore(
                mc,
                key_bytes,
                export_and_store_private_key_fn=export_and_store_private_key,
            )
        except (RadioCommandRejectedError, KeystoreRefreshError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {"status": "ok"}


@router.post("/advertise")
async def send_advertisement(request: RadioAdvertiseRequest | None = None) -> dict:
    """Send an advertisement to announce presence on the mesh.

    Manual advertisement requests always send immediately. Flood adverts update
    the shared flood-advert timing state used by periodic/startup advertising;
    zero-hop adverts currently do not.

    Returns:
        status: "ok" if sent successfully
    """
    radio_manager.require_connected()
    mode: RadioAdvertMode = request.mode if request is not None else "flood"

    logger.info("Sending %s advertisement", mode.replace("_", "-"))
    async with radio_manager.radio_operation("manual_advertisement") as mc:
        success = await do_send_advertisement(mc, force=True, mode=mode)

    if not success:
        raise HTTPException(status_code=422, detail=f"Failed to send {mode} advertisement")

    return {"status": "ok"}


@router.post("/discover", response_model=RadioDiscoveryResponse)
async def discover_mesh(request: RadioDiscoveryRequest) -> RadioDiscoveryResponse:
    """Run a short node-discovery sweep from the local radio."""
    radio_manager.require_connected()

    target_bits = _DISCOVERY_TARGET_BITS[request.target]
    tag = random.randint(1, 0xFFFFFFFF)
    tag_hex = tag.to_bytes(4, "little", signed=False).hex()
    events: asyncio.Queue = asyncio.Queue()

    async with radio_manager.radio_operation(
        "discover_mesh",
        pause_polling=True,
        suspend_auto_fetch=True,
    ) as mc:
        subscription = mc.subscribe(
            EventType.DISCOVER_RESPONSE,
            lambda event: events.put_nowait(event),
            {"tag": tag_hex},
        )
        try:
            send_result = await mc.commands.send_node_discover_req(
                target_bits,
                prefix_only=False,
                tag=tag,
            )
            if send_result is None or send_result.type == EventType.ERROR:
                raise HTTPException(status_code=422, detail="Failed to start mesh discovery")

            deadline = _monotonic() + DISCOVERY_WINDOW_SECONDS
            results_by_key: dict[str, RadioDiscoveryResult] = {}

            while True:
                remaining = deadline - _monotonic()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(events.get(), timeout=remaining)
                except TimeoutError:
                    break

                merged = _merge_discovery_result(
                    results_by_key.get(event.payload.get("pubkey")),
                    event.payload,
                )
                if merged is not None:
                    results_by_key[merged.public_key] = merged
        finally:
            subscription.unsubscribe()

    results = sorted(
        results_by_key.values(),
        key=lambda item: (
            item.node_type,
            -(item.local_snr if item.local_snr is not None else -999.0),
            item.public_key,
        ),
    )
    await _persist_new_discovery_contacts(results)
    await _attach_known_names(results)
    return RadioDiscoveryResponse(
        target=request.target,
        duration_seconds=DISCOVERY_WINDOW_SECONDS,
        results=results,
    )


def _dedupe_region_names(names: list[str]) -> list[str]:
    """Dedupe region names case-insensitively, preserving first-seen order.

    Drops the wildcard ``*`` (means "allows unscoped flood", not a nameable
    region) and blanks so the result is safe to merge into ``known_regions``.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = (raw or "").strip()
        if not name or name == "*" or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    return out


async def _resolve_region_discovery_targets(request: RadioRegionDiscoveryRequest) -> list[Contact]:
    """Resolve the repeater contacts to sweep for regions.

    Explicit ``public_keys`` win (filtered to known repeaters); otherwise fall
    back to the most recently seen repeater contacts. Capped at
    ``max_repeaters`` either way.
    """
    if request.public_keys:
        targets: list[Contact] = []
        seen: set[str] = set()
        for raw in request.public_keys:
            key = (raw or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            contact = await ContactRepository.get_by_key(key)
            if contact is None or contact.type != CONTACT_TYPE_REPEATER:
                continue
            targets.append(contact)
            if len(targets) >= request.max_repeaters:
                break
        return targets
    return await ContactRepository.get_repeaters_by_recent(limit=request.max_repeaters)


@router.post("/discover-regions", response_model=RadioRegionDiscoveryResponse)
async def discover_regions(
    request: RadioRegionDiscoveryRequest,
) -> RadioRegionDiscoveryResponse:
    """Sweep nearby repeaters for their flood-allowed region names.

    Sends the guest-accessible anon regions request to each target repeater and
    aggregates the flood-allowed names into a deduplicated union that the
    operator can merge into ``known_regions``. The request is direct-routed, so
    only repeaters in range answer; unreachable ones are reported as
    ``answered=false``. This is the radio-wide companion to the per-repeater
    admin regions pane (issue #309).
    """
    radio_manager.require_connected()

    targets = await _resolve_region_discovery_targets(request)
    if not targets:
        return RadioRegionDiscoveryResponse(
            repeaters_queried=0, repeaters_answered=0, regions=[], results=[]
        )

    results: list[RadioRegionDiscoveryRepeater] = []
    # One radio_operation for the whole sweep: keeps polling paused once and
    # runs the per-repeater anon requests serially behind the single radio lock.
    async with radio_manager.radio_operation(
        "discover_regions", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        for contact in targets:
            names = await request_anon_region_names(mc, contact)
            results.append(
                RadioRegionDiscoveryRepeater(
                    public_key=contact.public_key,
                    name=contact.name,
                    answered=names is not None,
                    regions=_dedupe_region_names(names or []),
                )
            )

    union = _dedupe_region_names([name for result in results for name in result.regions])
    return RadioRegionDiscoveryResponse(
        repeaters_queried=len(targets),
        repeaters_answered=sum(1 for result in results if result.answered),
        regions=union,
        results=results,
    )


@router.post("/trace", response_model=RadioTraceResponse)
async def trace_path(request: RadioTraceRequest) -> RadioTraceResponse:
    """Send a multi-hop trace loop through known repeaters and back to the local radio."""
    radio_manager.require_connected()
    trace_nodes, requested_hashes = await _resolve_trace_hops(request.hops, request.hop_hash_bytes)

    tag = random.randint(1, 0xFFFFFFFF)
    trace_flags = TRACE_HASH_FLAGS[request.hop_hash_bytes]

    async with radio_manager.radio_operation("radio_trace", pause_polling=True) as mc:
        local_public_key = str((mc.self_info or {}).get("public_key") or "").lower()
        if len(local_public_key) != 64:
            raise HTTPException(status_code=423, detail="Local radio public key is unavailable")
        local_name = (mc.self_info or {}).get("name")

        response_task = asyncio.create_task(
            mc.wait_for_event(
                EventType.TRACE_DATA,
                attribute_filters={"tag": tag},
                timeout=TRACE_WAIT_TIMEOUT_SECONDS,
            )
        )
        try:
            send_result = await mc.commands.send_trace(
                path=",".join(requested_hashes),
                tag=tag,
                flags=trace_flags,
            )
            if send_result is None or send_result.type == EventType.ERROR:
                raise HTTPException(status_code=422, detail="Failed to send trace")

            timeout_seconds = _trace_timeout_seconds(send_result)
            try:
                event = await asyncio.wait_for(response_task, timeout=timeout_seconds)
            except TimeoutError as exc:
                raise HTTPException(status_code=408, detail="No trace response heard") from exc
        finally:
            if not response_task.done():
                response_task.cancel()
            with suppress(asyncio.CancelledError):
                await response_task

    if event is None:
        raise HTTPException(status_code=408, detail="No trace response heard")

    payload = event.payload if isinstance(event.payload, dict) else {}
    path_len = payload.get("path_len")
    if not isinstance(path_len, int):
        raise HTTPException(status_code=422, detail="Trace response was malformed")

    raw_path = payload.get("path")
    path_nodes = raw_path if isinstance(raw_path, list) else []
    final_local_node = (
        path_nodes[-1]
        if path_nodes
        and isinstance(path_nodes[-1], dict)
        and not isinstance(path_nodes[-1].get("hash"), str)
        else None
    )
    hashed_nodes = path_nodes[:-1] if final_local_node is not None else path_nodes

    if len(hashed_nodes) < len(trace_nodes):
        raise HTTPException(status_code=422, detail="Trace response was incomplete")

    nodes: list[RadioTraceNode] = []
    for index, trace_node in enumerate(trace_nodes):
        observed = hashed_nodes[index] if index < len(hashed_nodes) else {}
        observed_hash = observed.get("hash") if isinstance(observed, dict) else None
        observed_snr = observed.get("snr") if isinstance(observed, dict) else None
        nodes.append(
            RadioTraceNode(
                role=trace_node.role,
                public_key=trace_node.public_key,
                name=trace_node.name,
                observed_hash=(
                    observed_hash if isinstance(observed_hash, str) else trace_node.observed_hash
                ),
                snr=float(observed_snr) if isinstance(observed_snr, (int, float)) else None,
            )
        )

    terminal_snr_value = final_local_node.get("snr") if isinstance(final_local_node, dict) else None
    nodes.append(
        RadioTraceNode(
            role="local",
            public_key=local_public_key,
            name=local_name if isinstance(local_name, str) and local_name else None,
            observed_hash=None,
            snr=float(terminal_snr_value) if isinstance(terminal_snr_value, (int, float)) else None,
        )
    )

    return RadioTraceResponse(
        path_len=path_len,
        timeout_seconds=timeout_seconds,
        nodes=nodes,
    )


async def _attempt_reconnect() -> dict:
    """Shared reconnection logic for reboot and reconnect endpoints."""
    radio_manager.resume_connection()

    if radio_manager.is_reconnecting:
        return {
            "status": "pending",
            "message": "Reconnection already in progress",
            "connected": False,
        }

    try:
        success = await _reconnect_and_prepare(broadcast_on_success=True)
    except Exception as e:
        logger.exception("Post-connect setup failed after reconnect")
        raise HTTPException(
            status_code=423,
            detail=f"Radio connected but setup failed: {e}",
        ) from e

    if not success:
        raise HTTPException(
            status_code=423, detail="Failed to reconnect. Check radio connection and power."
        )

    return {"status": "ok", "message": "Reconnected successfully", "connected": True}


@router.post("/disconnect")
async def disconnect_radio() -> dict:
    """Disconnect from the radio and pause automatic reconnect attempts."""
    logger.info("Manual radio disconnect requested")
    await radio_manager.pause_connection()
    broadcast_health(False, radio_manager.connection_info)
    return {
        "status": "ok",
        "message": "Disconnected. Automatic reconnect is paused.",
        "connected": False,
        "paused": True,
    }


@router.post("/reboot")
async def reboot_radio() -> dict:
    """Reboot the radio, or reconnect if not currently connected.

    If connected: sends reboot command, connection will temporarily drop and auto-reconnect.
    If not connected: attempts to reconnect (same as /reconnect endpoint).
    """
    if radio_manager.is_connected:
        logger.info("Rebooting radio")
        async with radio_manager.radio_operation("reboot_radio") as mc:
            await mc.commands.reboot()
        return {
            "status": "ok",
            "message": "Reboot command sent. Radio will reconnect automatically.",
        }

    logger.info("Radio not connected, attempting reconnect")
    return await _attempt_reconnect()


@router.post("/reconnect")
async def reconnect_radio() -> dict:
    """Attempt to reconnect to the radio.

    This will try to re-establish connection to the radio, with auto-detection
    if no specific port is configured. Useful when the radio has been disconnected
    or power-cycled.
    """
    if radio_manager.is_connected:
        if radio_manager.is_setup_complete:
            return {"status": "ok", "message": "Already connected", "connected": True}

        logger.info("Radio connected but setup incomplete, retrying setup")
        try:
            if not await _prepare_connected(broadcast_on_success=True):
                raise HTTPException(status_code=423, detail="Radio connection is paused")
            return {"status": "ok", "message": "Setup completed", "connected": True}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Post-connect setup failed")
            raise HTTPException(
                status_code=423,
                detail=f"Radio connected but setup failed: {e}",
            ) from e

    logger.info("Manual reconnect requested")
    return await _attempt_reconnect()
