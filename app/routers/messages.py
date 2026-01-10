import logging
import time

from fastapi import APIRouter, HTTPException, Query
from meshcore import EventType

from app.dependencies import require_connected
from app.event_handlers import track_pending_ack, track_pending_repeat
from app.models import Message, SendChannelMessageRequest, SendDirectMessageRequest
from app.repository import MessageRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("", response_model=list[Message])
async def list_messages(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    type: str | None = Query(default=None, description="Filter by type: PRIV or CHAN"),
    conversation_key: str | None = Query(default=None, description="Filter by conversation key (channel key or contact pubkey)"),
) -> list[Message]:
    """List messages from the database."""
    return await MessageRepository.get_all(
        limit=limit,
        offset=offset,
        msg_type=type,
        conversation_key=conversation_key,
    )


@router.post("/bulk", response_model=dict[str, list[Message]])
async def get_messages_bulk(
    conversations: list[dict],
    limit_per_conversation: int = Query(default=100, ge=1, le=1000),
) -> dict[str, list[Message]]:
    """Fetch messages for multiple conversations in one request.

    Body should be a list of {type: 'PRIV'|'CHAN', conversation_key: string}.
    Returns a dict mapping 'type:conversation_key' to list of messages.
    """
    return await MessageRepository.get_bulk(conversations, limit_per_conversation)


@router.post("/direct", response_model=Message)
async def send_direct_message(request: SendDirectMessageRequest) -> Message:
    """Send a direct message to a contact."""
    mc = require_connected()

    # First check our database for the contact
    from app.repository import ContactRepository
    db_contact = await ContactRepository.get_by_key_or_prefix(request.destination)
    if not db_contact:
        raise HTTPException(
            status_code=404,
            detail=f"Contact not found in database: {request.destination}"
        )

    # Check if contact is on radio, if not add it
    contact = mc.get_contact_by_key_prefix(db_contact.public_key[:12])
    if not contact:
        logger.info("Adding contact %s to radio before sending", db_contact.public_key[:12])
        contact_data = db_contact.to_radio_dict()
        add_result = await mc.commands.add_contact(contact_data)
        if add_result.type == EventType.ERROR:
            logger.warning("Failed to add contact to radio: %s", add_result.payload)
            # Continue anyway - might still work

        # Get the contact from radio again
        contact = mc.get_contact_by_key_prefix(db_contact.public_key[:12])
        if not contact:
            # Use the contact_data we built as fallback
            contact = contact_data

    logger.info("Sending direct message to %s", db_contact.public_key[:12])

    result = await mc.commands.send_msg(
        dst=contact,
        msg=request.text,
    )

    if result.type == EventType.ERROR:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send message: {result.payload}"
        )

    # Store outgoing message
    now = int(time.time())
    message_id = await MessageRepository.create(
        msg_type="PRIV",
        text=request.text,
        conversation_key=db_contact.public_key,
        sender_timestamp=now,
        received_at=now,
        outgoing=True,
    )

    # Update last_contacted for the contact
    await ContactRepository.update_last_contacted(db_contact.public_key, now)

    # Track the expected ACK for this message
    expected_ack = result.payload.get("expected_ack")
    suggested_timeout = result.payload.get("suggested_timeout", 10000)  # default 10s
    if expected_ack:
        ack_code = expected_ack.hex() if isinstance(expected_ack, bytes) else expected_ack
        track_pending_ack(ack_code, message_id, suggested_timeout)
        logger.debug("Tracking ACK %s for message %d", ack_code, message_id)

    return Message(
        id=message_id,
        type="PRIV",
        conversation_key=db_contact.public_key,
        text=request.text,
        sender_timestamp=now,
        received_at=now,
        outgoing=True,
        acked=0,
    )


# Temporary radio slot used for sending channel messages
TEMP_RADIO_SLOT = 0


@router.post("/channel", response_model=Message)
async def send_channel_message(request: SendChannelMessageRequest) -> Message:
    """Send a message to a channel."""
    mc = require_connected()

    # Get channel info from our database
    from app.repository import ChannelRepository
    from app.decoder import calculate_channel_hash
    db_channel = await ChannelRepository.get_by_key(request.channel_key)
    if not db_channel:
        raise HTTPException(
            status_code=404,
            detail=f"Channel {request.channel_key} not found in database"
        )

    # Convert channel key hex to bytes
    try:
        key_bytes = bytes.fromhex(request.channel_key)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid channel key format: {request.channel_key}"
        )

    expected_hash = calculate_channel_hash(key_bytes)
    logger.info(
        "Sending to channel %s (%s) via radio slot %d, key hash: %s",
        request.channel_key, db_channel.name, TEMP_RADIO_SLOT, expected_hash
    )

    # Load the channel to a temporary radio slot before sending
    set_result = await mc.commands.set_channel(
        channel_idx=TEMP_RADIO_SLOT,
        channel_name=db_channel.name,
        channel_secret=key_bytes,
    )
    if set_result.type == EventType.ERROR:
        logger.warning(
            "Failed to set channel on radio slot %d before sending: %s",
            TEMP_RADIO_SLOT, set_result.payload
        )
        # Continue anyway - the channel might already be correctly configured

    logger.info("Sending channel message to %s: %s", db_channel.name, request.text[:50])

    result = await mc.commands.send_chan_msg(
        chan=TEMP_RADIO_SLOT,
        msg=request.text,
    )

    if result.type == EventType.ERROR:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send message: {result.payload}"
        )

    # Store outgoing message
    now = int(time.time())
    channel_key_upper = request.channel_key.upper()
    message_id = await MessageRepository.create(
        msg_type="CHAN",
        text=request.text,
        conversation_key=channel_key_upper,
        sender_timestamp=now,
        received_at=now,
        outgoing=True,
    )

    # Track for repeat detection (flood messages get confirmed by hearing repeats)
    track_pending_repeat(channel_key_upper, request.text, now, message_id)

    return Message(
        id=message_id,
        type="CHAN",
        conversation_key=channel_key_upper,
        text=request.text,
        sender_timestamp=now,
        received_at=now,
        outgoing=True,
        acked=0,
    )
