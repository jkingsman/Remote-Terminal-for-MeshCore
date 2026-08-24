import asyncio
import logging
import random
import time
from contextlib import suppress

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from meshcore import EventType
from pydantic import BaseModel, Field

from app.models import (
    Contact,
    ContactActiveRoom,
    ContactAdvertPathSummary,
    ContactAnalytics,
    ContactRoutingOverrideRequest,
    ContactTelemetryResponse,
    ContactUpsert,
    CreateContactRequest,
    LppSensor,
    NearestRepeater,
    PathDiscoveryResponse,
    PathDiscoveryRoute,
    TelemetryHistoryEntry,
    TraceResponse,
)
from app.packet_processor import start_historical_dm_decryption
from app.path_utils import parse_explicit_hop_route
from app.repository import (
    AmbiguousPublicKeyPrefixError,
    ContactAdvertPathRepository,
    ContactNameHistoryRepository,
    ContactRepository,
    MessageRepository,
)
from app.services.contact_reconciliation import (
    promote_prefix_contacts_for_contact,
    record_contact_name_and_reconcile,
)
from app.services.radio_runtime import radio_runtime as radio_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contacts", tags=["contacts"])


TRACE_HASH_BYTES = 4
TRACE_FLAGS_4BYTE = 2


def _ambiguous_contact_detail(err: AmbiguousPublicKeyPrefixError) -> str:
    sample = ", ".join(key[:12] for key in err.matches[:2])
    return (
        f"Ambiguous contact key prefix '{err.prefix}'. "
        f"Use a full 64-character public key. Matching contacts: {sample}"
    )


async def _resolve_contact_or_404(
    public_key: str, not_found_detail: str = "Contact not found"
) -> Contact:
    try:
        contact = await ContactRepository.get_by_key_or_prefix(public_key)
    except AmbiguousPublicKeyPrefixError as err:
        raise HTTPException(status_code=409, detail=_ambiguous_contact_detail(err)) from err
    if not contact:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return contact


async def _ensure_on_radio(mc, contact: Contact) -> None:
    """Add a contact to the radio for routing, raising 422 on failure."""
    add_result = await mc.commands.add_contact(contact.to_radio_dict())
    if add_result is not None and add_result.type == EventType.ERROR:
        raise HTTPException(
            status_code=422, detail=f"Failed to add contact to radio: {add_result.payload}"
        )


async def _best_effort_push_contact_to_radio(contact: Contact, operation_name: str) -> None:
    """Best-effort push the current effective route to the radio when connected."""
    if not radio_manager.is_connected:
        return

    try:
        async with radio_manager.radio_operation(operation_name) as mc:
            result = await mc.commands.add_contact(contact.to_radio_dict())
        if result is not None and result.type == EventType.ERROR:
            logger.warning(
                "Failed to push updated routing to radio for %s: %s",
                contact.public_key[:12],
                result.payload,
            )
    except Exception:
        logger.warning(
            "Failed to push updated routing to radio for %s",
            contact.public_key[:12],
            exc_info=True,
        )


async def _broadcast_contact_update(contact: Contact) -> None:
    from app.websocket import broadcast_event

    broadcast_event("contact", contact.model_dump())


async def _broadcast_contact_resolution(previous_public_keys: list[str], contact: Contact) -> None:
    from app.websocket import broadcast_event

    for old_key in previous_public_keys:
        broadcast_event(
            "contact_resolved",
            {
                "previous_public_key": old_key,
                "contact": contact.model_dump(),
            },
        )


def _path_hash_mode_from_hop_width(hop_width: object) -> int:
    if not isinstance(hop_width, int):
        return 0
    return max(0, min(hop_width - 1, 2))


async def _build_keyed_contact_analytics(contact: Contact) -> ContactAnalytics:
    name_history = await ContactNameHistoryRepository.get_history(contact.public_key)
    dm_count = await MessageRepository.count_dm_messages(contact.public_key)
    chan_count = await MessageRepository.count_channel_messages_by_sender(contact.public_key)
    active_rooms_raw = await MessageRepository.get_most_active_rooms(contact.public_key)
    advert_paths = await ContactAdvertPathRepository.get_recent_for_contact(contact.public_key)
    hourly_activity, weekly_activity = await MessageRepository.get_contact_activity_series(
        contact.public_key
    )

    most_active_rooms = [
        ContactActiveRoom(channel_key=key, channel_name=name, message_count=count)
        for key, name, count in active_rooms_raw
    ]

    advert_frequency: float | None = None
    if advert_paths:
        total_observations = sum(p.heard_count for p in advert_paths)
        earliest = min(p.first_seen for p in advert_paths)
        latest = max(p.last_seen for p in advert_paths)
        span_hours = (latest - earliest) / 3600.0
        if span_hours > 0:
            advert_frequency = round(total_observations / span_hours, 2)

    first_hop_stats: dict[str, dict] = {}
    for p in advert_paths:
        prefix = p.next_hop
        if prefix:
            if prefix not in first_hop_stats:
                first_hop_stats[prefix] = {
                    "heard_count": 0,
                    "path_len": p.path_len,
                    "last_seen": p.last_seen,
                }
            first_hop_stats[prefix]["heard_count"] += p.heard_count
            first_hop_stats[prefix]["last_seen"] = max(
                first_hop_stats[prefix]["last_seen"], p.last_seen
            )

    resolved_contacts = await ContactRepository.resolve_prefixes(list(first_hop_stats.keys()))

    nearest_repeaters: list[NearestRepeater] = []
    for prefix, stats in first_hop_stats.items():
        resolved = resolved_contacts.get(prefix)
        nearest_repeaters.append(
            NearestRepeater(
                public_key=resolved.public_key if resolved else prefix,
                name=resolved.name if resolved else None,
                path_len=stats["path_len"],
                last_seen=stats["last_seen"],
                heard_count=stats["heard_count"],
            )
        )

    nearest_repeaters.sort(key=lambda r: r.heard_count, reverse=True)

    return ContactAnalytics(
        lookup_type="contact",
        name=contact.name or contact.public_key[:12],
        contact=contact,
        name_history=name_history,
        dm_message_count=dm_count,
        channel_message_count=chan_count,
        includes_direct_messages=True,
        most_active_rooms=most_active_rooms,
        advert_paths=advert_paths,
        advert_frequency=advert_frequency,
        nearest_repeaters=nearest_repeaters,
        hourly_activity=hourly_activity,
        weekly_activity=weekly_activity,
    )


async def _build_name_only_contact_analytics(name: str) -> ContactAnalytics:
    chan_count = await MessageRepository.count_channel_messages_by_sender_name(name)
    name_first_seen_at = await MessageRepository.get_first_channel_message_by_sender_name(name)
    active_rooms_raw = await MessageRepository.get_most_active_rooms_by_sender_name(name)
    hourly_activity, weekly_activity = await MessageRepository.get_sender_name_activity_series(name)

    most_active_rooms = [
        ContactActiveRoom(channel_key=key, channel_name=room_name, message_count=count)
        for key, room_name, count in active_rooms_raw
    ]

    return ContactAnalytics(
        lookup_type="name",
        name=name,
        name_first_seen_at=name_first_seen_at,
        channel_message_count=chan_count,
        includes_direct_messages=False,
        most_active_rooms=most_active_rooms,
        hourly_activity=hourly_activity,
        weekly_activity=weekly_activity,
    )


@router.get("", response_model=list[Contact])
async def list_contacts(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[Contact]:
    """List contacts from the database."""
    return await ContactRepository.get_all(limit=limit, offset=offset)


@router.get("/repeaters/advert-paths", response_model=list[ContactAdvertPathSummary])
async def list_repeater_advert_paths(
    limit_per_repeater: int = Query(default=10, ge=1, le=50),
) -> list[ContactAdvertPathSummary]:
    """List recent unique advert paths for all repeaters.

    Note: This endpoint now returns paths for all contacts (table was renamed).
    The route is kept for backward compatibility.
    """
    return await ContactAdvertPathRepository.get_recent_for_all_contacts(
        limit_per_contact=limit_per_repeater
    )


@router.get("/analytics", response_model=ContactAnalytics)
async def get_contact_analytics(
    public_key: str | None = Query(default=None),
    name: str | None = Query(default=None, min_length=1, max_length=200),
) -> ContactAnalytics:
    """Get unified contact analytics for either a keyed contact or a sender name."""
    if bool(public_key) == bool(name):
        raise HTTPException(status_code=400, detail="Specify exactly one of public_key or name")

    if public_key:
        contact = await _resolve_contact_or_404(public_key)
        return await _build_keyed_contact_analytics(contact)

    assert name is not None
    normalized_name = name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="name is required")
    return await _build_name_only_contact_analytics(normalized_name)


@router.post("", response_model=Contact)
async def create_contact(
    request: CreateContactRequest, background_tasks: BackgroundTasks
) -> Contact:
    """Create a new contact in the database.

    If the contact already exists, updates the name (if provided).
    If try_historical is True, attempts to decrypt historical DM packets.
    """
    # Validate hex format
    try:
        bytes.fromhex(request.public_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid public key: must be valid hex") from e

    # Check if contact already exists
    existing = await ContactRepository.get_by_key(request.public_key)
    if existing:
        # Update name if provided and record name history
        if request.name:
            await ContactRepository.upsert(existing.to_upsert(name=request.name))
            refreshed = await ContactRepository.get_by_key(request.public_key)
            if refreshed is not None:
                existing = refreshed
            await record_contact_name_and_reconcile(
                public_key=request.public_key,
                contact_name=request.name,
                timestamp=int(time.time()),
                log=logger,
            )

        promoted_keys = await promote_prefix_contacts_for_contact(
            public_key=request.public_key,
            log=logger,
        )
        if promoted_keys:
            refreshed = await ContactRepository.get_by_key(request.public_key)
            if refreshed is not None:
                existing = refreshed
                await _broadcast_contact_resolution(promoted_keys, existing)

        # Trigger historical decryption if requested (even for existing contacts)
        if request.try_historical:
            await start_historical_dm_decryption(
                background_tasks, request.public_key, request.name or existing.name
            )

        await _broadcast_contact_update(existing)
        return existing

    # Create new contact
    lower_key = request.public_key.lower()
    contact_upsert = ContactUpsert(
        public_key=lower_key,
        name=request.name,
        type=request.type,
        on_radio=False,
    )
    await ContactRepository.upsert(contact_upsert)
    logger.info("Created contact %s", lower_key[:12])
    promoted_keys = await promote_prefix_contacts_for_contact(
        public_key=lower_key,
        log=logger,
    )

    await record_contact_name_and_reconcile(
        public_key=lower_key,
        contact_name=request.name,
        timestamp=int(time.time()),
        log=logger,
    )

    # Trigger historical decryption if requested
    if request.try_historical:
        await start_historical_dm_decryption(background_tasks, lower_key, request.name)

    stored = await ContactRepository.get_by_key(lower_key)
    if stored is None:
        raise HTTPException(status_code=500, detail="Contact was created but could not be reloaded")
    await _broadcast_contact_update(stored)
    await _broadcast_contact_resolution(promoted_keys, stored)
    return stored


@router.post("/{public_key}/mark-read")
async def mark_contact_read(public_key: str) -> dict:
    """Mark a contact conversation as read (update last_read_at timestamp)."""
    contact = await _resolve_contact_or_404(public_key)

    updated = await ContactRepository.update_last_read_at(contact.public_key)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update read state")

    return {"status": "ok", "public_key": contact.public_key}


class BulkDeleteRequest(BaseModel):
    public_keys: list[str] = Field(description="Public keys to delete")


@router.post("/bulk-delete")
async def bulk_delete_contacts(request: BulkDeleteRequest) -> dict:
    """Delete multiple contacts from the database (and radio if present)."""
    from app.websocket import broadcast_event

    # Resolve all contacts first
    contacts_to_delete: list[Contact] = []
    for key in request.public_keys:
        contact = await ContactRepository.get_by_key(key.lower())
        if contact:
            contacts_to_delete.append(contact)

    # Remove from radio in a single locked operation (blocks until radio is free)
    if radio_manager.is_connected and contacts_to_delete:
        try:
            async with radio_manager.radio_operation("bulk_delete_contacts_from_radio") as mc:
                for contact in contacts_to_delete:
                    radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
                    if radio_contact:
                        await mc.commands.remove_contact(radio_contact)
        except Exception as e:
            logger.warning("Radio removal during bulk delete failed: %s", e)

    # Delete from database and broadcast events
    deleted = 0
    for contact in contacts_to_delete:
        await ContactRepository.delete(contact.public_key)
        broadcast_event("contact_deleted", {"public_key": contact.public_key})
        deleted += 1

    logger.info("Bulk deleted %d/%d contacts", deleted, len(request.public_keys))
    return {"deleted": deleted}


@router.delete("/{public_key}")
async def delete_contact(public_key: str) -> dict:
    """Delete a contact from the database (and radio if present)."""
    contact = await _resolve_contact_or_404(public_key)

    # Remove from radio if connected and contact is on radio
    if radio_manager.is_connected:
        async with radio_manager.radio_operation("delete_contact_from_radio") as mc:
            radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
            if radio_contact:
                logger.info(
                    "Removing contact %s from radio before deletion", contact.public_key[:12]
                )
                await mc.commands.remove_contact(radio_contact)

    # Delete from database
    await ContactRepository.delete(contact.public_key)
    logger.info("Deleted contact %s", contact.public_key[:12])

    from app.websocket import broadcast_event

    broadcast_event("contact_deleted", {"public_key": contact.public_key})

    return {"status": "ok"}


@router.post("/{public_key}/trace", response_model=TraceResponse)
async def request_trace(public_key: str) -> TraceResponse:
    """Send a single-hop trace to a contact and wait for the result."""
    radio_manager.require_connected()

    contact = await _resolve_contact_or_404(public_key)

    tag = random.randint(1, 0xFFFFFFFF)
    contact_hash = contact.public_key[: TRACE_HASH_BYTES * 2]

    async with radio_manager.radio_operation("request_trace", pause_polling=True) as mc:
        await _ensure_on_radio(mc, contact)

        logger.info(
            "Sending trace to %s (tag=%d, hash=%s)", contact.public_key[:12], tag, contact_hash
        )
        result = await mc.commands.send_trace(
            path=contact_hash,
            tag=tag,
            flags=TRACE_FLAGS_4BYTE,
        )

        if result.type == EventType.ERROR:
            raise HTTPException(status_code=422, detail=f"Failed to send trace: {result.payload}")

        event = await mc.wait_for_event(
            EventType.TRACE_DATA,
            attribute_filters={"tag": tag},
            timeout=15,
        )

    if event is None:
        raise HTTPException(status_code=408, detail="No trace response heard")

    trace = event.payload
    path = trace.get("path", [])
    path_len = trace.get("path_len", 0)

    remote_snr = path[0]["snr"] if path else None
    local_snr = path[-1]["snr"] if path else None

    logger.info(
        "Trace result for %s: path_len=%d, remote_snr=%s, local_snr=%s",
        contact.public_key[:12],
        path_len,
        remote_snr,
        local_snr,
    )

    return TraceResponse(remote_snr=remote_snr, local_snr=local_snr, path_len=path_len)


@router.post("/{public_key}/path-discovery", response_model=PathDiscoveryResponse)
async def request_path_discovery(public_key: str) -> PathDiscoveryResponse:
    """Discover the current forward and return paths to a known contact."""
    radio_manager.require_connected()

    contact = await _resolve_contact_or_404(public_key)
    pubkey_prefix = contact.public_key[:12]

    async with radio_manager.radio_operation("request_path_discovery", pause_polling=True) as mc:
        await _ensure_on_radio(mc, contact)

        response_task = asyncio.create_task(
            mc.wait_for_event(
                EventType.PATH_RESPONSE,
                attribute_filters={"pubkey_pre": pubkey_prefix},
                timeout=15,
            )
        )
        try:
            result = await mc.commands.send_path_discovery(contact.public_key)
            if result.type == EventType.ERROR:
                raise HTTPException(
                    status_code=422,
                    detail=f"Failed to send path discovery: {result.payload}",
                )

            event = await response_task
        finally:
            if not response_task.done():
                response_task.cancel()
            with suppress(asyncio.CancelledError):
                await response_task

        if event is None:
            raise HTTPException(status_code=408, detail="No path discovery response heard")

        payload = event.payload
        forward_path = str(payload.get("out_path") or "")
        forward_len = int(payload.get("out_path_len") or 0)
        forward_mode = _path_hash_mode_from_hop_width(payload.get("out_path_hash_len"))
        return_path = str(payload.get("in_path") or "")
        return_len = int(payload.get("in_path_len") or 0)
        return_mode = _path_hash_mode_from_hop_width(payload.get("in_path_hash_len"))

        await ContactRepository.update_direct_path(
            contact.public_key,
            forward_path,
            forward_len,
            forward_mode,
        )
        refreshed_contact = await _resolve_contact_or_404(contact.public_key)

        try:
            sync_result = await mc.commands.add_contact(refreshed_contact.to_radio_dict())
            if sync_result is not None and sync_result.type == EventType.ERROR:
                logger.warning(
                    "Failed to sync discovered path back to radio for %s: %s",
                    refreshed_contact.public_key[:12],
                    sync_result.payload,
                )
        except Exception:
            logger.warning(
                "Failed to sync discovered path back to radio for %s",
                refreshed_contact.public_key[:12],
                exc_info=True,
            )

    await _broadcast_contact_update(refreshed_contact)

    return PathDiscoveryResponse(
        contact=refreshed_contact,
        forward_path=PathDiscoveryRoute(
            path=forward_path,
            path_len=forward_len,
            path_hash_mode=forward_mode,
        ),
        return_path=PathDiscoveryRoute(
            path=return_path,
            path_len=return_len,
            path_hash_mode=return_mode,
        ),
    )


@router.post("/{public_key}/routing-override")
async def set_contact_routing_override(
    public_key: str, request: ContactRoutingOverrideRequest
) -> dict:
    """Set, force, or clear an explicit routing override for a contact."""
    contact = await _resolve_contact_or_404(public_key)

    route_text = request.route.strip()
    if route_text == "":
        await ContactRepository.clear_routing_override(contact.public_key)
        logger.info(
            "Cleared routing override for %s",
            contact.public_key[:12],
        )
    elif route_text == "-1":
        await ContactRepository.set_routing_override(contact.public_key, "", -1, -1)
        logger.info("Set forced flood routing override for %s", contact.public_key[:12])
    elif route_text == "0":
        await ContactRepository.set_routing_override(contact.public_key, "", 0, 0)
        logger.info("Set forced direct routing override for %s", contact.public_key[:12])
    else:
        try:
            path_hex, path_len, hash_mode = parse_explicit_hop_route(route_text)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

        await ContactRepository.set_routing_override(
            contact.public_key,
            path_hex,
            path_len,
            hash_mode,
        )
        logger.info(
            "Set explicit routing override for %s: %d hop(s), %d-byte IDs",
            contact.public_key[:12],
            path_len,
            hash_mode + 1,
        )

    updated_contact = await ContactRepository.get_by_key(contact.public_key)
    if updated_contact:
        await _best_effort_push_contact_to_radio(updated_contact, "set_routing_override_on_radio")
        await _broadcast_contact_update(updated_contact)

    return {"status": "ok", "public_key": contact.public_key}


class ContactMcmpSettingsRequest(BaseModel):
    mcmp_enabled: bool = Field(
        default=False, description="Enable MCMP compression for direct messages"
    )


@router.post("/{public_key}/mcmp", response_model=Contact)
async def set_contact_mcmp(public_key: str, request: ContactMcmpSettingsRequest) -> Contact:
    """Set MCMP compression flag for a direct-contact conversation."""
    contact = await _resolve_contact_or_404(public_key)
    if len(contact.public_key) < 64:
        raise HTTPException(status_code=409, detail="Contact has no full public key")

    updated = await ContactRepository.set_mcmp_enabled(
        contact.public_key, request.mcmp_enabled
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update MCMP setting")

    refreshed = await ContactRepository.get_by_key(contact.public_key)
    if refreshed is None:
        raise HTTPException(status_code=500, detail="Contact disappeared after update")

    await _broadcast_contact_update(refreshed)
    return refreshed


# ---------------------------------------------------------------------------
# On-demand contact telemetry (CayenneLPP)
# ---------------------------------------------------------------------------


@router.post("/{public_key}/telemetry", response_model=ContactTelemetryResponse)
async def request_contact_telemetry(public_key: str) -> ContactTelemetryResponse:
    """Fetch CayenneLPP telemetry from any contact (single attempt, 10s timeout).

    Persists the result in contact_telemetry_history and returns the latest
    sensor readings along with recent telemetry history.
    """
    from app.repository.contact_telemetry import ContactTelemetryRepository

    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)

    async with radio_manager.radio_operation(
        "contact_telemetry", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        await _ensure_on_radio(mc, contact)
        telemetry = await mc.commands.req_telemetry_sync(
            contact.public_key, timeout=10, min_timeout=5
        )

    if telemetry is None:
        raise HTTPException(status_code=504, detail="No telemetry response from contact")

    sensors: list[LppSensor] = []
    for entry in telemetry:
        channel = entry.get("channel", 0)
        type_name = str(entry.get("type", "unknown"))
        value = entry.get("value", 0)
        sensors.append(LppSensor(channel=channel, type_name=type_name, value=value))

    fetched_at = int(time.time())

    # Persist snapshot
    data = {"lpp_sensors": [s.model_dump() for s in sensors]}
    await ContactTelemetryRepository.record(
        public_key=contact.public_key,
        timestamp=fetched_at,
        data=data,
    )

    # Dispatch to fanout modules (e.g. HA MQTT)
    from app.fanout.manager import fanout_manager

    asyncio.create_task(
        fanout_manager.broadcast_telemetry(
            {
                "public_key": contact.public_key,
                "name": contact.name or contact.public_key[:12],
                "timestamp": fetched_at,
                **data,
            }
        )
    )

    # Fetch recent history (30 days)
    since = fetched_at - 30 * 86400
    rows = await ContactTelemetryRepository.get_history(contact.public_key, since)
    history = [TelemetryHistoryEntry(**row) for row in rows]

    return ContactTelemetryResponse(
        sensors=sensors,
        fetched_at=fetched_at,
        telemetry_history=history,
    )


@router.get("/{public_key}/telemetry-history", response_model=list[TelemetryHistoryEntry])
async def get_contact_telemetry_history(public_key: str) -> list[TelemetryHistoryEntry]:
    """Get stored telemetry history for a contact (read-only, no radio access)."""
    from app.repository.contact_telemetry import ContactTelemetryRepository

    contact = await _resolve_contact_or_404(public_key)
    since = int(time.time()) - 30 * 86400
    rows = await ContactTelemetryRepository.get_history(contact.public_key, since)
    return [TelemetryHistoryEntry(**row) for row in rows]