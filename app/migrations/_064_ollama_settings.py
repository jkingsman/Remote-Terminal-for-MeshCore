import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add ollama_base_url and ollama_model columns to app_settings."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return
    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "ollama_base_url" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN ollama_base_url TEXT DEFAULT 'http://localhost:11434'"
        )
    if "ollama_model" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN ollama_model TEXT DEFAULT 'phi3:mini'"
        )
    await conn.commit()
