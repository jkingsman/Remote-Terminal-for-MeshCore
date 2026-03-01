import asyncio
import logging
import re
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings as server_settings
from app.models import AppSettings, BotConfig
from app.repository import AppSettingsRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


def validate_bot_code(code: str, bot_name: str | None = None) -> None:
    """Validate bot code syntax. Raises HTTPException on error."""
    if not code or not code.strip():
        return  # Empty code is valid (disables bot)

    try:
        compile(code, "<bot_code>", "exec")
    except SyntaxError as e:
        name_part = f"'{bot_name}' " if bot_name else ""
        raise HTTPException(
            status_code=400,
            detail=f"Bot {name_part}has syntax error at line {e.lineno}: {e.msg}",
        ) from None


def validate_all_bots(bots: list[BotConfig]) -> None:
    """Validate all bots' code syntax. Raises HTTPException on first error."""
    for bot in bots:
        validate_bot_code(bot.code, bot.name)


class FavoriteRequest(BaseModel):
    type: Literal["channel", "contact"] = Field(description="'channel' or 'contact'")
    id: str = Field(description="Channel key or contact public key")


class AppSettingsUpdate(BaseModel):
    max_radio_contacts: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description=(
            "Maximum contacts to keep on radio (favorites first, then recent non-repeaters)"
        ),
    )
    auto_decrypt_dm_on_advert: bool | None = Field(
        default=None,
        description="Whether to attempt historical DM decryption on new contact advertisement",
    )
    sidebar_sort_order: Literal["recent", "alpha"] | None = Field(
        default=None,
        description="Sidebar sort order: 'recent' or 'alpha'",
    )
    advert_interval: int | None = Field(
        default=None,
        ge=0,
        description="Periodic advertisement interval in seconds (0 = disabled, minimum 3600)",
    )
    bots: list[BotConfig] | None = Field(
        default=None,
        description="List of bot configurations",
    )
    mqtt_broker_host: str | None = Field(
        default=None,
        description="MQTT broker hostname (empty = disabled)",
    )
    mqtt_broker_port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="MQTT broker port",
    )
    mqtt_username: str | None = Field(
        default=None,
        description="MQTT username (optional)",
    )
    mqtt_password: str | None = Field(
        default=None,
        description="MQTT password (optional)",
    )
    mqtt_use_tls: bool | None = Field(
        default=None,
        description="Whether to use TLS for MQTT connection",
    )
    mqtt_tls_insecure: bool | None = Field(
        default=None,
        description="Skip TLS certificate verification (for self-signed certs)",
    )
    mqtt_topic_prefix: str | None = Field(
        default=None,
        description="MQTT topic prefix",
    )
    mqtt_publish_messages: bool | None = Field(
        default=None,
        description="Whether to publish decrypted messages to MQTT",
    )
    mqtt_publish_raw_packets: bool | None = Field(
        default=None,
        description="Whether to publish raw packets to MQTT",
    )
    apprise_enabled: bool | None = Field(
        default=None,
        description="Whether external notifications via Apprise are enabled",
    )
    apprise_url: str | None = Field(
        default=None,
        description="Apprise URL(s), one per line",
    )
    apprise_mode: Literal["all", "selected"] | None = Field(
        default=None,
        description="Notify for all incoming or selected conversations only",
    )
    apprise_preserve_identity: bool | None = Field(
        default=None,
        description="Preserve destination webhook identity where supported",
    )
    apprise_targets: list[FavoriteRequest] | None = Field(
        default=None,
        description="Allowed channels/contacts when apprise_mode is 'selected'",
    )
    community_mqtt_enabled: bool | None = Field(
        default=None,
        description="Whether to publish raw packets to the community MQTT broker",
    )
    community_mqtt_iata: str | None = Field(
        default=None,
        description="IATA region code for community MQTT topic routing (3 alpha chars)",
    )
    community_mqtt_broker_host: str | None = Field(
        default=None,
        description="Community MQTT broker hostname",
    )
    community_mqtt_broker_port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="Community MQTT broker port",
    )
    community_mqtt_email: str | None = Field(
        default=None,
        description="Email address for node claiming on the community aggregator",
    )
    flood_scope: str | None = Field(
        default=None,
        description="Outbound flood scope / region name (empty = disabled)",
    )
    blocked_keys: list[str] | None = Field(
        default=None,
        description="Public keys whose messages are hidden from the UI",
    )
    blocked_names: list[str] | None = Field(
        default=None,
        description="Display names whose messages are hidden from the UI",
    )


class BlockKeyRequest(BaseModel):
    key: str = Field(description="Public key to toggle block status")


class BlockNameRequest(BaseModel):
    name: str = Field(description="Display name to toggle block status")


class MigratePreferencesRequest(BaseModel):
    favorites: list[FavoriteRequest] = Field(
        default_factory=list,
        description="List of favorites from localStorage",
    )
    sort_order: str = Field(
        default="recent",
        description="Sort order preference from localStorage",
    )
    last_message_times: dict[str, int] = Field(
        default_factory=dict,
        description="Map of conversation state keys to timestamps from localStorage",
    )


class MigratePreferencesResponse(BaseModel):
    migrated: bool = Field(description="Whether migration occurred (false if already migrated)")
    settings: AppSettings = Field(description="Current settings after migration attempt")


@router.get("", response_model=AppSettings)
async def get_settings() -> AppSettings:
    """Get current application settings."""
    return await AppSettingsRepository.get()


@router.patch("", response_model=AppSettings)
async def update_settings(update: AppSettingsUpdate) -> AppSettings:
    """Update application settings.

    Settings are persisted to the database and survive restarts.
    """
    kwargs = {}
    if update.max_radio_contacts is not None:
        logger.info("Updating max_radio_contacts to %d", update.max_radio_contacts)
        kwargs["max_radio_contacts"] = update.max_radio_contacts

    if update.auto_decrypt_dm_on_advert is not None:
        logger.info("Updating auto_decrypt_dm_on_advert to %s", update.auto_decrypt_dm_on_advert)
        kwargs["auto_decrypt_dm_on_advert"] = update.auto_decrypt_dm_on_advert

    if update.sidebar_sort_order is not None:
        logger.info("Updating sidebar_sort_order to %s", update.sidebar_sort_order)
        kwargs["sidebar_sort_order"] = update.sidebar_sort_order

    if update.advert_interval is not None:
        # Enforce minimum 1-hour interval; 0 means disabled
        interval = update.advert_interval
        if 0 < interval < 3600:
            interval = 3600
        logger.info("Updating advert_interval to %d", interval)
        kwargs["advert_interval"] = interval

    if update.bots is not None:
        if server_settings.disable_bots:
            raise HTTPException(
                status_code=403, detail="Bot system disabled by server configuration"
            )
        validate_all_bots(update.bots)
        logger.info("Updating bots (count=%d)", len(update.bots))
        kwargs["bots"] = update.bots

    # MQTT fields
    mqtt_fields = [
        "mqtt_broker_host",
        "mqtt_broker_port",
        "mqtt_username",
        "mqtt_password",
        "mqtt_use_tls",
        "mqtt_tls_insecure",
        "mqtt_topic_prefix",
        "mqtt_publish_messages",
        "mqtt_publish_raw_packets",
    ]
    mqtt_changed = False
    for field in mqtt_fields:
        value = getattr(update, field)
        if value is not None:
            kwargs[field] = value
            mqtt_changed = True

    if update.apprise_enabled is not None:
        logger.info("Updating apprise_enabled to %s", update.apprise_enabled)
        kwargs["apprise_enabled"] = update.apprise_enabled

    if update.apprise_url is not None:
        logger.info("Updating apprise_url (len=%d)", len(update.apprise_url.strip()))
        kwargs["apprise_url"] = update.apprise_url.strip()

    if update.apprise_mode is not None:
        logger.info("Updating apprise_mode to %s", update.apprise_mode)
        kwargs["apprise_mode"] = update.apprise_mode

    if update.apprise_preserve_identity is not None:
        logger.info("Updating apprise_preserve_identity to %s", update.apprise_preserve_identity)
        kwargs["apprise_preserve_identity"] = update.apprise_preserve_identity

    if update.apprise_targets is not None:
        logger.info("Updating apprise_targets (count=%d)", len(update.apprise_targets))
        kwargs["apprise_targets"] = update.apprise_targets

    # Community MQTT fields
    community_mqtt_changed = False
    if update.community_mqtt_enabled is not None:
        kwargs["community_mqtt_enabled"] = update.community_mqtt_enabled
        community_mqtt_changed = True

    if update.community_mqtt_iata is not None:
        iata = update.community_mqtt_iata.upper().strip()
        if iata and not re.fullmatch(r"[A-Z]{3}", iata):
            raise HTTPException(
                status_code=400,
                detail="IATA code must be exactly 3 uppercase alphabetic characters",
            )
        kwargs["community_mqtt_iata"] = iata
        community_mqtt_changed = True

    if update.community_mqtt_broker_host is not None:
        kwargs["community_mqtt_broker_host"] = update.community_mqtt_broker_host
        community_mqtt_changed = True

    if update.community_mqtt_broker_port is not None:
        kwargs["community_mqtt_broker_port"] = update.community_mqtt_broker_port
        community_mqtt_changed = True

    if update.community_mqtt_email is not None:
        kwargs["community_mqtt_email"] = update.community_mqtt_email
        community_mqtt_changed = True

    # Block lists
    if update.blocked_keys is not None:
        kwargs["blocked_keys"] = [k.lower() for k in update.blocked_keys]
    if update.blocked_names is not None:
        kwargs["blocked_names"] = update.blocked_names

    # Flood scope
    flood_scope_changed = False
    if update.flood_scope is not None:
        stripped = update.flood_scope.strip()
        kwargs["flood_scope"] = stripped
        flood_scope_changed = True

    # Require IATA when enabling community MQTT
    if kwargs.get("community_mqtt_enabled", False):
        # Check the IATA value being set, or fall back to current settings
        iata_value = kwargs.get("community_mqtt_iata")
        if iata_value is None:
            current = await AppSettingsRepository.get()
            iata_value = current.community_mqtt_iata
        if not iata_value or not re.fullmatch(r"[A-Z]{3}", iata_value):
            raise HTTPException(
                status_code=400,
                detail="A valid IATA region code is required to enable community sharing",
            )

    if kwargs:
        result = await AppSettingsRepository.update(**kwargs)

        # Restart MQTT publisher if any MQTT settings changed
        if mqtt_changed:
            from app.mqtt import mqtt_publisher

            await mqtt_publisher.restart(result)

        # Restart community MQTT publisher if any community settings changed
        if community_mqtt_changed:
            from app.community_mqtt import community_publisher

            await community_publisher.restart(result)

        # Apply flood scope to radio immediately if changed
        if flood_scope_changed:
            from app.radio import radio_manager

            if radio_manager.is_connected:
                try:
                    scope = result.flood_scope
                    async with radio_manager.radio_operation("set_flood_scope") as mc:
                        await mc.commands.set_flood_scope(scope if scope else "")
                        logger.info("Applied flood_scope=%r to radio", scope or "(disabled)")
                except Exception as e:
                    logger.warning("Failed to apply flood_scope to radio: %s", e)

        return result

    return await AppSettingsRepository.get()


@router.post("/favorites/toggle", response_model=AppSettings)
async def toggle_favorite(request: FavoriteRequest) -> AppSettings:
    """Toggle a conversation's favorite status."""
    settings = await AppSettingsRepository.get()
    is_favorited = any(f.type == request.type and f.id == request.id for f in settings.favorites)

    if is_favorited:
        logger.info("Removing favorite: %s %s", request.type, request.id[:12])
        result = await AppSettingsRepository.remove_favorite(request.type, request.id)
    else:
        logger.info("Adding favorite: %s %s", request.type, request.id[:12])
        result = await AppSettingsRepository.add_favorite(request.type, request.id)

    # When a contact favorite changes, sync the radio so the contact is
    # loaded/unloaded immediately rather than waiting for the next advert.
    if request.type == "contact":
        from app.radio_sync import sync_recent_contacts_to_radio

        asyncio.create_task(sync_recent_contacts_to_radio(force=True))

    return result


@router.post("/blocked-keys/toggle", response_model=AppSettings)
async def toggle_blocked_key(request: BlockKeyRequest) -> AppSettings:
    """Toggle a public key's blocked status."""
    logger.info("Toggling blocked key: %s", request.key[:12])
    return await AppSettingsRepository.toggle_blocked_key(request.key)


@router.post("/blocked-names/toggle", response_model=AppSettings)
async def toggle_blocked_name(request: BlockNameRequest) -> AppSettings:
    """Toggle a display name's blocked status."""
    logger.info("Toggling blocked name: %s", request.name)
    return await AppSettingsRepository.toggle_blocked_name(request.name)


@router.post("/migrate", response_model=MigratePreferencesResponse)
async def migrate_preferences(request: MigratePreferencesRequest) -> MigratePreferencesResponse:
    """Migrate all preferences from frontend localStorage to database.

    This is a one-time migration. If preferences have already been migrated,
    this endpoint will not overwrite them and will return migrated=false.

    Call this on frontend startup to ensure preferences are moved to the database.
    After successful migration, the frontend should clear localStorage preferences.

    Migrates:
    - favorites (remoteterm-favorites)
    - sort_order (remoteterm-sortOrder)
    - last_message_times (remoteterm-lastMessageTime)
    """
    # Convert to dict format for the repository method
    frontend_favorites = [{"type": f.type, "id": f.id} for f in request.favorites]

    settings, did_migrate = await AppSettingsRepository.migrate_preferences_from_frontend(
        favorites=frontend_favorites,
        sort_order=request.sort_order,
        last_message_times=request.last_message_times,
    )

    if did_migrate:
        logger.info(
            "Migrated preferences from frontend: %d favorites, sort_order=%s, %d message times",
            len(frontend_favorites),
            request.sort_order,
            len(request.last_message_times),
        )
    else:
        logger.debug("Preferences already migrated, skipping")

    return MigratePreferencesResponse(
        migrated=did_migrate,
        settings=settings,
    )
