import logging

from fastapi import APIRouter, HTTPException, Query
from meshcore import EventType

from app.dependencies import require_connected
from app.models import (
    Contact,
    TelemetryRequest,
    TelemetryResponse,
    NeighborInfo,
    AclEntry,
    CommandRequest,
    CommandResponse,
    CONTACT_TYPE_REPEATER,
)

# ACL permission level names
ACL_PERMISSION_NAMES = {
    0: "Guest",
    1: "Read-only",
    2: "Read-write",
    3: "Admin",
}
from app.radio import radio_manager
from app.radio_sync import pause_polling
from app.repository import ContactRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/contacts", tags=["contacts"])


async def ensure_repeater_on_radio(mc, contact: Contact) -> None:
    """Ensure a repeater contact is on the radio with flood mode.

    This syncs contacts, removes any existing entry (to clear stale state),
    and re-adds with flood mode. Does NOT perform login.

    Args:
        mc: MeshCore instance
        contact: The repeater contact

    Raises:
        HTTPException: If contact cannot be added
    """
    # Sync contacts from radio to ensure our cache is up-to-date
    logger.info("Syncing contacts from radio before repeater operation")
    await mc.ensure_contacts()

    # Remove contact if it exists (clears any stale state on radio)
    radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
    if radio_contact:
        logger.info("Removing existing contact %s from radio", contact.public_key[:12])
        await mc.commands.remove_contact(contact.public_key)
        await mc.commands.get_contacts()

    # Add contact fresh with flood mode
    logger.info("Adding repeater %s to radio with flood mode", contact.public_key[:12])
    contact_data = {
        "public_key": contact.public_key,
        "adv_name": contact.name or "",
        "type": contact.type,
        "flags": contact.flags,
        "out_path": "",
        "out_path_len": -1,  # Flood mode
        "adv_lat": contact.lat or 0.0,
        "adv_lon": contact.lon or 0.0,
        "last_advert": contact.last_advert or 0,
    }
    add_result = await mc.commands.add_contact(contact_data)
    if add_result.type == EventType.ERROR:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to add contact to radio: {add_result.payload}"
        )

    # Refresh and verify
    await mc.commands.get_contacts()
    radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
    if not radio_contact:
        raise HTTPException(
            status_code=500,
            detail="Failed to add contact to radio - contact not found after add"
        )


async def prepare_repeater_connection(mc, contact: Contact, password: str) -> None:
    """Prepare connection to a repeater by adding to radio and logging in.

    This ensures the contact is on the radio and performs a fresh login.

    Args:
        mc: MeshCore instance
        contact: The repeater contact
        password: Password for login (empty string for no password)

    Raises:
        HTTPException: If contact cannot be added or login fails
    """
    await ensure_repeater_on_radio(mc, contact)

    # Send login with password
    logger.info("Sending login to repeater %s", contact.public_key[:12])
    login_result = await mc.commands.send_login(contact.public_key, password)

    if login_result.type == EventType.ERROR:
        raise HTTPException(
            status_code=401,
            detail=f"Login failed: {login_result.payload}"
        )


@router.get("", response_model=list[Contact])
async def list_contacts(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[Contact]:
    """List contacts from the database."""
    return await ContactRepository.get_all(limit=limit, offset=offset)


@router.get("/{public_key}", response_model=Contact)
async def get_contact(public_key: str) -> Contact:
    """Get a specific contact by public key or prefix."""
    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@router.post("/sync")
async def sync_contacts_from_radio() -> dict:
    """Sync contacts from the radio to the database."""
    mc = require_connected()

    logger.info("Syncing contacts from radio")

    result = await mc.commands.get_contacts()

    if result.type == EventType.ERROR:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get contacts: {result.payload}"
        )

    contacts = result.payload
    count = 0

    for public_key, contact_data in contacts.items():
        await ContactRepository.upsert(
            Contact.from_radio_dict(public_key, contact_data, on_radio=True)
        )
        count += 1

    logger.info("Synced %d contacts from radio", count)
    return {"synced": count}


@router.post("/{public_key}/remove-from-radio")
async def remove_contact_from_radio(public_key: str) -> dict:
    """Remove a contact from the radio (keeps it in database)."""
    mc = require_connected()

    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Get the contact from radio
    radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
    if not radio_contact:
        # Already not on radio
        await ContactRepository.set_on_radio(contact.public_key, False)
        return {"status": "ok", "message": "Contact was not on radio"}

    logger.info("Removing contact %s from radio", contact.public_key[:12])

    result = await mc.commands.remove_contact(radio_contact)

    if result.type == EventType.ERROR:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to remove contact: {result.payload}"
        )

    await ContactRepository.set_on_radio(contact.public_key, False)
    return {"status": "ok"}


@router.post("/{public_key}/add-to-radio")
async def add_contact_to_radio(public_key: str) -> dict:
    """Add a contact from the database to the radio."""
    mc = require_connected()

    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found in database")

    # Check if already on radio
    radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
    if radio_contact:
        return {"status": "ok", "message": "Contact already on radio"}

    logger.info("Adding contact %s to radio", contact.public_key[:12])

    result = await mc.commands.add_contact(contact.to_radio_dict())

    if result.type == EventType.ERROR:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to add contact: {result.payload}"
        )

    await ContactRepository.set_on_radio(contact.public_key, True)
    return {"status": "ok"}


@router.delete("/{public_key}")
async def delete_contact(public_key: str) -> dict:
    """Delete a contact from the database (and radio if present)."""
    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Remove from radio if connected and contact is on radio
    if radio_manager.is_connected and radio_manager.meshcore:
        mc = radio_manager.meshcore
        radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
        if radio_contact:
            logger.info("Removing contact %s from radio before deletion", contact.public_key[:12])
            await mc.commands.remove_contact(radio_contact)

    # Delete from database
    await ContactRepository.delete(contact.public_key)
    logger.info("Deleted contact %s", contact.public_key[:12])

    return {"status": "ok"}


@router.post("/{public_key}/telemetry", response_model=TelemetryResponse)
async def request_telemetry(public_key: str, request: TelemetryRequest) -> TelemetryResponse:
    """Request telemetry from a repeater.

    The contact must be a repeater (type=2). If not on the radio, it will be added.
    Uses login + status request with retry logic.
    """
    mc = require_connected()

    # Get contact from database
    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Verify it's a repeater
    if contact.type != CONTACT_TYPE_REPEATER:
        raise HTTPException(
            status_code=400,
            detail=f"Contact is not a repeater (type={contact.type}, expected {CONTACT_TYPE_REPEATER})"
        )

    # Prepare connection (add/remove dance + login)
    await prepare_repeater_connection(mc, contact, request.password)

    # Request status with retries
    logger.info("Requesting status from repeater %s", contact.public_key[:12])
    status = None
    for attempt in range(1, 4):
        logger.debug("Status request attempt %d/3", attempt)
        status = await mc.commands.req_status_sync(
            contact.public_key,
            timeout=10.0,
            min_timeout=5.0
        )
        if status:
            break
        logger.debug("Status request timeout, retrying...")

    if not status:
        raise HTTPException(
            status_code=504,
            detail="No response from repeater after 3 attempts"
        )

    logger.info("Received telemetry from %s: %s", contact.public_key[:12], status)

    # Fetch neighbors (fetch_all_neighbours handles pagination)
    logger.info("Fetching neighbors from repeater %s", contact.public_key[:12])
    neighbors_data = None
    for attempt in range(1, 4):
        logger.debug("Neighbors request attempt %d/3", attempt)
        neighbors_data = await mc.commands.fetch_all_neighbours(
            contact.public_key,
            timeout=10.0,
            min_timeout=5.0
        )
        if neighbors_data:
            break
        logger.debug("Neighbors request timeout, retrying...")

    # Process neighbors - resolve pubkey prefixes to contact names
    neighbors: list[NeighborInfo] = []
    if neighbors_data and "neighbours" in neighbors_data:
        logger.info("Received %d neighbors", len(neighbors_data["neighbours"]))
        for n in neighbors_data["neighbours"]:
            pubkey_prefix = n.get("pubkey", "")
            # Try to resolve to a contact name from our database
            resolved_contact = await ContactRepository.get_by_key_prefix(pubkey_prefix)
            neighbors.append(NeighborInfo(
                pubkey_prefix=pubkey_prefix,
                name=resolved_contact.name if resolved_contact else None,
                snr=n.get("snr", 0.0),
                last_heard_seconds=n.get("secs_ago", 0),
            ))

    # Fetch ACL
    logger.info("Fetching ACL from repeater %s", contact.public_key[:12])
    acl_data = None
    for attempt in range(1, 4):
        logger.debug("ACL request attempt %d/3", attempt)
        acl_data = await mc.commands.req_acl_sync(
            contact.public_key,
            timeout=10.0,
            min_timeout=5.0
        )
        if acl_data:
            break
        logger.debug("ACL request timeout, retrying...")

    # Process ACL - resolve pubkey prefixes to contact names
    acl_entries: list[AclEntry] = []
    if acl_data and isinstance(acl_data, list):
        logger.info("Received %d ACL entries", len(acl_data))
        for entry in acl_data:
            pubkey_prefix = entry.get("key", "")
            perm = entry.get("perm", 0)
            # Try to resolve to a contact name from our database
            resolved_contact = await ContactRepository.get_by_key_prefix(pubkey_prefix)
            acl_entries.append(AclEntry(
                pubkey_prefix=pubkey_prefix,
                name=resolved_contact.name if resolved_contact else None,
                permission=perm,
                permission_name=ACL_PERMISSION_NAMES.get(perm, f"Unknown({perm})"),
            ))

    # Convert raw telemetry to response format
    # bat is in mV, convert to V (e.g., 3775 -> 3.775)
    return TelemetryResponse(
        pubkey_prefix=status.get("pubkey_pre", contact.public_key[:12]),
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
        neighbors=neighbors,
        acl=acl_entries,
    )


@router.post("/{public_key}/command", response_model=CommandResponse)
async def send_repeater_command(public_key: str, request: CommandRequest) -> CommandResponse:
    """Send a CLI command to a repeater.

    The contact must be a repeater (type=2). The user must have already logged in
    via the telemetry endpoint. This endpoint ensures the contact is on the radio
    before sending commands (the repeater remembers ACL permissions after login).

    Common commands:
    - get name, set name <value>
    - get tx, set tx <dbm>
    - get radio, set radio <freq,bw,sf,cr>
    - tempradio <freq,bw,sf,cr,minutes>
    - setperm <pubkey> <permission>  (0=guest, 1=read-only, 2=read-write, 3=admin)
    - clock, clock sync
    - reboot
    - ver
    """
    mc = require_connected()

    # Get contact from database
    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Verify it's a repeater
    if contact.type != CONTACT_TYPE_REPEATER:
        raise HTTPException(
            status_code=400,
            detail=f"Contact is not a repeater (type={contact.type}, expected {CONTACT_TYPE_REPEATER})"
        )

    # Pause message polling to prevent it from stealing our response
    async with pause_polling():
        # Ensure the repeater contact is on the radio (fixes error_code 2 / ERR_CODE_NOT_FOUND)
        await ensure_repeater_on_radio(mc, contact)

        # Send the command
        logger.info("Sending command to repeater %s: %s", contact.public_key[:12], request.command)

        send_result = await mc.commands.send_cmd(contact.public_key, request.command)

        if send_result.type == EventType.ERROR:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to send command: {send_result.payload}"
            )

        # Wait for response (MESSAGES_WAITING event, then get_msg)
        try:
            wait_result = await mc.wait_for_event(EventType.MESSAGES_WAITING, timeout=10.0)

            if wait_result is None:
                # Timeout - no response received
                logger.warning("No response from repeater %s for command: %s", contact.public_key[:12], request.command)
                return CommandResponse(
                    command=request.command,
                    response="(no response - command may have been processed)"
                )

            response_event = await mc.commands.get_msg()

            if response_event.type == EventType.ERROR:
                return CommandResponse(
                    command=request.command,
                    response=f"(error: {response_event.payload})"
                )

            # Extract the response text and timestamp from the payload
            response_text = response_event.payload.get("text", str(response_event.payload))
            sender_timestamp = response_event.payload.get("timestamp")
            logger.info("Received response from %s: %s", contact.public_key[:12], response_text)

            return CommandResponse(
                command=request.command,
                response=response_text,
                sender_timestamp=sender_timestamp,
            )
        except Exception as e:
            logger.error("Error waiting for response: %s", e)
            return CommandResponse(
                command=request.command,
                response=f"(error waiting for response: {e})"
            )
