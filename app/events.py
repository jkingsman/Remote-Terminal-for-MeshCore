"""Typed WebSocket event contracts and serialization helpers."""

import json
import logging
from typing import Any, Literal, NotRequired

from pydantic import TypeAdapter
from typing_extensions import TypedDict

from app.models import BotLogEntry, Channel, Contact, Message, MessagePath, RawPacketBroadcast
from app.routers.health import HealthResponse

logger = logging.getLogger(__name__)

WsEventType = Literal[
    "health",
    "message",
    "contact",
    "contact_resolved",
    "channel",
    "contact_deleted",
    "channel_deleted",
    "raw_packet",
    "message_acked",
    "bot_log",
    "error",
    "success",
]


class ContactDeletedPayload(TypedDict):
    public_key: str


class ContactResolvedPayload(TypedDict):
    previous_public_key: str
    contact: Contact


class ChannelDeletedPayload(TypedDict):
    key: str


class MessageAckedPayload(TypedDict):
    message_id: int
    ack_count: int
    paths: NotRequired[list[MessagePath]]
    packet_id: NotRequired[int | None]


class ToastPayload(TypedDict):
    message: str
    details: NotRequired[str]


_PAYLOAD_ADAPTERS: dict[WsEventType, TypeAdapter[Any]] = {
    "health": TypeAdapter(HealthResponse),
    "message": TypeAdapter(Message),
    "contact": TypeAdapter(Contact),
    "contact_resolved": TypeAdapter(ContactResolvedPayload),
    "channel": TypeAdapter(Channel),
    "contact_deleted": TypeAdapter(ContactDeletedPayload),
    "channel_deleted": TypeAdapter(ChannelDeletedPayload),
    "raw_packet": TypeAdapter(RawPacketBroadcast),
    "message_acked": TypeAdapter(MessageAckedPayload),
    "bot_log": TypeAdapter(BotLogEntry),
    "error": TypeAdapter(ToastPayload),
    "success": TypeAdapter(ToastPayload),
}


def dump_ws_event(event_type: str, data: Any) -> str:
    """Serialize a WebSocket event envelope with validation for known event types."""
    adapter = _PAYLOAD_ADAPTERS.get(event_type)  # type: ignore[arg-type]
    if adapter is None:
        return json.dumps({"type": event_type, "data": data})

    try:
        validated = adapter.validate_python(data)
        payload = adapter.dump_python(validated, mode="json")
        return json.dumps({"type": event_type, "data": payload})
    except Exception:
        logger.exception(
            "Failed to validate WebSocket payload for event %s; falling back to raw JSON envelope",
            event_type,
        )
        return json.dumps({"type": event_type, "data": data})
