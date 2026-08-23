from fastapi import APIRouter, HTTPException

from app.models import (
    CONTACT_TYPE_ROOM,
    AclEntry,
    LppSensor,
    RepeaterAclResponse,
    RepeaterLoginResponse,
    RepeaterLppTelemetryResponse,
    RepeaterStatusResponse,
    RoomLoginRequest,
    RoomPollConfigRequest,
    RoomPollStatus,
)
from app.repository.room_poll import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    RoomPollRepository,
)
from app.routers.contacts import _ensure_on_radio, _resolve_contact_or_404
from app.routers.server_control import (
    prepare_authenticated_contact_connection,
    require_server_capable_contact,
)
from app.services.radio_runtime import radio_runtime as radio_manager

router = APIRouter(prefix="/contacts", tags=["rooms"])


def _require_room(contact) -> None:
    require_server_capable_contact(contact, allowed_types=(CONTACT_TYPE_ROOM,))


def _poll_status(room_key: str, sub) -> RoomPollStatus:
    """Build the client-facing status. Deliberately omits the credential value."""
    if sub is None:
        return RoomPollStatus(
            room_key=room_key,
            has_stored_credential=False,
            is_guest_credential=False,
            poll_enabled=False,
            interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
        )
    return RoomPollStatus(
        room_key=room_key,
        has_stored_credential=sub.has_credential,
        is_guest_credential=sub.is_guest_credential,
        poll_enabled=sub.poll_enabled,
        interval_seconds=sub.interval_seconds,
        last_poll_at=sub.last_poll_at,
        last_result=sub.last_result,
        last_error=sub.last_error,
        consecutive_errors=sub.consecutive_errors,
    )


@router.post("/{public_key}/room/login", response_model=RepeaterLoginResponse)
async def room_login(public_key: str, request: RoomLoginRequest) -> RepeaterLoginResponse:
    """Attempt room-server login and report whether auth was confirmed.

    With ``use_stored_credential`` the server logs in using the credential saved
    for this room (password or guest) and never returns it to the caller — this
    is what lets the UI open a known room without the password prompt.
    """
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_room(contact)

    if request.use_stored_credential:
        sub = await RoomPollRepository.get(contact.public_key)
        # credential is three-state; `is None` (not falsiness) distinguishes
        # "nothing stored" from a valid guest ("") credential.
        if sub is None or sub.credential is None:
            raise HTTPException(status_code=400, detail="no stored credential for this room")
        password = sub.credential
    else:
        # Absent password -> guest; the model already normalizes None to guest.
        password = request.password if request.password is not None else ""

    async with radio_manager.radio_operation(
        "room_login",
        pause_polling=True,
        suspend_auto_fetch=True,
    ) as mc:
        return await prepare_authenticated_contact_connection(
            mc,
            contact,
            password,
            label="room server",
        )


@router.get("/{public_key}/room/poll", response_model=RoomPollStatus)
async def get_room_poll(public_key: str) -> RoomPollStatus:
    """Report a room's stored-credential / poll status (never the credential)."""
    contact = await _resolve_contact_or_404(public_key)
    _require_room(contact)
    sub = await RoomPollRepository.get(contact.public_key)
    return _poll_status(contact.public_key, sub)


@router.put("/{public_key}/room/poll", response_model=RoomPollStatus)
async def set_room_poll(public_key: str, request: RoomPollConfigRequest) -> RoomPollStatus:
    """Set a room's stored credential and/or background poll schedule."""
    contact = await _resolve_contact_or_404(public_key)
    _require_room(contact)

    sub = await RoomPollRepository.upsert(
        contact.public_key,
        enabled=request.enabled,
        interval_seconds=request.interval_seconds,
        credential_action=request.credential_action,
        credential=request.credential,
    )

    # Enabling polling without a stored credential can never succeed — the poller
    # needs one to log in. Reject rather than silently leaving it disabled.
    if sub.poll_enabled and not sub.has_credential:
        raise HTTPException(
            status_code=400,
            detail="store a room password or guest credential before enabling polling",
        )

    return _poll_status(contact.public_key, sub)


@router.delete("/{public_key}/room/poll", response_model=RoomPollStatus)
async def delete_room_poll(public_key: str) -> RoomPollStatus:
    """Remove a room's stored credential and disable polling."""
    contact = await _resolve_contact_or_404(public_key)
    _require_room(contact)
    await RoomPollRepository.delete(contact.public_key)
    return _poll_status(contact.public_key, None)


@router.post("/{public_key}/room/status", response_model=RepeaterStatusResponse)
async def room_status(public_key: str) -> RepeaterStatusResponse:
    """Fetch status telemetry from a room server."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_room(contact)

    async with radio_manager.radio_operation(
        "room_status", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        await _ensure_on_radio(mc, contact)
        status = await mc.commands.req_status_sync(contact.public_key, timeout=10, min_timeout=5)

    if status is None:
        raise HTTPException(status_code=408, detail="No status response from room server")

    return RepeaterStatusResponse(
        battery_volts=status.get("bat", 0) / 1000.0,
        tx_queue_len=status.get("tx_queue_len", 0),
        noise_floor_dbm=status.get("noise_floor", 0),
        last_rssi_dbm=status.get("last_rssi", 0),
        last_snr_db=status.get("last_snr", 0.0),
        packets_received=status.get("nb_recv", 0),
        packets_sent=status.get("nb_sent", 0),
        airtime_seconds=status.get("airtime", 0),
        rx_airtime_seconds=status.get("rx_airtime", 0),
        uptime_seconds=status.get("uptime", 0),
        sent_flood=status.get("sent_flood", 0),
        sent_direct=status.get("sent_direct", 0),
        recv_flood=status.get("recv_flood", 0),
        recv_direct=status.get("recv_direct", 0),
        flood_dups=status.get("flood_dups", 0),
        direct_dups=status.get("direct_dups", 0),
        full_events=status.get("full_evts", 0),
        recv_errors=status.get("recv_errors"),
    )


@router.post("/{public_key}/room/lpp-telemetry", response_model=RepeaterLppTelemetryResponse)
async def room_lpp_telemetry(public_key: str) -> RepeaterLppTelemetryResponse:
    """Fetch CayenneLPP telemetry from a room server."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_room(contact)

    async with radio_manager.radio_operation(
        "room_lpp_telemetry", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        await _ensure_on_radio(mc, contact)
        telemetry = await mc.commands.req_telemetry_sync(
            contact.public_key, timeout=10, min_timeout=5
        )

    if telemetry is None:
        raise HTTPException(status_code=408, detail="No telemetry response from room server")

    sensors = [
        LppSensor(
            channel=entry.get("channel", 0),
            type_name=str(entry.get("type", "unknown")),
            value=entry.get("value", 0),
        )
        for entry in telemetry
    ]
    return RepeaterLppTelemetryResponse(sensors=sensors)


@router.post("/{public_key}/room/acl", response_model=RepeaterAclResponse)
async def room_acl(public_key: str) -> RepeaterAclResponse:
    """Fetch ACL entries from a room server."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_room(contact)

    async with radio_manager.radio_operation(
        "room_acl", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        await _ensure_on_radio(mc, contact)
        acl_data = await mc.commands.req_acl_sync(contact.public_key, timeout=10, min_timeout=5)

    acl_entries = []
    if acl_data and isinstance(acl_data, list):
        from app.repository import ContactRepository
        from app.routers.repeaters import ACL_PERMISSION_NAMES

        for entry in acl_data:
            pubkey_prefix = entry.get("key", "")
            perm = entry.get("perm", 0)
            resolved_contact = await ContactRepository.get_by_key_prefix(pubkey_prefix)
            acl_entries.append(
                AclEntry(
                    pubkey_prefix=pubkey_prefix,
                    name=resolved_contact.name if resolved_contact else None,
                    permission=perm,
                    permission_name=ACL_PERMISSION_NAMES.get(perm, f"Unknown({perm})"),
                )
            )

    return RepeaterAclResponse(acl=acl_entries)
