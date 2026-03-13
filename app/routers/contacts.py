import logging
import random

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from meshcore import EventType

from app.dependencies import require_connected
from app.models import (
    Contact,
    ContactActiveRoom,
    ContactAdvertPathSummary,
    ContactAnalytics,
    ContactRoutingOverrideRequest,
    ContactUpsert,
    CreateContactRequest,
    NearestRepeater,
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
    reconcile_contact_messages,
)
from app.services.radio_runtime import radio_runtime as radio_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contacts", tags=["contacts"])


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
    """Add a contact to the radio for routing, raising 500 on failure."""
    add_result = await mc.commands.add_contact(contact.to_radio_dict())
    if add_result is not None and add_result.type == EventType.ERROR:
        raise HTTPException(
            status_code=500, detail=f"Failed to add contact to radio: {add_result.payload}"
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
        # Update name if provided
        if request.name:
            await ContactRepository.upsert(existing.to_upsert(name=request.name))
            refreshed = await ContactRepository.get_by_key(request.public_key)
            if refreshed is not None:
                existing = refreshed

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
        out_path_hash_mode=-1,
        on_radio=False,
    )
    await ContactRepository.upsert(contact_upsert)
    logger.info("Created contact %s", lower_key[:12])
    promoted_keys = await promote_prefix_contacts_for_contact(
        public_key=lower_key,
        log=logger,
    )

    await reconcile_contact_messages(
        public_key=lower_key,
        contact_name=request.name,
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
    """Send a single-hop trace to a contact and wait for the result.

    The trace path contains the contact's 1-byte pubkey hash as the sole hop
    (no intermediate repeaters). The radio firmware requires at least one
    node in the path.
    """
    require_connected()

    contact = await _resolve_contact_or_404(public_key)

    tag = random.randint(1, 0xFFFFFFFF)
    # First 2 hex chars of pubkey = 1-byte hash used by the trace protocol
    contact_hash = contact.public_key[:2]

    # Trace does not need auto-fetch suspension: response arrives as TRACE_DATA
    # from the reader loop, not via get_msg().
    async with radio_manager.radio_operation("request_trace", pause_polling=True) as mc:
        # Ensure contact is on radio so the trace can reach them
        await _ensure_on_radio(mc, contact)

        logger.info(
            "Sending trace to %s (tag=%d, hash=%s)", contact.public_key[:12], tag, contact_hash
        )
        result = await mc.commands.send_trace(path=contact_hash, tag=tag)

        if result.type == EventType.ERROR:
            raise HTTPException(status_code=500, detail=f"Failed to send trace: {result.payload}")

        # Wait for the matching TRACE_DATA event
        event = await mc.wait_for_event(
            EventType.TRACE_DATA,
            attribute_filters={"tag": tag},
            timeout=15,
        )

    if event is None:
        raise HTTPException(status_code=504, detail="No trace response heard")

    trace = event.payload
    path = trace.get("path", [])
    path_len = trace.get("path_len", 0)

    # remote_snr: first entry in path (what the target heard us at)
    remote_snr = path[0]["snr"] if path else None
    # local_snr: last entry in path (what we heard them at on the bounce-back)
    local_snr = path[-1]["snr"] if path else None

    logger.info(
        "Trace result for %s: path_len=%d, remote_snr=%s, local_snr=%s",
        contact.public_key[:12],
        path_len,
        remote_snr,
        local_snr,
    )

    return TraceResponse(remote_snr=remote_snr, local_snr=local_snr, path_len=path_len)


@router.post("/{public_key}/routing-override")
async def set_contact_routing_override(
    public_key: str, request: ContactRoutingOverrideRequest
) -> dict:
    """Set, force, or clear an explicit routing override for a contact."""
    contact = await _resolve_contact_or_404(public_key)

    route_text = request.route.strip()
    if route_text == "":
        await ContactRepository.clear_routing_override(contact.public_key)
        await ContactRepository.update_path(contact.public_key, "", -1, -1)
        logger.info(
            "Cleared routing override and reset learned path to flood for %s",
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
