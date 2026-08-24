"""Migrate legacy fanout ``bot`` configs into the new ``bots`` table.

The Bots workspace replaces the fanout-config bot type. Each legacy row's
Python code moves verbatim into a ``bots`` row (the engine auto-wraps the
``def bot(**kwargs)`` form), keeping its name and enabled state. The fanout
rows are then deleted so the integration list stops offering them.

The ``bots`` table itself is created by SCHEMA_TABLES before migrations run.
"""

import json
import time
import uuid

import aiosqlite


async def migrate(conn: aiosqlite.Connection) -> None:
    # Guard for synthetic-schema migration tests and partial databases: both
    # tables normally exist (SCHEMA_TABLES runs before migrations), but skip
    # gracefully when they don't.
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('fanout_configs', 'bots')"
    ) as cursor:
        tables = {row[0] for row in await cursor.fetchall()}
    if "fanout_configs" not in tables or "bots" not in tables:
        await conn.commit()
        return

    async with conn.execute(
        "SELECT id, name, enabled, config FROM fanout_configs WHERE type = 'bot'"
    ) as cursor:
        rows = await cursor.fetchall()

    if not rows:
        await conn.commit()
        return

    async with conn.execute("SELECT name FROM bots") as cursor:
        existing_names = {row[0] for row in await cursor.fetchall()}

    now = int(time.time())
    for row in rows:
        legacy_id, name, enabled, config_raw = row[0], row[1], row[2], row[3]
        try:
            config = json.loads(config_raw) if config_raw else {}
        except (json.JSONDecodeError, TypeError):
            config = {}
        code = config.get("code", "") if isinstance(config, dict) else ""

        base_name = name or "Migrated Bot"
        candidate = base_name
        suffix = 2
        while candidate in existing_names:
            candidate = f"{base_name} {suffix}"
            suffix += 1
        existing_names.add(candidate)

        await conn.execute(
            """
            INSERT INTO bots (
                id, name, category, description, code, enabled, admin_only,
                respond_to_dms, scope, cooldown_seconds, per_user_cooldown_seconds,
                queue_threshold_seconds, settings_schema, settings, ui_triggers,
                state, builtin_key, builtin_version, modified, sort_order,
                created_at, updated_at
            ) VALUES (?, ?, 'Custom', ?, ?, ?, 0, 1, ?, 0, 0, 0, '[]', '{}', '[]',
                      '{}', NULL, NULL, 1, 0, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                candidate,
                "Migrated from MQTT & Automation (legacy def bot code)",
                code,
                1 if enabled else 0,
                json.dumps({"channels": "all"}),
                now,
                now,
            ),
        )
        await conn.execute("DELETE FROM fanout_configs WHERE id = ?", (legacy_id,))

    await conn.commit()
