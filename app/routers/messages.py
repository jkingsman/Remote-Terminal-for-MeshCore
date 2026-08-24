import logging
import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.event_handlers import track_pending_ack
from app.mcmp import McmpAppCodec, MeshCompressor
from app.models import (
    Message,
    MessagesAroundResponse,
    ResendChannelMessageResponse,
    SendChannelMessageRequest,
    SendDirectMessageRequest,
)
from app.repository import AmbiguousPublicKeyPrefixError, AppSettingsRepository, MessageRepository
from app.services.message_send import (
    SCOPE_UNSET,
    resend_channel_message_record,
    send_channel_message_to_channel,
    send_direct_message_to_contact,
)
from app.services.radio_runtime import radio_runtime as radio_manager
from app.websocket import broadcast_error, broadcast_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/messages", tags=["messages"])


class MessageSizeEstimateRequest(BaseModel):
    text: str = Field(min_length=1)
    channel_key: str | None = Field(default=None)
    include_signature: bool = Field(default=False)


@router.post("/estimate-size")
async def estimate_message_size(request: MessageSizeEstimateRequest) -> dict:
    """Return estimated wire size of the message after MCMP v3 encoding.

    For channel messages, if the channel has MCMP enabled, the size is the
    length of the `mcmp3:` wire text. If signing is requested, a dummy 64-byte
    signature is used so the overhead is accurate. For messages that would be
    sent as plain text (MCMP disabled/model unavailable), returns UTF-8 length.
    """
    if not MeshCompressor.instance.is_ready:
        return {"size": len(request.text.encode('utf-8'))}
    try:
        if request.channel_key:
            # Channel path: may be signed
            if request.include_signature:
                dummy_signature = b'\x00' * 64
                encoded_body = McmpAppCodec.encode_body(
                    text=request.text,
                    timestamp=0,
                    signature=dummy_signature,
                )
            else:
                encoded_body = McmpAppCodec.encode_body(
                    text=request.text,
                    timestamp=0,
                )
            wire_text = McmpAppCodec.text_from_body(encoded_body.body)
            return {"size": len(wire_text)}
        else:
            # DM path: unsigned
            encoded_body = McmpAppCodec.encode_body(
                text=request.text,
                timestamp=0,
            )
            wire_text = McmpAppCodec.text_from_body(encoded_body.body)
            return {"size": len(wire_text)}
    except Exception:
        return {"size": len(request.text.encode('utf-8'))}