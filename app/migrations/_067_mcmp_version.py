import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add the mcmp_version column to contacts and channels.

    Selects which MCMP transport is used when compression is enabled for the
    conversation: 2 = v2 (``mcmp2:``, default), 3 = v3 container (``mcmp3:``).
    """
    for table in ("contacts", "channels"):
        table_check = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if not await table_check.fetchone():
            continue

        cursor = await conn.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await cursor.fetchall()}
        if "mcmp_version" not in columns:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN mcmp_version INTEGER DEFAULT 2")

    await conn.commit()
