"""Shared send/resend orchestration for outgoing messages."""

import asyncio
import logging
import time as _time
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from meshcore import EventType

from app.compression import encode_outbound
from app.models import ResendChannelMessageResponse
from app.radio import RadioOperationBusyError
from app.region_scope import is_unscoped, normalize_region_scope
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
    MessageRepository,
)
from app.services import dm_ack_tracker
from app.services.flood_scope import set_radio_flood_scope
from app.services.messages import (
    BroadcastFn,
    broadcast_message,
    build_stored_outgoing_channel_message,
    create_outgoing_channel_message,
    create_outgoing_direct_message,
    increment_ack_and_broadcast,
)

logger = logging.getLogger(__name__)


class _ScopeUnset:
    """Sentinel for "no per-send flood-scope override supplied".

    Distinguishes "caller did not request a per-send scope, so use the channel's
    persisted ``flood_scope_override``" from "caller explicitly requested unscoped
    flood (empty string)". A bare ``None``/``""`` cannot express both states.
    """

    __slots__ = ()


SCOPE_UNSET = _ScopeUnset()

NO_RADIO_RESPONSE_AFTER_SEND_DETAIL = (
    "Send command was issued to the radio, but no response was heard back. "
    "The message may or may not have sent successfully."
)
TrackAckFn = Callable[[str, int, int], bool]
NowFn = Callable[[], float]
OutgoingReservationKey = tuple[str, str, str]
RetryTaskScheduler = Callable[[Any], Any]

# Channel echo watchdog: delay before checking for echoes
ECHO_WATCHDOG_DELAY_SECONDS = 2.0

# Byte-perfect resend window (must match router's RESEND_WINDOW_SECONDS)
RESEND_WINDOW_SECONDS = 30

# Temp radio slot used by the router for channel sends
WATCHDOG_TEMP_RADIO_SLOT = 0

_pending_outgoing_timestamp_reservations: dict[OutgoingReservationKey, set[int]] = {}
_outgoing_timestamp_reservations_lock = asyncio.Lock()

DM_SEND_MAX_ATTEMPTS = 3
DEFAULT_DM_ACK_TIMEOUT_MS = 10000
DM_RETRY_WAIT_MARGIN = 1.2


async def allocate_outgoing_sender_timestamp(
    *,
    message_repository,
    msg_type: str,
    conversation_key: str,
    text: str,
    requested_timestamp: int,
) -> int:
    """Pick a sender timestamp that will not collide with an existing stored message."""
    reservation_key = (msg_type, conversation_key, text)
    candidate = requested_timestamp
    while True:
        async with _outgoing_timestamp_reservations_lock:
            reserved = _pending_outgoing_timestamp_reservations.get(reservation_key, set())
            is_reserved = candidate in reserved

        if is_reserved:
            candidate += 1
            continue

        existing = await message_repository.get_by_content(
            msg_type=msg_type,
            conversation_key=conversation_key,
            text=text,
            sender_timestamp=candidate,
        )
        if existing is not None:
            candidate += 1
            continue

        async with _outgoing_timestamp_reservations_lock:
            reserved = _pending_outgoing_timestamp_reservations.setdefault(reservation_key, set())
            if candidate in reserved:
                candidate += 1
                continue
            reserved.add(candidate)
            break

    if candidate != requested_timestamp:
        logger.info(
            "Bumped outgoing %s timestamp for %s from %d to %d to avoid same-content collision",
            msg_type,
            conversation_key[:12],
            requested_timestamp,
            candidate,
        )

    return candidate


async def release_outgoing_sender_timestamp(
    *,
    msg_type: str,
    conversation_key: str,
    text: str,
    sender_timestamp: int,
) -> None:
    reservation_key = (msg_type, conversation_key, text)
    async with _outgoing_timestamp_reservations_lock:
        reserved = _pending_outgoing_timestamp_reservations.get(reservation_key)
        if not reserved:
            return
        reserved.discard(sender_timestamp)
        if not reserved:
            _pending_outgoing_timestamp_reservations.pop(reservation_key, None)


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
    flood_scope_override: str | _ScopeUnset = SCOPE_UNSET,
    app_settings_repository=AppSettingsRepository,
) -> Any:
    """Send a channel message, temporarily overriding flood scope and/or path hash mode.

    ``flood_scope_override`` lets a single send override the channel's persisted
    ``flood_scope_override``: pass a region name to scope this send, an empty
    string to force unscoped/plain flood, or leave it ``SCOPE_UNSET`` to fall
    back to the channel's persisted override.
    """
    if isinstance(flood_scope_override, _ScopeUnset):
        # Fall back to the channel's persisted override, which is tri-state:
        #   None -> inherit the global scope (leave radio untouched)
        #   unscoped marker ("*") -> force unscoped even over a scoped global
        #   region name -> scope this channel
        channel_override = channel.flood_scope_override
        if channel_override is None:
            desired_scope = ""
            scope_explicit = False
        elif is_unscoped(channel_override):
            desired_scope = ""
            scope_explicit = True
        else:
            desired_scope = normalize_region_scope(channel_override)
            scope_explicit = True
    else:
        desired_scope = normalize_region_scope(flood_scope_override)
        scope_explicit = True

    # Fetch the radio's standing scope as the restore target whenever we might
    # change it: a non-empty desired scope, or an explicit request to go unscoped.
    baseline_scope = ""
    if desired_scope or scope_explicit:
        settings = await app_settings_repository.get()
        baseline_scope = normalize_region_scope(settings.flood_scope)

    # Apply only when the desired scope differs from the radio's baseline. A blank
    # desired scope forces unscoped only when explicitly requested; the implicit
    # (channel-default) path leaves the radio untouched when no override is set.
    apply_scope = desired_scope != baseline_scope and (bool(desired_scope) or scope_explicit)

    # Path hash mode per-channel override
    override_phm = channel.path_hash_mode_override
    baseline_phm = radio_manager.path_hash_mode
    apply_phm = (
        override_phm is not None
        and radio_manager.path_hash_mode_supported
        and override_phm != baseline_phm
    )

    # Apply the temporary overrides and send inside the try so the finally below
    # always restores the radio's baseline scope/hash-mode -- even when applying an
    # override raises (e.g. the radio rejects the scope, or the path-hash-mode apply
    # fails) or the send itself throws. ``apply_scope``/``apply_phm`` are computed
    # above so the finally can reference them regardless of where we fail, and
    # restoring to baseline is idempotent, so forcing baseline back after an apply
    # whose effect on the radio is unknown is safe.
    try:
        if apply_scope:
            logger.info(
                "Temporarily applying flood_scope %s for %s",
                desired_scope or "(unscoped)",
                channel.name,
            )
            override_result = await set_radio_flood_scope(
                mc, desired_scope, fw_ver=radio_manager.firmware_ver_code
            )
            if override_result is not None and override_result.type == EventType.ERROR:
                logger.warning(
                    "Failed to apply flood_scope %r for %s: %s",
                    desired_scope,
                    channel.name,
                    override_result.payload,
                )
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Failed to apply regional override {desired_scope!r} before {action_label}: "
                        f"{override_result.payload}"
                    ),
                )

        if apply_phm:
            logger.info(
                "Temporarily applying channel path_hash_mode override for %s: %d",
                channel.name,
                override_phm,
            )
            phm_result = await mc.commands.set_path_hash_mode(override_phm)
            if phm_result is not None and phm_result.type == EventType.ERROR:
                logger.warning(
                    "Failed to apply channel path_hash_mode override for %s: %s",
                    channel.name,
                    phm_result.payload,
                )
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Failed to apply path hash mode override before {action_label}: "
                        f"{phm_result.payload}"
                    ),
                )

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
            try:
                set_result = await mc.commands.set_channel(
                    channel_idx=channel_slot,
                    channel_name=channel.name,
                    channel_secret=key_bytes,
                )
            except Exception:
                if evicted_channel_key is not None:
                    radio_manager.invalidate_cached_channel_slot(evicted_channel_key)
                raise
            if set_result.type == EventType.ERROR:
                if evicted_channel_key is not None:
                    radio_manager.invalidate_cached_channel_slot(evicted_channel_key)
                logger.warning(
                    "Failed to set channel on radio slot %d before %s: %s",
                    channel_slot,
                    action_label,
                    set_result.payload,
                )
                raise HTTPException(
                    status_code=422,
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

        # Compress on the wire when the channel opts in. Only the transmitted
        # body is compressed; callers persist the plaintext (with the sender
        # prefix) separately. The firmware prepends "<name>: " to what we send,
        # so the sender name stays outside the compressed payload -- exactly how
        # meshcore-open frames it, and the channel budget already reserves room
        # for the prefix. encode_outbound() is deterministic (given the same
        # timestamp), so a resend produces identical wire bytes.
        wire_msg = (
            encode_outbound(
                text,
                version=channel.mcmp_version,
                timestamp=int.from_bytes(timestamp_bytes, "little"),
            )
            if channel.mcmp_enabled
            else text
        )
        if wire_msg != text:
            logger.debug(
                "MCMP-compressed channel body for %s (%d -> %d bytes)",
                channel.name,
                len(text.encode("utf-8")),
                len(wire_msg.encode("utf-8")),
            )
        send_result = await mc.commands.send_chan_msg(
            chan=channel_slot,
            msg=wire_msg,
            timestamp=timestamp_bytes,
        )
        if send_result is None:
            logger.warning(
                "No response from radio after %s for channel %s; send outcome is unknown",
                action_label,
                channel.name,
            )
            raise HTTPException(status_code=408, detail=NO_RADIO_RESPONSE_AFTER_SEND_DETAIL)
        if send_result.type == EventType.ERROR:
            logger.error(
                "Radio returned error during %s for channel %s: %s",
                action_label,
                channel.name,
                send_result.payload,
            )
            radio_manager.invalidate_cached_channel_slot(channel_key)
        else:
            logger.debug(
                "Radio send result for %s (%s): %r",
                channel.name,
                action_label,
                send_result.payload,
            )
            radio_manager.note_channel_slot_used(channel_key)
        return send_result
    finally:
        if apply_scope:
            restored = False
            for attempt in range(3):
                try:
                    restore_result = await set_radio_flood_scope(
                        mc, baseline_scope, fw_ver=radio_manager.firmware_ver_code
                    )
                    if restore_result is not None and restore_result.type == EventType.ERROR:
                        logger.warning(
                            "Attempt %d/3: failed to restore flood_scope after sending to %s: %s",
                            attempt + 1,
                            channel.name,
                            restore_result.payload,
                        )
                    else:
                        logger.debug(
                            "Restored baseline flood_scope after channel send: %r",
                            baseline_scope or "(disabled)",
                        )
                        restored = True
                        break
                except Exception:
                    logger.exception(
                        "Attempt %d/3: exception restoring flood_scope after sending to %s",
                        attempt + 1,
                        channel.name,
                    )
            if not restored:
                logger.error(
                    "All 3 attempts to restore flood_scope failed for %s",
                    channel.name,
                )
                error_broadcast_fn(
                    "Regional override restore failed",
                    (
                        f"Sent to {channel.name}, but restoring flood scope failed "
                        f"after 3 attempts. The radio may still be region-scoped. "
                        f"Consider rebooting the radio."
                    ),
                )

        if apply_phm:
            restored = False
            for attempt in range(3):
                try:
                    restore_phm = await mc.commands.set_path_hash_mode(baseline_phm)
                    if restore_phm is not None and restore_phm.type == EventType.ERROR:
                        logger.warning(
                            "Attempt %d/3: failed to restore path_hash_mode after sending to %s: %s",
                            attempt + 1,
                            channel.name,
                            restore_phm.payload,
                        )
                    else:
                        radio_manager.path_hash_mode = baseline_phm
                        logger.debug(
                            "Restored baseline path_hash_mode after channel send: %d",
                            baseline_phm,
                        )
                        restored = True
                        break
                except Exception:
                    logger.exception(
                        "Attempt %d/3: exception restoring path_hash_mode after sending to %s",
                        attempt + 1,
                        channel.name,
                    )
            if not restored:
                logger.error(
                    "All 3 attempts to restore path_hash_mode failed for %s",
                    channel.name,
                )
                error_broadcast_fn(
                    "Path hash mode restore failed",
                    (
                        f"Sent to {channel.name}, but restoring path hash mode failed "
                        f"after 3 attempts. The radio is still using a non-default hop "
                        f"width. Set it back manually in Radio settings."
                    ),
                )


def _extract_expected_ack_code(result: Any) -> str | None:
    if result is None or result.type == EventType.ERROR:
        return None
    payload = result.payload or {}
    expected_ack = payload.get("expected_ack")
    if not expected_ack:
        return None
    return expected_ack.hex() if isinstance(expected_ack, bytes) else expected_ack


def _get_ack_tracking_timeout_ms(result: Any) -> int:
    if result is None or result.type == EventType.ERROR:
        return DEFAULT_DM_ACK_TIMEOUT_MS
    payload = result.payload or {}
    suggested_timeout = payload.get("suggested_timeout")
    if suggested_timeout is None:
        return DEFAULT_DM_ACK_TIMEOUT_MS
    try:
        return max(1, int(suggested_timeout))
    except (TypeError, ValueError):
        return DEFAULT_DM_ACK_TIMEOUT_MS


def _get_direct_message_retry_timeout_ms(result: Any) -> int:
    """Return the ACK window to wait before retrying a DM.

    The MeshCore firmware already computes and returns `suggested_timeout` in
    `PACKET_MSG_SENT`, derived from estimated packet airtime and route mode.
    We use that firmware-supplied window directly so retries do not fire before
    the radio's own ACK timeout expires.

    Sources:
    - https://github.com/meshcore-dev/MeshCore/blob/main/src/helpers/BaseChatMesh.cpp
    - https://github.com/meshcore-dev/MeshCore/blob/main/examples/companion_radio/MyMesh.cpp
    - https://github.com/meshcore-dev/MeshCore/blob/main/docs/companion_protocol.md
    """
    return _get_ack_tracking_timeout_ms(result)


async def _apply_direct_message_ack_tracking(
    *,
    result: Any,
    message_id: int,
    track_pending_ack_fn: TrackAckFn,
    broadcast_fn: BroadcastFn,
) -> int:
    ack_code = _extract_expected_ack_code(result)
    if not ack_code:
        return 0

    timeout_ms = _get_ack_tracking_timeout_ms(result)
    matched_immediately = track_pending_ack_fn(ack_code, message_id, timeout_ms) is True
    logger.debug("Tracking ACK %s for message %d", ack_code, message_id)
    if matched_immediately:
        dm_ack_tracker.clear_pending_acks_for_message(message_id)
        return await increment_ack_and_broadcast(
            message_id=message_id,
            broadcast_fn=broadcast_fn,
        )
    return 0


async def _is_message_acked(*, message_id: int, message_repository) -> bool:
    acked_count, _paths = await message_repository.get_ack_and_paths(message_id)
    return acked_count > 0


async def _retry_direct_message_until_acked(
    *,
    contact,
    text: str,
    message_id: int,
    sender_timestamp: int,
    radio_manager,
    track_pending_ack_fn: TrackAckFn,
    broadcast_fn: BroadcastFn,
    wait_timeout_ms: int,
    sleep_fn,
    message_repository,
) -> None:
    next_wait_timeout_ms = wait_timeout_ms
    attempt = 1
    while attempt < DM_SEND_MAX_ATTEMPTS:
        await sleep_fn((next_wait_timeout_ms / 1000) * DM_RETRY_WAIT_MARGIN)
        if await _is_message_acked(message_id=message_id, message_repository=message_repository):
            return

        try:
            async with radio_manager.radio_operation("retry_direct_message") as mc:
                contact_data = contact.to_radio_dict()
                add_result = await mc.commands.add_contact(contact_data)
                if add_result.type == EventType.ERROR:
                    logger.warning(
                        "Failed to reload contact %s on radio before DM retry: %s",
                        contact.public_key[:12],
                        add_result.payload,
                    )
                cached_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
                if not cached_contact:
                    cached_contact = contact_data

                if attempt == DM_SEND_MAX_ATTEMPTS - 1:
                    reset_result = await mc.commands.reset_path(contact.public_key)
                    if reset_result is None:
                        logger.warning(
                            "No response from radio for reset_path to %s before final DM retry",
                            contact.public_key[:12],
                        )
                    elif reset_result.type == EventType.ERROR:
                        logger.warning(
                            "Failed to reset path before final DM retry to %s: %s",
                            contact.public_key[:12],
                            reset_result.payload,
                        )
                    refreshed_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
                    if refreshed_contact:
                        cached_contact = refreshed_contact

                result = await mc.commands.send_msg(
                    dst=cached_contact,
                    msg=(
                        encode_outbound(
                            text, version=contact.mcmp_version, timestamp=sender_timestamp
                        )
                        if contact.mcmp_enabled
                        else text
                    ),
                    timestamp=sender_timestamp,
                    attempt=attempt,
                )
        except RadioOperationBusyError:
            logger.debug(
                "Radio busy during DM retry attempt %d/%d for %s, will retry without consuming attempt",
                attempt + 1,
                DM_SEND_MAX_ATTEMPTS,
                contact.public_key[:12],
            )
            continue
        except Exception:
            logger.exception(
                "Background DM retry attempt %d/%d failed for %s",
                attempt + 1,
                DM_SEND_MAX_ATTEMPTS,
                contact.public_key[:12],
            )
            attempt += 1
            continue

        if result is None:
            logger.warning(
                "No response from radio after background DM retry attempt %d/%d to %s",
                attempt + 1,
                DM_SEND_MAX_ATTEMPTS,
                contact.public_key[:12],
            )
            attempt += 1
            continue

        if result.type == EventType.ERROR:
            logger.warning(
                "Background DM retry attempt %d/%d failed for %s: %s",
                attempt + 1,
                DM_SEND_MAX_ATTEMPTS,
                contact.public_key[:12],
                result.payload,
            )
            attempt += 1
            continue

        if await _is_message_acked(message_id=message_id, message_repository=message_repository):
            return

        ack_code = _extract_expected_ack_code(result)
        if not ack_code:
            logger.debug(
                "Background DM retry attempt %d/%d for %s returned no expected_ack; "
                "continuing with previous timeout",
                attempt + 1,
                DM_SEND_MAX_ATTEMPTS,
                contact.public_key[:12],
            )
            attempt += 1
            continue

        next_wait_timeout_ms = _get_direct_message_retry_timeout_ms(result)

        ack_count = await _apply_direct_message_ack_tracking(
            result=result,
            message_id=message_id,
            track_pending_ack_fn=track_pending_ack_fn,
            broadcast_fn=broadcast_fn,
        )
        if ack_count > 0:
            return

        attempt += 1


async def send_direct_message_to_contact(
    *,
    contact,
    text: str,
    radio_manager,
    broadcast_fn: BroadcastFn,
    track_pending_ack_fn: TrackAckFn,
    now_fn: NowFn,
    retry_task_scheduler: RetryTaskScheduler | None = None,
    retry_sleep_fn=None,
    message_repository=MessageRepository,
    contact_repository=ContactRepository,
) -> Any:
    """Send a direct message and persist/broadcast the outgoing row."""
    if retry_task_scheduler is None:
        retry_task_scheduler = asyncio.create_task
    if retry_sleep_fn is None:
        retry_sleep_fn = asyncio.sleep

    contact_data = contact.to_radio_dict()
    sent_at: int | None = None
    sender_timestamp: int | None = None
    message = None
    result = None
    try:
        async with radio_manager.radio_operation("send_direct_message") as mc:
            logger.debug("Ensuring contact %s is on radio before sending", contact.public_key[:12])
            add_result = await mc.commands.add_contact(contact_data)
            if add_result.type == EventType.ERROR:
                logger.warning("Failed to add contact to radio: %s", add_result.payload)

            cached_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
            if not cached_contact:
                cached_contact = contact_data

            logger.info("Sending direct message to %s", contact.public_key[:12])
            sent_at = int(now_fn())
            sender_timestamp = await allocate_outgoing_sender_timestamp(
                message_repository=message_repository,
                msg_type="PRIV",
                conversation_key=contact.public_key.lower(),
                text=text,
                requested_timestamp=sent_at,
            )
            # Compress on the wire when the contact opts in; the stored/dedup
            # text stays plaintext (below). encode_outbound() is deterministic so
            # the retry path sends identical bytes.
            result = await mc.commands.send_msg(
                dst=cached_contact,
                msg=(
                    encode_outbound(text, version=contact.mcmp_version, timestamp=sender_timestamp)
                    if contact.mcmp_enabled
                    else text
                ),
                timestamp=sender_timestamp,
            )

        if result is None:
            logger.warning(
                "No response from radio after direct send to %s; send outcome is unknown",
                contact.public_key[:12],
            )
            raise HTTPException(status_code=408, detail=NO_RADIO_RESPONSE_AFTER_SEND_DETAIL)

        if result.type == EventType.ERROR:
            raise HTTPException(status_code=422, detail=f"Failed to send message: {result.payload}")

        logger.debug(
            "Radio send result for direct message to %s: %r",
            contact.public_key[:12],
            result.payload,
        )

        message = await create_outgoing_direct_message(
            conversation_key=contact.public_key.lower(),
            text=text,
            sender_timestamp=sender_timestamp,
            received_at=sent_at,
            broadcast_fn=broadcast_fn,
            message_repository=message_repository,
        )
        if message is None:
            raise HTTPException(
                status_code=422,
                detail="Failed to store outgoing message - unexpected duplicate",
            )
    finally:
        if sender_timestamp is not None:
            await release_outgoing_sender_timestamp(
                msg_type="PRIV",
                conversation_key=contact.public_key.lower(),
                text=text,
                sender_timestamp=sender_timestamp,
            )

    if sent_at is None or sender_timestamp is None or message is None or result is None:
        raise HTTPException(status_code=422, detail="Failed to store outgoing message")

    await contact_repository.update_last_contacted(contact.public_key.lower(), sent_at)

    ack_code = _extract_expected_ack_code(result)
    retry_timeout_ms = _get_direct_message_retry_timeout_ms(result)
    ack_count = await _apply_direct_message_ack_tracking(
        result=result,
        message_id=message.id,
        track_pending_ack_fn=track_pending_ack_fn,
        broadcast_fn=broadcast_fn,
    )
    if ack_count > 0:
        message.acked = ack_count
        return message

    if DM_SEND_MAX_ATTEMPTS > 1 and ack_code:
        retry_task_scheduler(
            _retry_direct_message_until_acked(
                contact=contact,
                text=text,
                message_id=message.id,
                sender_timestamp=sender_timestamp,
                radio_manager=radio_manager,
                track_pending_ack_fn=track_pending_ack_fn,
                broadcast_fn=broadcast_fn,
                wait_timeout_ms=retry_timeout_ms,
                sleep_fn=retry_sleep_fn,
                message_repository=message_repository,
            )
        )

    return message


async def _channel_echo_watchdog(
    message_id: int,
    radio_manager,
    broadcast_fn: BroadcastFn,
    error_broadcast_fn: BroadcastFn,
) -> None:
    """One-shot watchdog: if no echo heard after delay, attempt one byte-perfect resend.

    Spawned as a fire-and-forget task after a channel send when auto_resend_channel is enabled.
    Uses non-blocking radio lock so it never stalls user actions.
    """
    try:
        await asyncio.sleep(ECHO_WATCHDOG_DELAY_SECONDS)

        msg = await MessageRepository.get_by_id(message_id)
        if not msg:
            return
        if msg.acked > 0:
            logger.debug(
                "Echo watchdog: message %d already has %d echo(s), skipping", message_id, msg.acked
            )
            return
        if msg.sender_timestamp is None:
            return

        elapsed = int(_time.time()) - msg.sender_timestamp
        if elapsed > RESEND_WINDOW_SECONDS:
            logger.debug(
                "Echo watchdog: message %d outside resend window (%ds)", message_id, elapsed
            )
            return

        channel = await ChannelRepository.get_by_key(msg.conversation_key)
        if not channel:
            return

        logger.info(
            "Echo watchdog: no echo for message %d after %.0fs, attempting byte-perfect resend",
            message_id,
            ECHO_WATCHDOG_DELAY_SECONDS,
        )

        try:
            key_bytes = bytes.fromhex(msg.conversation_key)
        except ValueError:
            return

        timestamp_bytes = msg.sender_timestamp.to_bytes(4, "little")

        # Strip sender name prefix to get the raw text for the radio
        async with radio_manager.radio_operation("echo_watchdog_resend", blocking=False) as mc:
            radio_name = mc.self_info.get("name", "") if mc.self_info else ""
            text_to_send = msg.text
            if radio_name and text_to_send.startswith(f"{radio_name}: "):
                text_to_send = text_to_send[len(f"{radio_name}: ") :]

            result = await send_channel_message_with_effective_scope(
                mc=mc,
                channel=channel,
                channel_key=msg.conversation_key,
                key_bytes=key_bytes,
                text=text_to_send,
                timestamp_bytes=timestamp_bytes,
                action_label="echo watchdog resend",
                radio_manager=radio_manager,
                temp_radio_slot=WATCHDOG_TEMP_RADIO_SLOT,
                error_broadcast_fn=error_broadcast_fn,
            )
            if result is not None and result.type != EventType.ERROR:
                logger.info("Echo watchdog: resent message %d successfully", message_id)
            else:
                logger.debug("Echo watchdog: resend got no/error result for message %d", message_id)

    except RadioOperationBusyError:
        logger.debug("Echo watchdog: radio busy, skipping resend for message %d", message_id)
    except Exception:
        logger.debug("Echo watchdog: resend failed for message %d", message_id, exc_info=True)


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
    flood_scope_override: str | _ScopeUnset = SCOPE_UNSET,
    message_repository=MessageRepository,
) -> Any:
    """Send a channel message and persist/broadcast the outgoing row.

    ``flood_scope_override`` is forwarded to the scoped send: a region name
    scopes this send, an empty string forces unscoped flood, and ``SCOPE_UNSET``
    falls back to the channel's persisted override.
    """
    sent_at: int | None = None
    sender_timestamp: int | None = None
    radio_name = ""
    our_public_key: str | None = None
    text_with_sender = text
    outgoing_message = None

    try:
        async with radio_manager.radio_operation("send_channel_message") as mc:
            radio_name = mc.self_info.get("name", "") if mc.self_info else ""
            our_public_key = (mc.self_info.get("public_key") or None) if mc.self_info else None
            text_with_sender = f"{radio_name}: {text}" if radio_name else text
            logger.info("Sending channel message to %s: %s", channel.name, text[:50])

            sent_at = int(now_fn())
            sender_timestamp = await allocate_outgoing_sender_timestamp(
                message_repository=message_repository,
                msg_type="CHAN",
                conversation_key=channel_key_upper,
                text=text_with_sender,
                requested_timestamp=sent_at,
            )
            timestamp_bytes = sender_timestamp.to_bytes(4, "little")
            outgoing_message = await create_outgoing_channel_message(
                conversation_key=channel_key_upper,
                text=text_with_sender,
                sender_timestamp=sender_timestamp,
                received_at=sent_at,
                sender_name=radio_name or None,
                sender_key=our_public_key,
                channel_name=channel.name,
                broadcast_fn=broadcast_fn,
                broadcast=False,
                message_repository=message_repository,
            )
            if outgoing_message is None:
                raise HTTPException(
                    status_code=422,
                    detail="Failed to store outgoing message - unexpected duplicate",
                )

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
                flood_scope_override=flood_scope_override,
            )

            if result is None:
                logger.warning(
                    "No response from radio after channel send to %s; send outcome is unknown",
                    channel.name,
                )
                raise HTTPException(status_code=408, detail=NO_RADIO_RESPONSE_AFTER_SEND_DETAIL)

            if result.type == EventType.ERROR:
                raise HTTPException(
                    status_code=422, detail=f"Failed to send message: {result.payload}"
                )
    except Exception:
        if outgoing_message is not None:
            await message_repository.delete_by_id(outgoing_message.id)
            outgoing_message = None
        raise
    finally:
        if sender_timestamp is not None:
            await release_outgoing_sender_timestamp(
                msg_type="CHAN",
                conversation_key=channel_key_upper,
                text=text_with_sender,
                sender_timestamp=sender_timestamp,
            )

    if sent_at is None or sender_timestamp is None or outgoing_message is None:
        raise HTTPException(status_code=422, detail="Failed to store outgoing message")

    outgoing_message = await build_stored_outgoing_channel_message(
        message_id=outgoing_message.id,
        conversation_key=channel_key_upper,
        text=text_with_sender,
        sender_timestamp=sender_timestamp,
        received_at=sent_at,
        sender_name=radio_name or None,
        sender_key=our_public_key,
        channel_name=channel.name,
        message_repository=message_repository,
    )
    broadcast_message(message=outgoing_message, broadcast_fn=broadcast_fn)

    # Spawn echo watchdog if auto-resend is enabled
    try:
        settings = await AppSettingsRepository.get()
        if settings.auto_resend_channel:
            asyncio.create_task(
                _channel_echo_watchdog(
                    message_id=outgoing_message.id,
                    radio_manager=radio_manager,
                    broadcast_fn=broadcast_fn,
                    error_broadcast_fn=error_broadcast_fn,
                )
            )
    except Exception:
        logger.error("Echo watchdog setup failed", exc_info=True)

    return outgoing_message


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
) -> ResendChannelMessageResponse:
    """Resend a stored outgoing channel message."""
    try:
        key_bytes = bytes.fromhex(message.conversation_key)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid channel key format: {message.conversation_key}",
        ) from None

    sent_at: int | None = None
    sender_timestamp = message.sender_timestamp
    timestamp_bytes = message.sender_timestamp.to_bytes(4, "little")

    resend_public_key: str | None = None
    radio_name = ""
    new_message = None
    stored_text = message.text

    try:
        async with radio_manager.radio_operation("resend_channel_message") as mc:
            radio_name = mc.self_info.get("name", "") if mc.self_info else ""
            resend_public_key = (mc.self_info.get("public_key") or None) if mc.self_info else None
            text_to_send = message.text
            if radio_name and text_to_send.startswith(f"{radio_name}: "):
                text_to_send = text_to_send[len(f"{radio_name}: ") :]
            if new_timestamp:
                sent_at = int(now_fn())
                sender_timestamp = await allocate_outgoing_sender_timestamp(
                    message_repository=message_repository,
                    msg_type="CHAN",
                    conversation_key=message.conversation_key,
                    text=stored_text,
                    requested_timestamp=sent_at,
                )
                timestamp_bytes = sender_timestamp.to_bytes(4, "little")
                new_message = await create_outgoing_channel_message(
                    conversation_key=message.conversation_key,
                    text=message.text,
                    sender_timestamp=sender_timestamp,
                    received_at=sent_at,
                    sender_name=radio_name or None,
                    sender_key=resend_public_key,
                    channel_name=channel.name,
                    broadcast_fn=broadcast_fn,
                    broadcast=False,
                    message_repository=message_repository,
                )
                if new_message is None:
                    raise HTTPException(
                        status_code=422,
                        detail="Failed to store resent message - unexpected duplicate",
                    )

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
            if result is None:
                logger.warning(
                    "No response from radio after channel resend to %s; send outcome is unknown",
                    channel.name,
                )
                raise HTTPException(status_code=408, detail=NO_RADIO_RESPONSE_AFTER_SEND_DETAIL)
            if result.type == EventType.ERROR:
                raise HTTPException(
                    status_code=422,
                    detail=f"Failed to resend message: {result.payload}",
                )
    except Exception:
        if new_message is not None:
            await message_repository.delete_by_id(new_message.id)
            new_message = None
        raise
    finally:
        if new_timestamp and sent_at is not None:
            await release_outgoing_sender_timestamp(
                msg_type="CHAN",
                conversation_key=message.conversation_key,
                text=stored_text,
                sender_timestamp=sender_timestamp,
            )

    if new_timestamp:
        if sent_at is None or new_message is None:
            raise HTTPException(status_code=422, detail="Failed to assign resend timestamp")

        new_message = await build_stored_outgoing_channel_message(
            message_id=new_message.id,
            conversation_key=message.conversation_key,
            text=message.text,
            sender_timestamp=sender_timestamp,
            received_at=sent_at,
            sender_name=radio_name or None,
            sender_key=resend_public_key,
            channel_name=channel.name,
            message_repository=message_repository,
        )
        broadcast_message(message=new_message, broadcast_fn=broadcast_fn)

        logger.info(
            "Resent channel message %d as new message %d to %s",
            message.id,
            new_message.id,
            channel.name,
        )
        return ResendChannelMessageResponse(
            status="ok",
            message_id=new_message.id,
            message=new_message,
        )

    logger.info("Resent channel message %d to %s", message.id, channel.name)
    return ResendChannelMessageResponse(status="ok", message_id=message.id)
