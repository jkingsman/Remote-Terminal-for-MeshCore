import hashlib
import importlib.metadata
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from meshcore import EventType
from pydantic import BaseModel, Field

from app.config import get_recent_log_lines, settings
from app.radio_sync import get_contacts_selected_for_radio_sync, get_radio_channel_limit
from app.repository import MessageRepository
from app.routers.health import HealthResponse, build_health_data
from app.services.radio_runtime import radio_runtime

logger = logging.getLogger(__name__)

router = APIRouter(tags=["debug"])


class DebugApplicationInfo(BaseModel):
    version: str
    commit_hash: str | None = None
    git_branch: str | None = None
    git_dirty: bool | None = None
    python_version: str


class DebugRuntimeInfo(BaseModel):
    connection_info: str | None = None
    connection_desired: bool
    setup_in_progress: bool
    setup_complete: bool
    channels_with_incoming_messages: int
    max_channels: int
    path_hash_mode: int
    path_hash_mode_supported: bool
    channel_slot_reuse_enabled: bool
    channel_send_cache_capacity: int
    remediation_flags: dict[str, bool]


class DebugContactAudit(BaseModel):
    expected_and_found: int
    expected_but_not_found: list[str]
    found_but_not_expected: list[str]


class DebugChannelSlotMismatch(BaseModel):
    slot_number: int
    expected_sha256_of_room_key: str | None = None
    actual_sha256_of_room_key: str | None = None


class DebugChannelAudit(BaseModel):
    matched_slots: int
    wrong_slots: list[DebugChannelSlotMismatch]


class DebugRadioProbe(BaseModel):
    performed: bool
    errors: list[str] = Field(default_factory=list)
    self_info: dict[str, Any] | None = None
    device_info: dict[str, Any] | None = None
    stats_core: dict[str, Any] | None = None
    stats_radio: dict[str, Any] | None = None
    contacts: DebugContactAudit | None = None
    channels: DebugChannelAudit | None = None


class DebugSnapshotResponse(BaseModel):
    captured_at: str
    application: DebugApplicationInfo
    health: HealthResponse
    runtime: DebugRuntimeInfo
    radio_probe: DebugRadioProbe
    logs: list[str]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _get_app_version() -> str:
    try:
        return importlib.metadata.version("remoteterm-meshcore")
    except Exception:
        pyproject = _repo_root() / "pyproject.toml"
        try:
            for line in pyproject.read_text().splitlines():
                if line.startswith("version = "):
                    return line.split('"')[1]
        except Exception:
            pass
    return "0.0.0"


def _git_output(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=_repo_root(),
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    output = result.stdout.strip()
    return output or None


def _build_application_info() -> DebugApplicationInfo:
    dirty_output = _git_output("status", "--porcelain")
    return DebugApplicationInfo(
        version=_get_app_version(),
        commit_hash=_git_output("rev-parse", "HEAD"),
        git_branch=_git_output("rev-parse", "--abbrev-ref", "HEAD"),
        git_dirty=(dirty_output is not None and dirty_output != ""),
        python_version=sys.version.split()[0],
    )


def _event_type_name(event: Any) -> str:
    event_type = getattr(event, "type", None)
    return getattr(event_type, "name", str(event_type))


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_channel_secret(payload: dict[str, Any]) -> bytes:
    secret = payload.get("channel_secret", b"")
    if isinstance(secret, bytes):
        return secret
    return bytes(secret)


def _is_empty_channel_payload(payload: dict[str, Any]) -> bool:
    name = payload.get("channel_name", "")
    return not name or name == "\x00" * len(name)


def _observed_channel_key(event: Any) -> str | None:
    if getattr(event, "type", None) != EventType.CHANNEL_INFO:
        return None

    payload = event.payload or {}
    if _is_empty_channel_payload(payload):
        return None

    return _normalize_channel_secret(payload).hex().upper()


def _coerce_live_max_channels(device_info: dict[str, Any] | None) -> int | None:
    if not device_info or "max_channels" not in device_info:
        return None
    try:
        return int(device_info["max_channels"])
    except (TypeError, ValueError):
        return None


async def _build_contact_audit(
    observed_contacts_payload: dict[str, dict[str, Any]],
) -> DebugContactAudit:
    expected_contacts = await get_contacts_selected_for_radio_sync()
    expected_keys = {contact.public_key.lower() for contact in expected_contacts}
    observed_keys = {public_key.lower() for public_key in observed_contacts_payload}

    return DebugContactAudit(
        expected_and_found=len(expected_keys & observed_keys),
        expected_but_not_found=sorted(_sha256_hex(key) for key in (expected_keys - observed_keys)),
        found_but_not_expected=sorted(_sha256_hex(key) for key in (observed_keys - expected_keys)),
    )


async def _build_channel_audit(mc: Any, max_channels: int | None = None) -> DebugChannelAudit:
    cache_key_by_slot = {
        slot: channel_key for channel_key, slot in radio_runtime.get_channel_send_cache_snapshot()
    }

    matched_slots = 0
    wrong_slots: list[DebugChannelSlotMismatch] = []
    for slot in range(get_radio_channel_limit(max_channels)):
        event = await mc.commands.get_channel(slot)
        expected_key = cache_key_by_slot.get(slot)
        observed_key = _observed_channel_key(event)
        if expected_key == observed_key:
            matched_slots += 1
            continue
        wrong_slots.append(
            DebugChannelSlotMismatch(
                slot_number=slot,
                expected_sha256_of_room_key=_sha256_hex(expected_key) if expected_key else None,
                actual_sha256_of_room_key=_sha256_hex(observed_key) if observed_key else None,
            )
        )

    return DebugChannelAudit(
        matched_slots=matched_slots,
        wrong_slots=wrong_slots,
    )


async def _probe_radio() -> DebugRadioProbe:
    if not radio_runtime.is_connected:
        return DebugRadioProbe(performed=False, errors=["Radio not connected"])

    errors: list[str] = []
    try:
        async with radio_runtime.radio_operation(
            "debug_support_snapshot",
            suspend_auto_fetch=True,
        ) as mc:
            device_info = None
            stats_core = None
            stats_radio = None

            device_event = await mc.commands.send_device_query()
            if getattr(device_event, "type", None) == EventType.DEVICE_INFO:
                device_info = device_event.payload
            else:
                errors.append(f"send_device_query returned {_event_type_name(device_event)}")

            core_event = await mc.commands.get_stats_core()
            if getattr(core_event, "type", None) == EventType.STATS_CORE:
                stats_core = core_event.payload
            else:
                errors.append(f"get_stats_core returned {_event_type_name(core_event)}")

            radio_event = await mc.commands.get_stats_radio()
            if getattr(radio_event, "type", None) == EventType.STATS_RADIO:
                stats_radio = radio_event.payload
            else:
                errors.append(f"get_stats_radio returned {_event_type_name(radio_event)}")

            contacts_event = await mc.commands.get_contacts()
            observed_contacts_payload: dict[str, dict[str, Any]] = {}
            if getattr(contacts_event, "type", None) != EventType.ERROR:
                observed_contacts_payload = contacts_event.payload or {}
            else:
                errors.append(f"get_contacts returned {_event_type_name(contacts_event)}")

            return DebugRadioProbe(
                performed=True,
                errors=errors,
                self_info=dict(mc.self_info or {}),
                device_info=device_info,
                stats_core=stats_core,
                stats_radio=stats_radio,
                contacts=await _build_contact_audit(observed_contacts_payload),
                channels=await _build_channel_audit(
                    mc,
                    max_channels=_coerce_live_max_channels(device_info),
                ),
            )
    except Exception as exc:
        logger.warning("Debug support snapshot radio probe failed: %s", exc, exc_info=True)
        errors.append(str(exc))
        return DebugRadioProbe(performed=False, errors=errors)


@router.get("/debug", response_model=DebugSnapshotResponse)
async def debug_support_snapshot() -> DebugSnapshotResponse:
    """Return a support/debug snapshot with recent logs and live radio state."""
    health_data = await build_health_data(radio_runtime.is_connected, radio_runtime.connection_info)
    radio_probe = await _probe_radio()
    channels_with_incoming_messages = (
        await MessageRepository.count_channels_with_incoming_messages()
    )
    return DebugSnapshotResponse(
        captured_at=datetime.now(timezone.utc).isoformat(),
        application=_build_application_info(),
        health=HealthResponse(**health_data),
        runtime=DebugRuntimeInfo(
            connection_info=radio_runtime.connection_info,
            connection_desired=radio_runtime.connection_desired,
            setup_in_progress=radio_runtime.is_setup_in_progress,
            setup_complete=radio_runtime.is_setup_complete,
            channels_with_incoming_messages=channels_with_incoming_messages,
            max_channels=radio_runtime.max_channels,
            path_hash_mode=radio_runtime.path_hash_mode,
            path_hash_mode_supported=radio_runtime.path_hash_mode_supported,
            channel_slot_reuse_enabled=radio_runtime.channel_slot_reuse_enabled(),
            channel_send_cache_capacity=radio_runtime.get_channel_send_cache_capacity(),
            remediation_flags={
                "enable_message_poll_fallback": settings.enable_message_poll_fallback,
                "force_channel_slot_reconfigure": settings.force_channel_slot_reconfigure,
            },
        ),
        radio_probe=radio_probe,
        logs=get_recent_log_lines(limit=1000),
    )
