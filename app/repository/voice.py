from __future__ import annotations

import time
from typing import Any

from app.database import db


class VoiceRepository:
    @staticmethod
    async def upsert_session(
        *,
        session_id: str,
        message_id: int | None,
        direction: str,
        conversation_type: str,
        conversation_key: str,
        peer_public_key: str | None,
        mode: int,
        duration_ms: int,
        packet_count: int,
        state: str,
        ttl_seconds: int = 86_400,
    ) -> None:
        now = int(time.time())
        async with db.tx() as conn:
            await conn.execute(
                """
                INSERT INTO voice_sessions (
                    session_id, message_id, direction, conversation_type,
                    conversation_key, peer_public_key, mode, duration_ms,
                    packet_count, state, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    message_id=COALESCE(excluded.message_id, voice_sessions.message_id),
                    conversation_key=excluded.conversation_key,
                    peer_public_key=COALESCE(excluded.peer_public_key, voice_sessions.peer_public_key),
                    state=CASE WHEN voice_sessions.state='complete' THEN 'complete' ELSE excluded.state END,
                    expires_at=excluded.expires_at
                """,
                (
                    session_id,
                    message_id,
                    direction,
                    conversation_type,
                    conversation_key,
                    peer_public_key,
                    mode,
                    duration_ms,
                    packet_count,
                    state,
                    now,
                    now + ttl_seconds,
                ),
            )

    @staticmethod
    async def add_fragment(session_id: str, index: int, data: bytes) -> bool:
        async with db.tx() as conn:
            async with conn.execute(
                "INSERT OR IGNORE INTO voice_fragments (session_id, packet_index, codec2_data) VALUES (?, ?, ?)",
                (session_id, index, data),
            ) as cursor:
                inserted = cursor.rowcount > 0
            await conn.execute(
                """
                UPDATE voice_sessions SET state = CASE
                    WHEN packet_count <= (SELECT COUNT(*) FROM voice_fragments WHERE session_id=?)
                    THEN 'complete' ELSE 'receiving' END
                WHERE session_id=?
                """,
                (session_id, session_id),
            )
            return inserted

    @staticmethod
    async def get(session_id: str) -> dict[str, Any] | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM voice_sessions WHERE session_id=?", (session_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            result = dict(row)
            async with conn.execute(
                "SELECT packet_index, codec2_data FROM voice_fragments WHERE session_id=? ORDER BY packet_index",
                (session_id,),
            ) as fragments:
                result["fragments"] = [(r[0], bytes(r[1])) for r in await fragments.fetchall()]
            return result

    @staticmethod
    async def cleanup_expired() -> int:
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM voice_sessions WHERE expires_at < ?", (int(time.time()),)
            ) as cursor:
                return cursor.rowcount

    @staticmethod
    async def enforce_cache_limit(max_sessions: int = 128) -> int:
        """Expire stale sessions and retain only the newest bounded cache."""
        async with db.tx() as conn:
            await conn.execute(
                "DELETE FROM voice_sessions WHERE expires_at < ?", (int(time.time()),)
            )
            async with conn.execute(
                """
                DELETE FROM voice_sessions WHERE session_id IN (
                    SELECT session_id FROM voice_sessions
                    ORDER BY created_at DESC, session_id DESC LIMIT -1 OFFSET ?
                )
                """,
                (max_sessions,),
            ) as cursor:
                return cursor.rowcount
