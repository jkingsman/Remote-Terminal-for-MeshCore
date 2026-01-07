import logging

from fastapi import APIRouter, HTTPException, Query
from meshcore import EventType

from app.dependencies import require_connected
from app.models import Contact
from app.radio import radio_manager
from app.repository import ContactRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/contacts", tags=["contacts"])


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
