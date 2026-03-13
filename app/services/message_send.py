"""Shared send/resend orchestration for outgoing messages."""

import logging
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from meshcore import EventType

from app.region_scope import normalize_region_scope
from app.repository import AppSettingsRepository, ContactRepository, MessageRepository
from app.services.messages import (
    build_message_model,
    create_outgoing_channel_message,
    create_outgoing_direct_message,
)

logger = logging.getLogger(__name__)

BroadcastFn = Callable[..., Any]
TrackAckFn = Callable[[str, int, int], None]
NowFn = Callable[[], float]


async def send_channel_message_with_effective_scope(
    *,
    mc,
    channel,
    channel_key: str,
    key_bytes: bytes,
    text: str,
    timestamp_bytes: bytes,
    action_label: str,
    radio_manager,
    temp_radio_slot: int,
    error_broadcast_fn: BroadcastFn,
    app_settings_repository=AppSettingsRepository,
) -> Any:
    """Send a channel message, temporarily overriding flood scope when configured."""
    override_scope = normalize_region_scope(channel.flood_scope_override)
    baseline_scope = ""

    if override_scope:
        settings = await app_settings_repository.get()
        baseline_scope = normalize_region_scope(settings.flood_scope)

    if override_scope and override_scope != baseline_scope:
        logger.info(
            "Temporarily applying channel flood_scope override for %s: %r",
            channel.name,
            override_scope,
        )
        override_result = await mc.commands.set_flood_scope(override_scope)
        if override_result is not None and override_result.type == EventType.ERROR:
            logger.warning(
                "Failed to apply channel flood_scope override for %s: %s",
                channel.name,
                override_result.payload,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Failed to apply regional override {override_scope!r} before {action_label}: "
                    f"{override_result.payload}"
                ),
            )

    try:
        channel_slot, needs_configure, evicted_channel_key = radio_manager.plan_channel_send_slot(
            channel_key,
            preferred_slot=temp_radio_slot,
        )
        if needs_configure:
            logger.debug(
                "Loading channel %s into radio slot %d before %s%s",
                channel.name,
                channel_slot,
                action_label,
                (
                    f" (evicting cached {evicted_channel_key[:8]})"
                    if evicted_channel_key is not None
                    else ""
                ),
            )
            set_result = await mc.commands.set_channel(
                channel_idx=channel_slot,
                channel_name=channel.name,
                channel_secret=key_bytes,
            )
            if set_result.type == EventType.ERROR:
                logger.warning(
                    "Failed to set channel on radio slot %d before %s: %s",
                    channel_slot,
                    action_label,
                    set_result.payload,
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to configure channel on radio before {action_label}",
                )
            radio_manager.note_channel_slot_loaded(channel_key, channel_slot)
        else:
            logger.debug(
                "Reusing cached radio slot %d for channel %s before %s",
                channel_slot,
                channel.name,
                action_label,
            )

        send_result = await mc.commands.send_chan_msg(
            chan=channel_slot,
            msg=text,
            timestamp=timestamp_bytes,
        )
        if send_result.type == EventType.ERROR:
            radio_manager.invalidate_cached_channel_slot(channel_key)
        else:
            radio_manager.note_channel_slot_used(channel_key)
        return send_result
    finally:
        if override_scope and override_scope != baseline_scope:
            try:
                restore_result = await mc.commands.set_flood_scope(
                    baseline_scope if baseline_scope else ""
                )
                if restore_result is not None and restore_result.type == EventType.ERROR:
                    logger.error(
                        "Failed to restore baseline flood_scope after sending to %s: %s",
                        channel.name,
                        restore_result.payload,
                    )
                    error_broadcast_fn(
                        "Regional override restore failed",
                        (
                            f"Sent to {channel.name}, but restoring flood scope failed. "
                            "The radio may still be region-scoped. Consider rebooting the radio."
                        ),
                    )
                else:
                    logger.debug(
                        "Restored baseline flood_scope after channel send: %r",
                        baseline_scope or "(disabled)",
                    )
            except Exception:
                logger.exception(
                    "Failed to restore baseline flood_scope after sending to %s",
                    channel.name,
                )
                error_broadcast_fn(
                    "Regional override restore failed",
                    (
                        f"Sent to {channel.name}, but restoring flood scope failed. "
                        "The radio may still be region-scoped. Consider rebooting the radio."
                    ),
                )


async def send_direct_message_to_contact(
    *,
    contact,
    text: str,
    radio_manager,
    broadcast_fn: BroadcastFn,
    track_pending_ack_fn: TrackAckFn,
    now_fn: NowFn,
    message_repository=MessageRepository,
    contact_repository=ContactRepository,
) -> Any:
    """Send a direct message and persist/broadcast the outgoing row."""
    contact_data = contact.to_radio_dict()
    contact_ensured_on_radio = False
    async with radio_manager.radio_operation("send_direct_message") as mc:
        logger.debug("Ensuring contact %s is on radio before sending", contact.public_key[:12])
        add_result = await mc.commands.add_contact(contact_data)
        if add_result.type == EventType.ERROR:
            logger.warning("Failed to add contact to radio: %s", add_result.payload)
        else:
            contact_ensured_on_radio = True

        cached_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
        if not cached_contact:
            cached_contact = contact_data
        else:
            contact_ensured_on_radio = True

        logger.info("Sending direct message to %s", contact.public_key[:12])
        now = int(now_fn())
        result = await mc.commands.send_msg(
            dst=cached_contact,
            msg=text,
            timestamp=now,
        )

    if result.type == EventType.ERROR:
        raise HTTPException(status_code=500, detail=f"Failed to send message: {result.payload}")

    if contact_ensured_on_radio and not contact.on_radio:
        await contact_repository.set_on_radio(contact.public_key.lower(), True)

    message = await create_outgoing_direct_message(
        conversation_key=contact.public_key.lower(),
        text=text,
        sender_timestamp=now,
        received_at=now,
        broadcast_fn=broadcast_fn,
        message_repository=message_repository,
    )
    if message is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to store outgoing message - unexpected duplicate",
        )

    await contact_repository.update_last_contacted(contact.public_key.lower(), now)

    expected_ack = result.payload.get("expected_ack")
    suggested_timeout: int = result.payload.get("suggested_timeout", 10000)
    if expected_ack:
        ack_code = expected_ack.hex() if isinstance(expected_ack, bytes) else expected_ack
        track_pending_ack_fn(ack_code, message.id, suggested_timeout)
        logger.debug("Tracking ACK %s for message %d", ack_code, message.id)

    return message


async def send_channel_message_to_channel(
    *,
    channel,
    channel_key_upper: str,
    key_bytes: bytes,
    text: str,
    radio_manager,
    broadcast_fn: BroadcastFn,
    error_broadcast_fn: BroadcastFn,
    now_fn: NowFn,
    temp_radio_slot: int,
    message_repository=MessageRepository,
) -> Any:
    """Send a channel message and persist/broadcast the outgoing row."""
    now: int | None = None
    radio_name = ""
    our_public_key: str | None = None
    text_with_sender = text

    async with radio_manager.radio_operation("send_channel_message") as mc:
        radio_name = mc.self_info.get("name", "") if mc.self_info else ""
        our_public_key = (mc.self_info.get("public_key") or None) if mc.self_info else None
        text_with_sender = f"{radio_name}: {text}" if radio_name else text
        logger.info("Sending channel message to %s: %s", channel.name, text[:50])

        now = int(now_fn())
        timestamp_bytes = now.to_bytes(4, "little")

        result = await send_channel_message_with_effective_scope(
            mc=mc,
            channel=channel,
            channel_key=channel_key_upper,
            key_bytes=key_bytes,
            text=text,
            timestamp_bytes=timestamp_bytes,
            action_label="sending message",
            radio_manager=radio_manager,
            temp_radio_slot=temp_radio_slot,
            error_broadcast_fn=error_broadcast_fn,
        )

        if result.type == EventType.ERROR:
            raise HTTPException(status_code=500, detail=f"Failed to send message: {result.payload}")

    if now is None:
        raise HTTPException(status_code=500, detail="Failed to store outgoing message")

    outgoing_message = await create_outgoing_channel_message(
        conversation_key=channel_key_upper,
        text=text_with_sender,
        sender_timestamp=now,
        received_at=now,
        sender_name=radio_name or None,
        sender_key=our_public_key,
        channel_name=channel.name,
        broadcast_fn=broadcast_fn,
        message_repository=message_repository,
    )
    if outgoing_message is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to store outgoing message - unexpected duplicate",
        )

    message_id = outgoing_message.id
    acked_count, paths = await message_repository.get_ack_and_paths(message_id)
    return build_message_model(
        message_id=message_id,
        msg_type="CHAN",
        conversation_key=channel_key_upper,
        text=text_with_sender,
        sender_timestamp=now,
        received_at=now,
        paths=paths,
        outgoing=True,
        acked=acked_count,
        sender_name=radio_name or None,
        sender_key=our_public_key,
        channel_name=channel.name,
    )


async def resend_channel_message_record(
    *,
    message,
    channel,
    new_timestamp: bool,
    radio_manager,
    broadcast_fn: BroadcastFn,
    error_broadcast_fn: BroadcastFn,
    now_fn: NowFn,
    temp_radio_slot: int,
    message_repository=MessageRepository,
) -> dict[str, Any]:
    """Resend a stored outgoing channel message."""
    try:
        key_bytes = bytes.fromhex(message.conversation_key)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid channel key format: {message.conversation_key}",
        ) from None

    now: int | None = None
    if new_timestamp:
        now = int(now_fn())
        timestamp_bytes = now.to_bytes(4, "little")
    else:
        timestamp_bytes = message.sender_timestamp.to_bytes(4, "little")

    resend_public_key: str | None = None
    radio_name = ""

    async with radio_manager.radio_operation("resend_channel_message") as mc:
        radio_name = mc.self_info.get("name", "") if mc.self_info else ""
        resend_public_key = (mc.self_info.get("public_key") or None) if mc.self_info else None
        text_to_send = message.text
        if radio_name and text_to_send.startswith(f"{radio_name}: "):
            text_to_send = text_to_send[len(f"{radio_name}: ") :]

        result = await send_channel_message_with_effective_scope(
            mc=mc,
            channel=channel,
            channel_key=message.conversation_key,
            key_bytes=key_bytes,
            text=text_to_send,
            timestamp_bytes=timestamp_bytes,
            action_label="resending message",
            radio_manager=radio_manager,
            temp_radio_slot=temp_radio_slot,
            error_broadcast_fn=error_broadcast_fn,
        )
        if result.type == EventType.ERROR:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to resend message: {result.payload}",
            )

    if new_timestamp:
        if now is None:
            raise HTTPException(status_code=500, detail="Failed to assign resend timestamp")
        new_message = await create_outgoing_channel_message(
            conversation_key=message.conversation_key,
            text=message.text,
            sender_timestamp=now,
            received_at=now,
            sender_name=radio_name or None,
            sender_key=resend_public_key,
            channel_name=channel.name,
            broadcast_fn=broadcast_fn,
            message_repository=message_repository,
        )
        if new_message is None:
            logger.warning(
                "Duplicate timestamp collision resending message %d — radio sent but DB row not created",
                message.id,
            )
            return {"status": "ok", "message_id": message.id}

        logger.info(
            "Resent channel message %d as new message %d to %s",
            message.id,
            new_message.id,
            channel.name,
        )
        return {"status": "ok", "message_id": new_message.id}

    logger.info("Resent channel message %d to %s", message.id, channel.name)
    return {"status": "ok", "message_id": message.id}
