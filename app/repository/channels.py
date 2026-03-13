import time

from app.database import db
from app.models import Channel


class ChannelRepository:
    @staticmethod
    async def upsert(key: str, name: str, is_hashtag: bool = False, on_radio: bool = False) -> None:
        """Upsert a channel. Key is 32-char hex string."""
        await db.conn.execute(
            """
            INSERT INTO channels (key, name, is_hashtag, on_radio, flood_scope_override)
            VALUES (?, ?, ?, ?, NULL)
            ON CONFLICT(key) DO UPDATE SET
                name = excluded.name,
                is_hashtag = excluded.is_hashtag,
                on_radio = excluded.on_radio
            """,
            (key.upper(), name, is_hashtag, on_radio),
        )
        await db.conn.commit()

    @staticmethod
    async def get_by_key(key: str) -> Channel | None:
        """Get a channel by its key (32-char hex string)."""
        cursor = await db.conn.execute(
            """
            SELECT key, name, is_hashtag, on_radio, flood_scope_override, last_read_at
            FROM channels
            WHERE key = ?
            """,
            (key.upper(),),
        )
        row = await cursor.fetchone()
        if row:
            return Channel(
                key=row["key"],
                name=row["name"],
                is_hashtag=bool(row["is_hashtag"]),
                on_radio=bool(row["on_radio"]),
                flood_scope_override=row["flood_scope_override"],
                last_read_at=row["last_read_at"],
            )
        return None

    @staticmethod
    async def get_all() -> list[Channel]:
        cursor = await db.conn.execute(
            """
            SELECT key, name, is_hashtag, on_radio, flood_scope_override, last_read_at
            FROM channels
            ORDER BY name
            """
        )
        rows = await cursor.fetchall()
        return [
            Channel(
                key=row["key"],
                name=row["name"],
                is_hashtag=bool(row["is_hashtag"]),
                on_radio=bool(row["on_radio"]),
                flood_scope_override=row["flood_scope_override"],
                last_read_at=row["last_read_at"],
            )
            for row in rows
        ]

    @staticmethod
    async def get_on_radio() -> list[Channel]:
        """Return channels currently marked as resident on the radio in the database."""
        cursor = await db.conn.execute(
            """
            SELECT key, name, is_hashtag, on_radio, flood_scope_override, last_read_at
            FROM channels
            WHERE on_radio = 1
            ORDER BY name
            """
        )
        rows = await cursor.fetchall()
        return [
            Channel(
                key=row["key"],
                name=row["name"],
                is_hashtag=bool(row["is_hashtag"]),
                on_radio=bool(row["on_radio"]),
                flood_scope_override=row["flood_scope_override"],
                last_read_at=row["last_read_at"],
            )
            for row in rows
        ]

    @staticmethod
    async def delete(key: str) -> None:
        """Delete a channel by key."""
        await db.conn.execute(
            "DELETE FROM channels WHERE key = ?",
            (key.upper(),),
        )
        await db.conn.commit()

    @staticmethod
    async def update_last_read_at(key: str, timestamp: int | None = None) -> bool:
        """Update the last_read_at timestamp for a channel.

        Returns True if a row was updated, False if channel not found.
        """
        ts = timestamp if timestamp is not None else int(time.time())
        cursor = await db.conn.execute(
            "UPDATE channels SET last_read_at = ? WHERE key = ?",
            (ts, key.upper()),
        )
        await db.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    async def update_flood_scope_override(key: str, flood_scope_override: str | None) -> bool:
        """Set or clear a channel's flood-scope override."""
        cursor = await db.conn.execute(
            "UPDATE channels SET flood_scope_override = ? WHERE key = ?",
            (flood_scope_override, key.upper()),
        )
        await db.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    async def mark_all_read(timestamp: int) -> None:
        """Mark all channels as read at the given timestamp."""
        await db.conn.execute("UPDATE channels SET last_read_at = ?", (timestamp,))
        await db.conn.commit()
