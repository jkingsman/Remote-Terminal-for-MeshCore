import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Bump earlier ollama defaults to localhost + phi3:mini when still unset."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return
    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "ollama_base_url" not in columns or "ollama_model" not in columns:
        await conn.commit()
        return

    await conn.execute(
        """
        UPDATE app_settings
        SET ollama_base_url = 'http://localhost:11434'
        WHERE id = 1
          AND (ollama_base_url IS NULL
               OR ollama_base_url = ''
               OR ollama_base_url = 'http://127.0.0.1:11434')
        """
    )
    await conn.execute(
        """
        UPDATE app_settings
        SET ollama_model = 'phi3:mini'
        WHERE id = 1
          AND (ollama_model IS NULL OR ollama_model = '')
        """
    )
    await conn.commit()
