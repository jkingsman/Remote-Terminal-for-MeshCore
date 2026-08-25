import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add MCMP compression/signing flags to channels and contacts.

    1. channels.mcmp_enabled / channels.mcmp_sign_enabled (default 0)
    2. contacts.mcmp_enabled (default 0)

    These flags are local-only settings controlling outbound MCMP v3 encoding;
    they do not affect radio sync and are intentionally left untouched on
    conflict/upsert paths (see repository changes).
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await tables_cursor.fetchall()}

    # --- channels ---
    if "channels" in existing_tables:
        col_cursor = await conn.execute("PRAGMA table_info(channels)")
        channel_columns = {row[1] for row in await col_cursor.fetchall()}
        if "mcmp_enabled" not in channel_columns:
            await conn.execute(
                "ALTER TABLE channels ADD COLUMN mcmp_enabled INTEGER NOT NULL DEFAULT 0"
            )
        if "mcmp_sign_enabled" not in channel_columns:
            await conn.execute(
                "ALTER TABLE channels ADD COLUMN mcmp_sign_enabled INTEGER NOT NULL DEFAULT 0"
            )
        await conn.commit()

    # --- contacts ---
    if "contacts" in existing_tables:
        col_cursor = await conn.execute("PRAGMA table_info(contacts)")
        contact_columns = {row[1] for row in await col_cursor.fetchall()}
        if "mcmp_enabled" not in contact_columns:
            await conn.execute(
                "ALTER TABLE contacts ADD COLUMN mcmp_enabled INTEGER NOT NULL DEFAULT 0"
            )
        await conn.commit()

    logger.info("Migrated MCMP channel/contact flags")