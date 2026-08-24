import aiosqlite


async def migrate(conn: aiosqlite.Connection) -> None:
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS voice_sessions (
            session_id TEXT PRIMARY KEY,
            message_id INTEGER,
            direction TEXT NOT NULL CHECK(direction IN ('incoming', 'outgoing')),
            conversation_type TEXT NOT NULL CHECK(conversation_type IN ('PRIV', 'CHAN')),
            conversation_key TEXT NOT NULL,
            peer_public_key TEXT,
            mode INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL CHECK(duration_ms BETWEEN 0 AND 10000),
            packet_count INTEGER NOT NULL CHECK(packet_count BETWEEN 1 AND 255),
            state TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS voice_fragments (
            session_id TEXT NOT NULL,
            packet_index INTEGER NOT NULL CHECK(packet_index BETWEEN 0 AND 254),
            codec2_data BLOB NOT NULL CHECK(length(codec2_data) BETWEEN 1 AND 174),
            PRIMARY KEY (session_id, packet_index),
            FOREIGN KEY (session_id) REFERENCES voice_sessions(session_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_voice_sessions_expiry ON voice_sessions(expires_at);
        """
    )
    await conn.commit()
