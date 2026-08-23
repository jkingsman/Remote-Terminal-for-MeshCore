"""Persistence for room-poll subscriptions: stored credential + poll schedule.

The credential is three-state and must never be read with a truthiness check:

* ``None``  -> no credential stored; the room cannot be auto-opened or polled.
* ``""``    -> guest login (MeshCore treats an empty password as a guest login).
* ``"pw"``  -> a real password.

``has_credential`` is therefore ``credential is not None``, and the poller
selects only rows where a credential is stored. The plaintext credential is
returned by the repository (the poller and the login path need it) but must
never be serialized to an API client — expose only the booleans in
:class:`RoomPollStatus`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.database import db

# Floor protects mesh airtime: each poll is a login round-trip over LoRa.
MIN_POLL_INTERVAL_SECONDS = 300
DEFAULT_POLL_INTERVAL_SECONDS = 1200


@dataclass
class RoomPollSubscription:
    room_key: str
    credential: str | None
    poll_enabled: bool
    interval_seconds: int
    last_poll_at: int | None
    last_result: str | None
    last_error: str | None
    consecutive_errors: int
    created_at: int

    @property
    def has_credential(self) -> bool:
        return self.credential is not None

    @property
    def is_guest_credential(self) -> bool:
        return self.credential == ""


def _row_to_sub(row: Any) -> RoomPollSubscription:
    return RoomPollSubscription(
        room_key=row["room_key"],
        credential=row["credential"],
        poll_enabled=bool(row["poll_enabled"]),
        interval_seconds=int(row["interval_seconds"]),
        last_poll_at=row["last_poll_at"],
        last_result=row["last_result"],
        last_error=row["last_error"],
        consecutive_errors=row["consecutive_errors"] or 0,
        created_at=row["created_at"] or 0,
    )


class RoomPollRepository:
    @staticmethod
    async def get(room_key: str) -> RoomPollSubscription | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM room_poll_subscriptions WHERE room_key = ?", (room_key,)
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_sub(row) if row else None

    @staticmethod
    async def get_pollable() -> list[RoomPollSubscription]:
        """Rows the background poller should consider: enabled AND credential stored.

        ``credential IS NOT NULL`` keeps guest rows (empty-string credential) in
        while excluding rows that only exist for other bookkeeping.
        """
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT * FROM room_poll_subscriptions
                WHERE poll_enabled = 1 AND credential IS NOT NULL
                ORDER BY created_at
                """
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_sub(row) for row in rows]

    @staticmethod
    async def upsert(
        room_key: str,
        *,
        enabled: bool | None = None,
        interval_seconds: int | None = None,
        credential_action: str = "keep",
        credential: str | None = None,
    ) -> RoomPollSubscription:
        """Create or update a subscription.

        ``credential_action`` is explicit so an empty-string guest credential is
        never confused with "leave unchanged":
          * ``"keep"``  -> credential column untouched (default).
          * ``"set"``   -> store ``credential`` verbatim ("" stores a guest login).
          * ``"clear"`` -> set credential to NULL.
        """
        existing = await RoomPollRepository.get(room_key)

        if credential_action == "set":
            new_credential = credential
        elif credential_action == "clear":
            new_credential = None
        else:  # keep
            new_credential = existing.credential if existing else None

        new_enabled = existing.poll_enabled if existing else False
        if enabled is not None:
            new_enabled = enabled

        new_interval = existing.interval_seconds if existing else DEFAULT_POLL_INTERVAL_SECONDS
        if interval_seconds is not None:
            new_interval = max(MIN_POLL_INTERVAL_SECONDS, int(interval_seconds))

        async with db.tx() as conn:
            await conn.execute(
                """
                INSERT INTO room_poll_subscriptions (
                    room_key, credential, poll_enabled, interval_seconds, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(room_key) DO UPDATE SET
                    credential = excluded.credential,
                    poll_enabled = excluded.poll_enabled,
                    interval_seconds = excluded.interval_seconds
                """,
                (
                    room_key,
                    new_credential,
                    1 if new_enabled else 0,
                    new_interval,
                    existing.created_at if existing else int(time.time()),
                ),
            )
        result = await RoomPollRepository.get(room_key)
        assert result is not None
        return result

    @staticmethod
    async def delete(room_key: str) -> bool:
        async with db.tx() as conn:
            cursor = await conn.execute(
                "DELETE FROM room_poll_subscriptions WHERE room_key = ?", (room_key,)
            )
            return cursor.rowcount > 0

    @staticmethod
    async def record_result(room_key: str, *, ok: bool, result: str, error: str | None) -> None:
        """Update bookkeeping after a poll cycle. On success the error streak resets."""
        async with db.tx() as conn:
            if ok:
                await conn.execute(
                    """
                    UPDATE room_poll_subscriptions
                    SET last_poll_at = ?, last_result = ?, last_error = NULL,
                        consecutive_errors = 0
                    WHERE room_key = ?
                    """,
                    (int(time.time()), result, room_key),
                )
            else:
                await conn.execute(
                    """
                    UPDATE room_poll_subscriptions
                    SET last_poll_at = ?, last_result = ?, last_error = ?,
                        consecutive_errors = consecutive_errors + 1
                    WHERE room_key = ?
                    """,
                    (int(time.time()), result, error, room_key),
                )

    @staticmethod
    async def set_enabled(room_key: str, enabled: bool) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE room_poll_subscriptions SET poll_enabled = ? WHERE room_key = ?",
                (1 if enabled else 0, room_key),
            )
