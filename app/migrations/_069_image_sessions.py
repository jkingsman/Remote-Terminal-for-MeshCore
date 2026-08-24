import aiosqlite


async def migrate(conn: aiosqlite.Connection) -> None:
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS image_sessions (
            session_id TEXT PRIMARY KEY,
            message_id INTEGER,
            direction TEXT NOT NULL CHECK(direction IN ('incoming', 'outgoing')),
            conversation_type TEXT NOT NULL CHECK(conversation_type IN ('PRIV', 'CHAN')),
            conversation_key TEXT NOT NULL,
            peer_public_key TEXT,
            format INTEGER NOT NULL CHECK(format IN (0, 1)),
            width INTEGER NOT NULL CHECK(width BETWEEN 1 AND 256),
            height INTEGER NOT NULL CHECK(height BETWEEN 1 AND 256),
            size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 38760),
            fragment_count INTEGER NOT NULL CHECK(fragment_count BETWEEN 1 AND 255),
            state TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS image_fragments (
            session_id TEXT NOT NULL,
            fragment_index INTEGER NOT NULL CHECK(fragment_index BETWEEN 0 AND 254),
            image_data BLOB NOT NULL CHECK(length(image_data) BETWEEN 1 AND 152),
            PRIMARY KEY (session_id, fragment_index),
            FOREIGN KEY (session_id) REFERENCES image_sessions(session_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_image_sessions_expiry ON image_sessions(expires_at);
        """
    )
    await conn.commit()
