import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add the mcmp_enabled opt-in column to contacts and channels.

    When set, outbound messages to that contact / channel are MCMP-compressed
    before sending. Off by default: compression must be enabled per conversation
    because the receiver has to understand it.
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
        if "mcmp_enabled" not in columns:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN mcmp_enabled INTEGER DEFAULT 0")

    await conn.commit()
