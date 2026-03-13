import logging
import time

from fastapi import APIRouter, HTTPException, Query

from app.dependencies import require_connected
from app.event_handlers import track_pending_ack
from app.models import (
    Message,
    MessagesAroundResponse,
    SendChannelMessageRequest,
    SendDirectMessageRequest,
)
from app.repository import AmbiguousPublicKeyPrefixError, AppSettingsRepository, MessageRepository
from app.services.message_send import (
    resend_channel_message_record,
    send_channel_message_to_channel,
    send_direct_message_to_contact,
)
from app.services.radio_runtime import radio_runtime as radio_manager
from app.websocket import broadcast_error, broadcast_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("/around/{message_id}", response_model=MessagesAroundResponse)
async def get_messages_around(
    message_id: int,
    type: str | None = Query(default=None, description="Filter by type: PRIV or CHAN"),
    conversation_key: str | None = Query(default=None, description="Filter by conversation key"),
    context: int = Query(default=100, ge=1, le=500, description="Number of messages before/after"),
) -> MessagesAroundResponse:
    """Get messages around a specific message for jump-to-message navigation."""
    settings = await AppSettingsRepository.get()
    blocked_keys = settings.blocked_keys or None
    blocked_names = settings.blocked_names or None
    messages, has_older, has_newer = await MessageRepository.get_around(
        message_id=message_id,
        msg_type=type,
        conversation_key=conversation_key,
        context_size=context,
        blocked_keys=blocked_keys,
        blocked_names=blocked_names,
    )
    return MessagesAroundResponse(messages=messages, has_older=has_older, has_newer=has_newer)


@router.get("", response_model=list[Message])
async def list_messages(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    type: str | None = Query(default=None, description="Filter by type: PRIV or CHAN"),
    conversation_key: str | None = Query(
        default=None, description="Filter by conversation key (channel key or contact pubkey)"
    ),
    before: int | None = Query(
        default=None, description="Cursor: received_at of last seen message"
    ),
    before_id: int | None = Query(default=None, description="Cursor: id of last seen message"),
    after: int | None = Query(
        default=None, description="Forward cursor: received_at of last seen message"
    ),
    after_id: int | None = Query(
        default=None, description="Forward cursor: id of last seen message"
    ),
    q: str | None = Query(default=None, description="Full-text search query"),
) -> list[Message]:
    """List messages from the database."""
    settings = await AppSettingsRepository.get()
    blocked_keys = settings.blocked_keys or None
    blocked_names = settings.blocked_names or None
    return await MessageRepository.get_all(
        limit=limit,
        offset=offset,
        msg_type=type,
        conversation_key=conversation_key,
        before=before,
        before_id=before_id,
        after=after,
        after_id=after_id,
        q=q,
        blocked_keys=blocked_keys,
        blocked_names=blocked_names,
    )


@router.post("/direct", response_model=Message)
async def send_direct_message(request: SendDirectMessageRequest) -> Message:
    """Send a direct message to a contact."""
    require_connected()

    # First check our database for the contact
    from app.repository import ContactRepository

    try:
        db_contact = await ContactRepository.get_by_key_or_prefix(request.destination)
    except AmbiguousPublicKeyPrefixError as err:
        sample = ", ".join(key[:12] for key in err.matches[:2])
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ambiguous destination key prefix '{err.prefix}'. "
                f"Use a full 64-character public key. Matching contacts: {sample}"
            ),
        ) from err
    if not db_contact:
        raise HTTPException(
            status_code=404, detail=f"Contact not found in database: {request.destination}"
        )
    if len(db_contact.public_key) < 64:
        raise HTTPException(
            status_code=409,
            detail="Cannot send to an unresolved prefix-only contact until a full key is known",
        )

    return await send_direct_message_to_contact(
        contact=db_contact,
        text=request.text,
        radio_manager=radio_manager,
        broadcast_fn=broadcast_event,
        track_pending_ack_fn=track_pending_ack,
        now_fn=time.time,
        message_repository=MessageRepository,
        contact_repository=ContactRepository,
    )


# Preferred first radio slot used for sending channel messages.
# The send service may reuse/load other app-managed slots depending on transport
# and session cache state.
TEMP_RADIO_SLOT = 0


@router.post("/channel", response_model=Message)
async def send_channel_message(request: SendChannelMessageRequest) -> Message:
    """Send a message to a channel."""
    require_connected()

    # Get channel info from our database
    from app.repository import ChannelRepository

    db_channel = await ChannelRepository.get_by_key(request.channel_key)
    if not db_channel:
        raise HTTPException(
            status_code=404, detail=f"Channel {request.channel_key} not found in database"
        )

    # Convert channel key hex to bytes
    try:
        key_bytes = bytes.fromhex(request.channel_key)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid channel key format: {request.channel_key}"
        ) from None

    return await send_channel_message_to_channel(
        channel=db_channel,
        channel_key_upper=request.channel_key.upper(),
        key_bytes=key_bytes,
        text=request.text,
        radio_manager=radio_manager,
        broadcast_fn=broadcast_event,
        error_broadcast_fn=broadcast_error,
        now_fn=time.time,
        temp_radio_slot=TEMP_RADIO_SLOT,
        message_repository=MessageRepository,
    )


RESEND_WINDOW_SECONDS = 30


@router.post("/channel/{message_id}/resend")
async def resend_channel_message(
    message_id: int,
    new_timestamp: bool = Query(default=False),
) -> dict:
    """Resend a channel message.

    When new_timestamp=False (default): byte-perfect resend using the original timestamp.
    Only allowed within 30 seconds of the original send.

    When new_timestamp=True: resend with a fresh timestamp so repeaters treat it as a
    new packet. Creates a new message row in the database. No time window restriction.
    """
    require_connected()

    from app.repository import ChannelRepository

    msg = await MessageRepository.get_by_id(message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    if not msg.outgoing:
        raise HTTPException(status_code=400, detail="Can only resend outgoing messages")

    if msg.type != "CHAN":
        raise HTTPException(status_code=400, detail="Can only resend channel messages")

    if msg.sender_timestamp is None:
        raise HTTPException(status_code=400, detail="Message has no timestamp")

    # Byte-perfect resend enforces the 30s window; new-timestamp resend does not
    if not new_timestamp:
        elapsed = int(time.time()) - msg.sender_timestamp
        if elapsed > RESEND_WINDOW_SECONDS:
            raise HTTPException(status_code=400, detail="Resend window has expired (30 seconds)")

    db_channel = await ChannelRepository.get_by_key(msg.conversation_key)
    if not db_channel:
        raise HTTPException(status_code=404, detail=f"Channel {msg.conversation_key} not found")

    return await resend_channel_message_record(
        message=msg,
        channel=db_channel,
        new_timestamp=new_timestamp,
        radio_manager=radio_manager,
        broadcast_fn=broadcast_event,
        error_broadcast_fn=broadcast_error,
        now_fn=time.time,
        temp_radio_slot=TEMP_RADIO_SLOT,
        message_repository=MessageRepository,
    )
