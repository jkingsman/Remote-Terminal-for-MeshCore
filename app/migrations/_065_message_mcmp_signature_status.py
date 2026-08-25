import logging

import aiosqlite

logger = logging.getLogger(__name__)

async def migrate(conn: aiosqlite.Connection) -> None:
    col_cursor = await conn.execute("PRAGMA table_info(messages)")
    message_columns = {row[1] for row in await col_cursor.fetchall()}
    if "mcmp_signature_status" not in message_columns:
        await conn.execute(
            "ALTER TABLE messages ADD COLUMN mcmp_signature_status TEXT"
        )
        await conn.commit()
        logger.info("Added messages.mcmp_signature_status")