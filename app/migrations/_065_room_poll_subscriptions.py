import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Create room_poll_subscriptions: per-room stored credential + poll schedule.

    ``credential`` is deliberately three-state and never interpreted with a
    truthiness check: NULL = no credential stored (cannot auto-open or poll),
    '' = guest login (valid, MeshCore's own convention for empty-password
    logins), any other string = password. ``poll_enabled`` requires a stored
    credential; the background poller only picks up rows with credential
    IS NOT NULL.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}

    if "room_poll_subscriptions" not in tables:
        await conn.execute(
            """
            CREATE TABLE room_poll_subscriptions (
                room_key TEXT PRIMARY KEY,
                credential TEXT,
                poll_enabled INTEGER NOT NULL DEFAULT 0,
                interval_seconds INTEGER NOT NULL DEFAULT 1200,
                last_poll_at INTEGER,
                last_result TEXT,
                last_error TEXT,
                consecutive_errors INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (room_key) REFERENCES contacts(public_key) ON DELETE CASCADE
            )
            """
        )

    await conn.commit()
