"""Database CRUD for the Bots workspace: bots, runs, schedules, feeds, engine settings."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import aiosqlite

from app.database import db
from app.models import (
    Bot,
    BotEngineSettings,
    BotFeed,
    BotRun,
    BotSchedule,
)

logger = logging.getLogger(__name__)


def _load_json(raw: Any, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _row_to_bot(row: aiosqlite.Row) -> Bot:
    return Bot(
        id=row["id"],
        name=row["name"],
        category=row["category"],
        description=row["description"],
        code=row["code"],
        enabled=bool(row["enabled"]),
        admin_only=bool(row["admin_only"]),
        respond_to_dms=bool(row["respond_to_dms"]),
        scope=_load_json(row["scope"], {"channels": "all"}),
        cooldown_seconds=row["cooldown_seconds"] or 0,
        per_user_cooldown_seconds=row["per_user_cooldown_seconds"] or 0,
        queue_threshold_seconds=row["queue_threshold_seconds"] or 0,
        settings_schema=_load_json(row["settings_schema"], []),
        settings=_load_json(row["settings"], {}),
        ui_triggers=_load_json(row["ui_triggers"], []),
        builtin_key=row["builtin_key"],
        builtin_version=row["builtin_version"],
        modified=bool(row["modified"]),
        last_error=row["last_error"],
        sort_order=row["sort_order"] or 0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_BOT_COLUMNS = """
    id, name, category, description, code, enabled, admin_only, respond_to_dms,
    scope, cooldown_seconds, per_user_cooldown_seconds, queue_threshold_seconds,
    settings_schema, settings, ui_triggers, builtin_key, builtin_version,
    modified, last_error, sort_order, created_at, updated_at
"""


class BotRepository:
    @staticmethod
    async def get_all() -> list[Bot]:
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT {_BOT_COLUMNS} FROM bots ORDER BY category, name"
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_bot(row) for row in rows]

    @staticmethod
    async def get(bot_id: str) -> Bot | None:
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT {_BOT_COLUMNS} FROM bots WHERE id = ?", (bot_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_bot(row) if row else None

    @staticmethod
    async def get_by_builtin_key(builtin_key: str) -> Bot | None:
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT {_BOT_COLUMNS} FROM bots WHERE builtin_key = ?", (builtin_key,)
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_bot(row) if row else None

    @staticmethod
    async def name_exists(name: str, *, exclude_id: str | None = None) -> bool:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT 1 FROM bots WHERE name = ? AND (? IS NULL OR id != ?) LIMIT 1",
                (name, exclude_id, exclude_id),
            ) as cursor:
                return await cursor.fetchone() is not None

    @staticmethod
    async def create(
        *,
        name: str,
        category: str = "Custom",
        description: str = "",
        code: str = "",
        enabled: bool = False,
        admin_only: bool = False,
        respond_to_dms: bool = True,
        scope: dict[str, Any] | None = None,
        cooldown_seconds: float = 0,
        per_user_cooldown_seconds: float = 0,
        queue_threshold_seconds: float = 0,
        settings_schema: list[dict[str, Any]] | None = None,
        settings: dict[str, Any] | None = None,
        ui_triggers: list[dict[str, Any]] | None = None,
        builtin_key: str | None = None,
        builtin_version: str | None = None,
        modified: bool = False,
        bot_id: str | None = None,
    ) -> Bot:
        now = int(time.time())
        new_id = bot_id or str(uuid.uuid4())
        async with db.tx() as conn:
            await conn.execute(
                """
                INSERT INTO bots (
                    id, name, category, description, code, enabled, admin_only,
                    respond_to_dms, scope, cooldown_seconds, per_user_cooldown_seconds,
                    queue_threshold_seconds, settings_schema, settings, ui_triggers,
                    state, builtin_key, builtin_version, modified, sort_order,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, 0, ?, ?)
                """,
                (
                    new_id,
                    name,
                    category,
                    description,
                    code,
                    1 if enabled else 0,
                    1 if admin_only else 0,
                    1 if respond_to_dms else 0,
                    json.dumps(scope if scope is not None else {"channels": "all"}),
                    cooldown_seconds,
                    per_user_cooldown_seconds,
                    queue_threshold_seconds,
                    json.dumps(settings_schema or []),
                    json.dumps(settings or {}),
                    json.dumps(ui_triggers or []),
                    builtin_key,
                    builtin_version,
                    1 if modified else 0,
                    now,
                    now,
                ),
            )
        bot = await BotRepository.get(new_id)
        assert bot is not None
        return bot

    @staticmethod
    async def update(bot_id: str, **fields: Any) -> Bot | None:
        """Update the given columns. JSON-typed fields are serialized here."""
        json_fields = {"scope", "settings_schema", "settings", "ui_triggers"}
        bool_fields = {"enabled", "admin_only", "respond_to_dms", "modified"}
        allowed = {
            "name",
            "category",
            "description",
            "code",
            "enabled",
            "admin_only",
            "respond_to_dms",
            "scope",
            "cooldown_seconds",
            "per_user_cooldown_seconds",
            "queue_threshold_seconds",
            "settings_schema",
            "settings",
            "ui_triggers",
            "builtin_version",
            "modified",
            "last_error",
            "sort_order",
        }
        updates: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed or value is None and key != "last_error":
                continue
            updates.append(f"{key} = ?")
            if key in json_fields:
                params.append(json.dumps(value))
            elif key in bool_fields:
                params.append(1 if value else 0)
            else:
                params.append(value)
        if not updates:
            return await BotRepository.get(bot_id)
        updates.append("updated_at = ?")
        params.append(int(time.time()))
        params.append(bot_id)
        async with db.tx() as conn:
            await conn.execute(f"UPDATE bots SET {', '.join(updates)} WHERE id = ?", params)
        return await BotRepository.get(bot_id)

    @staticmethod
    async def delete(bot_id: str) -> bool:
        async with db.tx() as conn:
            async with conn.execute("DELETE FROM bots WHERE id = ?", (bot_id,)) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def get_state(bot_id: str) -> dict[str, Any]:
        async with db.readonly() as conn:
            async with conn.execute("SELECT state FROM bots WHERE id = ?", (bot_id,)) as cursor:
                row = await cursor.fetchone()
        return _load_json(row["state"], {}) if row else {}

    @staticmethod
    async def set_state(bot_id: str, state: dict[str, Any]) -> None:
        try:
            payload = json.dumps(state)
        except (TypeError, ValueError):
            logger.warning("Bot %s state is not JSON-serializable; dropping", bot_id)
            return
        async with db.tx() as conn:
            await conn.execute("UPDATE bots SET state = ? WHERE id = ?", (payload, bot_id))

    @staticmethod
    async def set_last_error(bot_id: str, error: str | None) -> None:
        async with db.tx() as conn:
            await conn.execute("UPDATE bots SET last_error = ? WHERE id = ?", (error, bot_id))


class BotRunRepository:
    MAX_RUNS_KEPT = 5000

    @staticmethod
    async def record(
        *,
        bot_id: str,
        started_at: int,
        duration_ms: int | None,
        trigger: str,
        sender_name: str | None,
        sender_key: str | None,
        channel_key: str | None,
        channel_name: str | None,
        is_dm: bool,
        result: str,
        replies: int,
        error: str | None,
        test_run: bool = False,
    ) -> None:
        async with db.tx() as conn:
            await conn.execute(
                """
                INSERT INTO bot_runs (
                    bot_id, started_at, duration_ms, trigger, sender_name, sender_key,
                    channel_key, channel_name, is_dm, result, replies, error, test_run
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bot_id,
                    started_at,
                    duration_ms,
                    trigger,
                    sender_name,
                    sender_key,
                    channel_key,
                    channel_name,
                    1 if is_dm else 0,
                    result,
                    replies,
                    error,
                    1 if test_run else 0,
                ),
            )
            # Bounded history: trim oldest rows past the cap (cheap, indexed).
            await conn.execute(
                """
                DELETE FROM bot_runs WHERE id IN (
                    SELECT id FROM bot_runs ORDER BY started_at DESC, id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (BotRunRepository.MAX_RUNS_KEPT,),
            )

    @staticmethod
    async def recent(bot_id: str | None = None, limit: int = 50) -> list[BotRun]:
        query = """
            SELECT r.id, r.bot_id, b.name AS bot_name, r.started_at, r.duration_ms,
                   r.trigger, r.sender_name, r.sender_key, r.channel_key, r.channel_name,
                   r.is_dm, r.result, r.replies, r.error, r.test_run
            FROM bot_runs r LEFT JOIN bots b ON b.id = r.bot_id
        """
        params: list[Any] = []
        if bot_id:
            query += " WHERE r.bot_id = ?"
            params.append(bot_id)
        query += " ORDER BY r.started_at DESC, r.id DESC LIMIT ?"
        params.append(limit)
        async with db.readonly() as conn:
            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        return [
            BotRun(
                id=row["id"],
                bot_id=row["bot_id"],
                bot_name=row["bot_name"] or "",
                started_at=row["started_at"],
                duration_ms=row["duration_ms"],
                trigger=row["trigger"],
                sender_name=row["sender_name"],
                sender_key=row["sender_key"],
                channel_key=row["channel_key"],
                channel_name=row["channel_name"],
                is_dm=bool(row["is_dm"]),
                result=row["result"],
                replies=row["replies"] or 0,
                error=row["error"],
                test_run=bool(row["test_run"]),
            )
            for row in rows
        ]

    @staticmethod
    async def stats(window_seconds: int) -> dict[str, Any]:
        """Aggregate dashboard stats over the window (test runs excluded)."""
        cutoff = int(time.time()) - window_seconds
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT COUNT(*) AS runs,
                       SUM(replies) AS replies,
                       SUM(CASE WHEN result = 'error' OR result = 'timeout' THEN 1 ELSE 0 END)
                           AS errors,
                       COUNT(DISTINCT COALESCE(sender_key, sender_name)) AS users
                FROM bot_runs WHERE started_at >= ? AND test_run = 0
                """,
                (cutoff,),
            ) as cursor:
                totals = await cursor.fetchone()

            async with conn.execute(
                """
                SELECT AVG(duration_ms) AS avg_ms FROM bot_runs
                WHERE started_at >= ? AND test_run = 0 AND duration_ms IS NOT NULL
                """,
                (cutoff,),
            ) as cursor:
                avg_row = await cursor.fetchone()

            async with conn.execute(
                """
                SELECT b.name AS label, COUNT(*) AS count
                FROM bot_runs r LEFT JOIN bots b ON b.id = r.bot_id
                WHERE r.started_at >= ? AND r.test_run = 0
                GROUP BY r.bot_id ORDER BY count DESC LIMIT 6
                """,
                (cutoff,),
            ) as cursor:
                top_bots = await cursor.fetchall()

            async with conn.execute(
                """
                SELECT CASE WHEN is_dm = 1 THEN 'Direct messages'
                            ELSE COALESCE(channel_name, channel_key, 'unknown') END AS label,
                       COUNT(*) AS count
                FROM bot_runs WHERE started_at >= ? AND test_run = 0
                GROUP BY label ORDER BY count DESC LIMIT 6
                """,
                (cutoff,),
            ) as cursor:
                top_channels = await cursor.fetchall()

            async with conn.execute(
                """
                SELECT COALESCE(sender_name, substr(sender_key, 1, 12), 'unknown') AS label,
                       COUNT(*) AS count
                FROM bot_runs
                WHERE started_at >= ? AND test_run = 0 AND trigger NOT IN ('cron', 'schedule')
                GROUP BY label ORDER BY count DESC LIMIT 6
                """,
                (cutoff,),
            ) as cursor:
                top_users = await cursor.fetchall()

            async with conn.execute(
                """
                SELECT b.name AS label, COUNT(*) AS count
                FROM bot_runs r LEFT JOIN bots b ON b.id = r.bot_id
                WHERE r.started_at >= ? AND r.test_run = 0
                  AND (r.result = 'error' OR r.result = 'timeout')
                GROUP BY r.bot_id ORDER BY count DESC LIMIT 6
                """,
                (cutoff,),
            ) as cursor:
                error_bots = await cursor.fetchall()

            async with conn.execute(
                """
                SELECT (started_at / 3600) * 3600 AS hour_ts, COUNT(*) AS count
                FROM bot_runs WHERE started_at >= ? AND test_run = 0
                GROUP BY hour_ts ORDER BY hour_ts
                """,
                (int(time.time()) - 24 * 3600,),
            ) as cursor:
                hourly = await cursor.fetchall()

        runs = (totals["runs"] if totals else 0) or 0
        replies = (totals["replies"] if totals else 0) or 0
        return {
            "runs": runs,
            "replies": replies,
            "reply_rate": round(replies / runs * 100) if runs else 0,
            "errors": (totals["errors"] if totals else 0) or 0,
            "unique_users": (totals["users"] if totals else 0) or 0,
            "avg_duration_ms": round(avg_row["avg_ms"]) if avg_row and avg_row["avg_ms"] else 0,
            "top_bots": [{"label": r["label"] or "deleted", "count": r["count"]} for r in top_bots],
            "top_channels": [{"label": r["label"], "count": r["count"]} for r in top_channels],
            "top_users": [{"label": r["label"], "count": r["count"]} for r in top_users],
            "error_bots": [
                {"label": r["label"] or "deleted", "count": r["count"]} for r in error_bots
            ],
            "runs_by_hour": [{"timestamp": r["hour_ts"], "count": r["count"]} for r in hourly],
        }


def _row_to_schedule(row: aiosqlite.Row) -> BotSchedule:
    return BotSchedule(
        id=row["id"],
        label=row["label"],
        cron=row["cron"],
        channel_key=row["channel_key"],
        flood_scope=row["flood_scope"],
        message=row["message"],
        enabled=bool(row["enabled"]),
        last_run_at=row["last_run_at"],
        last_result=row["last_result"],
        created_at=row["created_at"],
    )


class BotScheduleRepository:
    @staticmethod
    async def get_all() -> list[BotSchedule]:
        async with db.readonly() as conn:
            async with conn.execute("SELECT * FROM bot_schedules ORDER BY created_at") as cursor:
                rows = await cursor.fetchall()
        return [_row_to_schedule(row) for row in rows]

    @staticmethod
    async def get(schedule_id: str) -> BotSchedule | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM bot_schedules WHERE id = ?", (schedule_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_schedule(row) if row else None

    @staticmethod
    async def create(
        *,
        label: str,
        cron: str,
        channel_key: str,
        message: str,
        flood_scope: str | None = None,
        enabled: bool = True,
    ) -> BotSchedule:
        new_id = str(uuid.uuid4())
        async with db.tx() as conn:
            await conn.execute(
                """
                INSERT INTO bot_schedules (id, label, cron, channel_key, flood_scope,
                                           message, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    label,
                    cron,
                    channel_key,
                    flood_scope,
                    message,
                    1 if enabled else 0,
                    int(time.time()),
                ),
            )
        schedule = await BotScheduleRepository.get(new_id)
        assert schedule is not None
        return schedule

    @staticmethod
    async def update(schedule_id: str, **fields: Any) -> BotSchedule | None:
        allowed = {"label", "cron", "channel_key", "flood_scope", "message", "enabled"}
        updates: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            updates.append(f"{key} = ?")
            params.append(1 if key == "enabled" and value else 0 if key == "enabled" else value)
        if updates:
            params.append(schedule_id)
            async with db.tx() as conn:
                await conn.execute(
                    f"UPDATE bot_schedules SET {', '.join(updates)} WHERE id = ?", params
                )
        return await BotScheduleRepository.get(schedule_id)

    @staticmethod
    async def record_run(schedule_id: str, result: str) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE bot_schedules SET last_run_at = ?, last_result = ? WHERE id = ?",
                (int(time.time()), result, schedule_id),
            )

    @staticmethod
    async def delete(schedule_id: str) -> bool:
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM bot_schedules WHERE id = ?", (schedule_id,)
            ) as cursor:
                return cursor.rowcount > 0


def _row_to_feed(row: aiosqlite.Row) -> BotFeed:
    return BotFeed(
        id=row["id"],
        name=row["name"],
        feed_type=row["feed_type"],
        url=row["url"],
        channel_key=row["channel_key"],
        interval_seconds=row["interval_seconds"] or 1800,
        format=row["format"],
        items_path=row["items_path"],
        enabled=bool(row["enabled"]),
        last_item_id=row["last_item_id"],
        last_check_at=row["last_check_at"],
        last_error=row["last_error"],
        error_count=row["error_count"] or 0,
        items_posted=row["items_posted"] or 0,
        max_posts_per_check=row["max_posts_per_check"] or 3,
        created_at=row["created_at"],
    )


class BotFeedRepository:
    @staticmethod
    async def get_all() -> list[BotFeed]:
        async with db.readonly() as conn:
            async with conn.execute("SELECT * FROM bot_feeds ORDER BY created_at") as cursor:
                rows = await cursor.fetchall()
        return [_row_to_feed(row) for row in rows]

    @staticmethod
    async def get(feed_id: str) -> BotFeed | None:
        async with db.readonly() as conn:
            async with conn.execute("SELECT * FROM bot_feeds WHERE id = ?", (feed_id,)) as cursor:
                row = await cursor.fetchone()
        return _row_to_feed(row) if row else None

    @staticmethod
    async def create(
        *,
        name: str,
        feed_type: str,
        url: str,
        channel_key: str,
        interval_seconds: int,
        format: str,
        items_path: str | None = None,
        max_posts_per_check: int = 3,
        enabled: bool = True,
    ) -> BotFeed:
        new_id = str(uuid.uuid4())
        async with db.tx() as conn:
            await conn.execute(
                """
                INSERT INTO bot_feeds (id, name, feed_type, url, channel_key,
                                       interval_seconds, format, items_path,
                                       max_posts_per_check, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    name,
                    feed_type,
                    url,
                    channel_key,
                    interval_seconds,
                    format,
                    items_path,
                    max_posts_per_check,
                    1 if enabled else 0,
                    int(time.time()),
                ),
            )
        feed = await BotFeedRepository.get(new_id)
        assert feed is not None
        return feed

    @staticmethod
    async def update(feed_id: str, **fields: Any) -> BotFeed | None:
        allowed = {
            "name",
            "feed_type",
            "url",
            "channel_key",
            "interval_seconds",
            "format",
            "items_path",
            "max_posts_per_check",
            "enabled",
        }
        updates: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            updates.append(f"{key} = ?")
            params.append(1 if key == "enabled" and value else 0 if key == "enabled" else value)
        if updates:
            params.append(feed_id)
            async with db.tx() as conn:
                await conn.execute(
                    f"UPDATE bot_feeds SET {', '.join(updates)} WHERE id = ?", params
                )
        return await BotFeedRepository.get(feed_id)

    @staticmethod
    async def record_check(
        feed_id: str,
        *,
        newest_id: str | None,
        posted: int,
        error: str | None,
    ) -> None:
        async with db.tx() as conn:
            if error is None:
                await conn.execute(
                    """
                    UPDATE bot_feeds
                    SET last_check_at = ?, last_item_id = COALESCE(?, last_item_id),
                        items_posted = items_posted + ?, last_error = NULL, error_count = 0
                    WHERE id = ?
                    """,
                    (int(time.time()), newest_id, posted, feed_id),
                )
            else:
                await conn.execute(
                    """
                    UPDATE bot_feeds
                    SET last_check_at = ?, last_error = ?, error_count = error_count + 1
                    WHERE id = ?
                    """,
                    (int(time.time()), error, feed_id),
                )

    @staticmethod
    async def delete(feed_id: str) -> bool:
        async with db.tx() as conn:
            async with conn.execute("DELETE FROM bot_feeds WHERE id = ?", (feed_id,)) as cursor:
                return cursor.rowcount > 0


class BotEngineSettingsRepository:
    @staticmethod
    async def get() -> BotEngineSettings:
        async with db.readonly() as conn:
            async with conn.execute("SELECT * FROM bot_engine_settings WHERE id = 1") as cursor:
                row = await cursor.fetchone()
        if not row:
            return BotEngineSettings()
        return BotEngineSettings(
            command_prefix=row["command_prefix"] or "",
            require_prefix=bool(row["require_prefix"]),
            mention_mode=row["mention_mode"] or "also",
            global_reply_seconds=row["global_reply_seconds"] or 0,
            per_user_seconds=row["per_user_seconds"] or 0,
            tx_spacing_seconds=row["tx_spacing_seconds"] or 2.0,
            max_response_hops=row["max_response_hops"] or 64,
            default_language=row["default_language"] or "en",
            auto_detect_language=bool(row["auto_detect_language"]),
            banned_users=_load_json(row["banned_users"], []),
            profanity_mode=row["profanity_mode"] or "off",
            admin_users=_load_json(row["admin_users"], []),
        )

    @staticmethod
    async def update(**fields: Any) -> BotEngineSettings:
        json_fields = {"banned_users", "admin_users"}
        bool_fields = {"require_prefix", "auto_detect_language"}
        allowed = {
            "command_prefix",
            "require_prefix",
            "mention_mode",
            "global_reply_seconds",
            "per_user_seconds",
            "tx_spacing_seconds",
            "max_response_hops",
            "default_language",
            "auto_detect_language",
            "banned_users",
            "profanity_mode",
            "admin_users",
        }
        updates: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed or value is None:
                continue
            updates.append(f"{key} = ?")
            if key in json_fields:
                params.append(json.dumps(value))
            elif key in bool_fields:
                params.append(1 if value else 0)
            else:
                params.append(value)
        if updates:
            async with db.tx() as conn:
                await conn.execute(
                    f"UPDATE bot_engine_settings SET {', '.join(updates)} WHERE id = 1", params
                )
        return await BotEngineSettingsRepository.get()
