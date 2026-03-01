import json
import logging
import time
from typing import Any, Literal

from app.database import db
from app.models import AppSettings, BotConfig, Favorite

logger = logging.getLogger(__name__)

SECONDS_1H = 3600
SECONDS_24H = 86400
SECONDS_7D = 604800


class AppSettingsRepository:
    """Repository for app_settings table (single-row pattern)."""

    @staticmethod
    async def get() -> AppSettings:
        """Get the current app settings.

        Always returns settings - creates default row if needed (migration handles initial row).
        """
        cursor = await db.conn.execute(
            """
            SELECT max_radio_contacts, favorites, auto_decrypt_dm_on_advert,
                   sidebar_sort_order, last_message_times, preferences_migrated,
                   advert_interval, last_advert_time, bots,
                   mqtt_broker_host, mqtt_broker_port, mqtt_username, mqtt_password,
                   mqtt_use_tls, mqtt_tls_insecure, mqtt_topic_prefix,
                   mqtt_publish_messages, mqtt_publish_raw_packets,
                   apprise_enabled, apprise_url, apprise_mode,
                   apprise_preserve_identity, apprise_targets,
                   community_mqtt_enabled, community_mqtt_iata,
                   community_mqtt_broker_host, community_mqtt_broker_port,
                   community_mqtt_email, flood_scope,
                   blocked_keys, blocked_names
            FROM app_settings WHERE id = 1
            """
        )
        row = await cursor.fetchone()

        if not row:
            # Should not happen after migration, but handle gracefully
            return AppSettings()

        def row_get(key: str, default: Any = None) -> Any:
            try:
                return row[key]
            except (KeyError, IndexError, TypeError):
                return default

        # Parse favorites JSON
        favorites = []
        if row["favorites"]:
            try:
                favorites_data = json.loads(row["favorites"])
                favorites = [Favorite(**f) for f in favorites_data]
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(
                    "Failed to parse favorites JSON, using empty list: %s (data=%r)",
                    e,
                    row["favorites"][:100] if row["favorites"] else None,
                )
                favorites = []

        # Parse last_message_times JSON
        last_message_times: dict[str, int] = {}
        if row["last_message_times"]:
            try:
                last_message_times = json.loads(row["last_message_times"])
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(
                    "Failed to parse last_message_times JSON, using empty dict: %s",
                    e,
                )
                last_message_times = {}

        # Parse bots JSON
        bots: list[BotConfig] = []
        if row_get("bots"):
            try:
                bots_data = json.loads(row_get("bots"))
                bots = [BotConfig(**b) for b in bots_data]
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(
                    "Failed to parse bots JSON, using empty list: %s (data=%r)",
                    e,
                    row_get("bots")[:100] if row_get("bots") else None,
                )
                bots = []

        # Parse apprise_targets JSON
        apprise_targets: list[Favorite] = []
        if row_get("apprise_targets"):
            try:
                targets_data = json.loads(row_get("apprise_targets"))
                apprise_targets = [Favorite(**t) for t in targets_data]
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(
                    "Failed to parse apprise_targets JSON, using empty list: %s (data=%r)",
                    e,
                    row_get("apprise_targets")[:100] if row_get("apprise_targets") else None,
                )
                apprise_targets = []

        # Parse blocked_keys JSON
        blocked_keys: list[str] = []
        if row["blocked_keys"]:
            try:
                blocked_keys = json.loads(row["blocked_keys"])
            except (json.JSONDecodeError, TypeError):
                blocked_keys = []

        # Parse blocked_names JSON
        blocked_names: list[str] = []
        if row["blocked_names"]:
            try:
                blocked_names = json.loads(row["blocked_names"])
            except (json.JSONDecodeError, TypeError):
                blocked_names = []

        # Validate sidebar_sort_order (fallback to "recent" if invalid)
        sort_order = row["sidebar_sort_order"]
        if sort_order not in ("recent", "alpha"):
            sort_order = "recent"
        apprise_mode = row_get("apprise_mode", "all")
        if apprise_mode not in ("all", "selected"):
            apprise_mode = "all"

        return AppSettings(
            max_radio_contacts=row["max_radio_contacts"],
            favorites=favorites,
            auto_decrypt_dm_on_advert=bool(row["auto_decrypt_dm_on_advert"]),
            sidebar_sort_order=sort_order,
            last_message_times=last_message_times,
            preferences_migrated=bool(row["preferences_migrated"]),
            advert_interval=row["advert_interval"] or 0,
            last_advert_time=row["last_advert_time"] or 0,
            bots=bots,
            mqtt_broker_host=row["mqtt_broker_host"] or "",
            mqtt_broker_port=row["mqtt_broker_port"] or 1883,
            mqtt_username=row["mqtt_username"] or "",
            mqtt_password=row["mqtt_password"] or "",
            mqtt_use_tls=bool(row["mqtt_use_tls"]),
            mqtt_tls_insecure=bool(row["mqtt_tls_insecure"]),
            mqtt_topic_prefix=row["mqtt_topic_prefix"] or "meshcore",
            mqtt_publish_messages=bool(row["mqtt_publish_messages"]),
            mqtt_publish_raw_packets=bool(row["mqtt_publish_raw_packets"]),
            apprise_enabled=bool(row_get("apprise_enabled", 0)),
            apprise_url=row_get("apprise_url", "") or "",
            apprise_mode=apprise_mode,
            apprise_preserve_identity=bool(row_get("apprise_preserve_identity", 1)),
            apprise_targets=apprise_targets,
            community_mqtt_enabled=bool(row["community_mqtt_enabled"]),
            community_mqtt_iata=row["community_mqtt_iata"] or "",
            community_mqtt_broker_host=row["community_mqtt_broker_host"]
            or "mqtt-us-v1.letsmesh.net",
            community_mqtt_broker_port=row["community_mqtt_broker_port"] or 443,
            community_mqtt_email=row["community_mqtt_email"] or "",
            flood_scope=row["flood_scope"] or "",
            blocked_keys=blocked_keys,
            blocked_names=blocked_names,
        )

    @staticmethod
    async def update(
        max_radio_contacts: int | None = None,
        favorites: list[Favorite] | None = None,
        auto_decrypt_dm_on_advert: bool | None = None,
        sidebar_sort_order: str | None = None,
        last_message_times: dict[str, int] | None = None,
        preferences_migrated: bool | None = None,
        advert_interval: int | None = None,
        last_advert_time: int | None = None,
        bots: list[BotConfig] | None = None,
        mqtt_broker_host: str | None = None,
        mqtt_broker_port: int | None = None,
        mqtt_username: str | None = None,
        mqtt_password: str | None = None,
        mqtt_use_tls: bool | None = None,
        mqtt_tls_insecure: bool | None = None,
        mqtt_topic_prefix: str | None = None,
        mqtt_publish_messages: bool | None = None,
        mqtt_publish_raw_packets: bool | None = None,
        apprise_enabled: bool | None = None,
        apprise_url: str | None = None,
        apprise_mode: str | None = None,
        apprise_preserve_identity: bool | None = None,
        apprise_targets: list[Favorite] | None = None,
        community_mqtt_enabled: bool | None = None,
        community_mqtt_iata: str | None = None,
        community_mqtt_broker_host: str | None = None,
        community_mqtt_broker_port: int | None = None,
        community_mqtt_email: str | None = None,
        flood_scope: str | None = None,
        blocked_keys: list[str] | None = None,
        blocked_names: list[str] | None = None,
    ) -> AppSettings:
        """Update app settings. Only provided fields are updated."""
        updates = []
        params: list[Any] = []

        if max_radio_contacts is not None:
            updates.append("max_radio_contacts = ?")
            params.append(max_radio_contacts)

        if favorites is not None:
            updates.append("favorites = ?")
            favorites_json = json.dumps([f.model_dump() for f in favorites])
            params.append(favorites_json)

        if auto_decrypt_dm_on_advert is not None:
            updates.append("auto_decrypt_dm_on_advert = ?")
            params.append(1 if auto_decrypt_dm_on_advert else 0)

        if sidebar_sort_order is not None:
            updates.append("sidebar_sort_order = ?")
            params.append(sidebar_sort_order)

        if last_message_times is not None:
            updates.append("last_message_times = ?")
            params.append(json.dumps(last_message_times))

        if preferences_migrated is not None:
            updates.append("preferences_migrated = ?")
            params.append(1 if preferences_migrated else 0)

        if advert_interval is not None:
            updates.append("advert_interval = ?")
            params.append(advert_interval)

        if last_advert_time is not None:
            updates.append("last_advert_time = ?")
            params.append(last_advert_time)

        if bots is not None:
            updates.append("bots = ?")
            bots_json = json.dumps([b.model_dump() for b in bots])
            params.append(bots_json)

        if mqtt_broker_host is not None:
            updates.append("mqtt_broker_host = ?")
            params.append(mqtt_broker_host)

        if mqtt_broker_port is not None:
            updates.append("mqtt_broker_port = ?")
            params.append(mqtt_broker_port)

        if mqtt_username is not None:
            updates.append("mqtt_username = ?")
            params.append(mqtt_username)

        if mqtt_password is not None:
            updates.append("mqtt_password = ?")
            params.append(mqtt_password)

        if mqtt_use_tls is not None:
            updates.append("mqtt_use_tls = ?")
            params.append(1 if mqtt_use_tls else 0)

        if mqtt_tls_insecure is not None:
            updates.append("mqtt_tls_insecure = ?")
            params.append(1 if mqtt_tls_insecure else 0)

        if mqtt_topic_prefix is not None:
            updates.append("mqtt_topic_prefix = ?")
            params.append(mqtt_topic_prefix)

        if mqtt_publish_messages is not None:
            updates.append("mqtt_publish_messages = ?")
            params.append(1 if mqtt_publish_messages else 0)

        if mqtt_publish_raw_packets is not None:
            updates.append("mqtt_publish_raw_packets = ?")
            params.append(1 if mqtt_publish_raw_packets else 0)

        if apprise_enabled is not None:
            updates.append("apprise_enabled = ?")
            params.append(1 if apprise_enabled else 0)

        if apprise_url is not None:
            updates.append("apprise_url = ?")
            params.append(apprise_url)

        if apprise_mode is not None:
            updates.append("apprise_mode = ?")
            params.append(apprise_mode)

        if apprise_preserve_identity is not None:
            updates.append("apprise_preserve_identity = ?")
            params.append(1 if apprise_preserve_identity else 0)

        if apprise_targets is not None:
            updates.append("apprise_targets = ?")
            targets_json = json.dumps([t.model_dump() for t in apprise_targets])
            params.append(targets_json)

        if community_mqtt_enabled is not None:
            updates.append("community_mqtt_enabled = ?")
            params.append(1 if community_mqtt_enabled else 0)

        if community_mqtt_iata is not None:
            updates.append("community_mqtt_iata = ?")
            params.append(community_mqtt_iata)

        if community_mqtt_broker_host is not None:
            updates.append("community_mqtt_broker_host = ?")
            params.append(community_mqtt_broker_host)

        if community_mqtt_broker_port is not None:
            updates.append("community_mqtt_broker_port = ?")
            params.append(community_mqtt_broker_port)

        if community_mqtt_email is not None:
            updates.append("community_mqtt_email = ?")
            params.append(community_mqtt_email)

        if flood_scope is not None:
            updates.append("flood_scope = ?")
            params.append(flood_scope)

        if blocked_keys is not None:
            updates.append("blocked_keys = ?")
            params.append(json.dumps(blocked_keys))

        if blocked_names is not None:
            updates.append("blocked_names = ?")
            params.append(json.dumps(blocked_names))

        if updates:
            query = f"UPDATE app_settings SET {', '.join(updates)} WHERE id = 1"
            await db.conn.execute(query, params)
            await db.conn.commit()

        return await AppSettingsRepository.get()

    @staticmethod
    async def add_favorite(fav_type: Literal["channel", "contact"], fav_id: str) -> AppSettings:
        """Add a favorite, avoiding duplicates."""
        settings = await AppSettingsRepository.get()

        # Check if already favorited
        if any(f.type == fav_type and f.id == fav_id for f in settings.favorites):
            return settings

        new_favorites = settings.favorites + [Favorite(type=fav_type, id=fav_id)]
        return await AppSettingsRepository.update(favorites=new_favorites)

    @staticmethod
    async def remove_favorite(fav_type: Literal["channel", "contact"], fav_id: str) -> AppSettings:
        """Remove a favorite."""
        settings = await AppSettingsRepository.get()
        new_favorites = [
            f for f in settings.favorites if not (f.type == fav_type and f.id == fav_id)
        ]
        return await AppSettingsRepository.update(favorites=new_favorites)

    @staticmethod
    async def toggle_blocked_key(key: str) -> AppSettings:
        """Toggle a public key in the blocked list. Keys are normalized to lowercase."""
        normalized = key.lower()
        settings = await AppSettingsRepository.get()
        if normalized in settings.blocked_keys:
            new_keys = [k for k in settings.blocked_keys if k != normalized]
        else:
            new_keys = settings.blocked_keys + [normalized]
        return await AppSettingsRepository.update(blocked_keys=new_keys)

    @staticmethod
    async def toggle_blocked_name(name: str) -> AppSettings:
        """Toggle a display name in the blocked list."""
        settings = await AppSettingsRepository.get()
        if name in settings.blocked_names:
            new_names = [n for n in settings.blocked_names if n != name]
        else:
            new_names = settings.blocked_names + [name]
        return await AppSettingsRepository.update(blocked_names=new_names)

    @staticmethod
    async def migrate_preferences_from_frontend(
        favorites: list[dict],
        sort_order: str,
        last_message_times: dict[str, int],
    ) -> tuple[AppSettings, bool]:
        """Migrate all preferences from frontend localStorage.

        This is a one-time migration. If already migrated, returns current settings
        without overwriting. Returns (settings, did_migrate) tuple.
        """
        settings = await AppSettingsRepository.get()

        if settings.preferences_migrated:
            # Already migrated, don't overwrite
            return settings, False

        # Convert frontend favorites format to Favorite objects
        new_favorites = []
        for f in favorites:
            if f.get("type") in ("channel", "contact") and f.get("id"):
                new_favorites.append(Favorite(type=f["type"], id=f["id"]))

        # Update with migrated preferences and mark as migrated
        settings = await AppSettingsRepository.update(
            favorites=new_favorites,
            sidebar_sort_order=sort_order if sort_order in ("recent", "alpha") else "recent",
            last_message_times=last_message_times,
            preferences_migrated=True,
        )

        return settings, True


class StatisticsRepository:
    @staticmethod
    async def _activity_counts(*, contact_type: int, exclude: bool = False) -> dict[str, int]:
        """Get time-windowed counts for contacts/repeaters heard."""
        now = int(time.time())
        op = "!=" if exclude else "="
        cursor = await db.conn.execute(
            f"""
            SELECT
                SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END) AS last_hour,
                SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END) AS last_24_hours,
                SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END) AS last_week
            FROM contacts
            WHERE type {op} ? AND last_seen IS NOT NULL
            """,
            (now - SECONDS_1H, now - SECONDS_24H, now - SECONDS_7D, contact_type),
        )
        row = await cursor.fetchone()
        assert row is not None  # Aggregate query always returns a row
        return {
            "last_hour": row["last_hour"] or 0,
            "last_24_hours": row["last_24_hours"] or 0,
            "last_week": row["last_week"] or 0,
        }

    @staticmethod
    async def get_all() -> dict:
        """Aggregate all statistics from existing tables."""
        now = int(time.time())

        # Top 5 busiest channels in last 24h
        cursor = await db.conn.execute(
            """
            SELECT m.conversation_key, COALESCE(c.name, m.conversation_key) AS channel_name,
                   COUNT(*) AS message_count
            FROM messages m
            LEFT JOIN channels c ON m.conversation_key = c.key
            WHERE m.type = 'CHAN' AND m.received_at >= ?
            GROUP BY m.conversation_key
            ORDER BY COUNT(*) DESC
            LIMIT 5
            """,
            (now - SECONDS_24H,),
        )
        rows = await cursor.fetchall()
        busiest_channels_24h = [
            {
                "channel_key": row["conversation_key"],
                "channel_name": row["channel_name"],
                "message_count": row["message_count"],
            }
            for row in rows
        ]

        # Entity counts
        cursor = await db.conn.execute("SELECT COUNT(*) AS cnt FROM contacts WHERE type != 2")
        row = await cursor.fetchone()
        assert row is not None
        contact_count: int = row["cnt"]

        cursor = await db.conn.execute("SELECT COUNT(*) AS cnt FROM contacts WHERE type = 2")
        row = await cursor.fetchone()
        assert row is not None
        repeater_count: int = row["cnt"]

        cursor = await db.conn.execute("SELECT COUNT(*) AS cnt FROM channels")
        row = await cursor.fetchone()
        assert row is not None
        channel_count: int = row["cnt"]

        # Packet split
        cursor = await db.conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN message_id IS NOT NULL THEN 1 ELSE 0 END) AS decrypted
            FROM raw_packets
            """
        )
        pkt_row = await cursor.fetchone()
        assert pkt_row is not None
        total_packets = pkt_row["total"] or 0
        decrypted_packets = pkt_row["decrypted"] or 0
        undecrypted_packets = total_packets - decrypted_packets

        # Message type counts
        cursor = await db.conn.execute("SELECT COUNT(*) AS cnt FROM messages WHERE type = 'PRIV'")
        row = await cursor.fetchone()
        assert row is not None
        total_dms: int = row["cnt"]

        cursor = await db.conn.execute("SELECT COUNT(*) AS cnt FROM messages WHERE type = 'CHAN'")
        row = await cursor.fetchone()
        assert row is not None
        total_channel_messages: int = row["cnt"]

        # Outgoing count
        cursor = await db.conn.execute("SELECT COUNT(*) AS cnt FROM messages WHERE outgoing = 1")
        row = await cursor.fetchone()
        assert row is not None
        total_outgoing: int = row["cnt"]

        # Activity windows
        contacts_heard = await StatisticsRepository._activity_counts(contact_type=2, exclude=True)
        repeaters_heard = await StatisticsRepository._activity_counts(contact_type=2)

        return {
            "busiest_channels_24h": busiest_channels_24h,
            "contact_count": contact_count,
            "repeater_count": repeater_count,
            "channel_count": channel_count,
            "total_packets": total_packets,
            "decrypted_packets": decrypted_packets,
            "undecrypted_packets": undecrypted_packets,
            "total_dms": total_dms,
            "total_channel_messages": total_channel_messages,
            "total_outgoing": total_outgoing,
            "contacts_heard": contacts_heard,
            "repeaters_heard": repeaters_heard,
        }
