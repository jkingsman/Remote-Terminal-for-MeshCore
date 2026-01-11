import time
from typing import Any

from app.database import db
from app.models import Channel, Contact, Message, RawPacket


class ContactRepository:
    @staticmethod
    async def upsert(contact: dict[str, Any]) -> None:
        await db.conn.execute(
            """
            INSERT INTO contacts (public_key, name, type, flags, last_path, last_path_len,
                                  last_advert, lat, lon, last_seen, on_radio, last_contacted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(public_key) DO UPDATE SET
                name = COALESCE(excluded.name, contacts.name),
                type = CASE WHEN excluded.type = 0 THEN contacts.type ELSE excluded.type END,
                flags = excluded.flags,
                last_path = COALESCE(excluded.last_path, contacts.last_path),
                last_path_len = excluded.last_path_len,
                last_advert = COALESCE(excluded.last_advert, contacts.last_advert),
                lat = COALESCE(excluded.lat, contacts.lat),
                lon = COALESCE(excluded.lon, contacts.lon),
                last_seen = excluded.last_seen,
                on_radio = excluded.on_radio,
                last_contacted = COALESCE(excluded.last_contacted, contacts.last_contacted)
            """,
            (
                contact.get("public_key"),
                contact.get("name") or contact.get("adv_name"),
                contact.get("type", 0),
                contact.get("flags", 0),
                contact.get("last_path") or contact.get("out_path"),
                contact.get("last_path_len") if "last_path_len" in contact else contact.get("out_path_len", -1),
                contact.get("last_advert"),
                contact.get("lat") or contact.get("adv_lat"),
                contact.get("lon") or contact.get("adv_lon"),
                contact.get("last_seen", int(time.time())),
                contact.get("on_radio", False),
                contact.get("last_contacted"),
            ),
        )
        await db.conn.commit()

    @staticmethod
    def _row_to_contact(row) -> Contact:
        """Convert a database row to a Contact model."""
        return Contact(
            public_key=row["public_key"],
            name=row["name"],
            type=row["type"],
            flags=row["flags"],
            last_path=row["last_path"],
            last_path_len=row["last_path_len"],
            last_advert=row["last_advert"],
            lat=row["lat"],
            lon=row["lon"],
            last_seen=row["last_seen"],
            on_radio=bool(row["on_radio"]),
            last_contacted=row["last_contacted"],
        )

    @staticmethod
    async def get_by_key(public_key: str) -> Contact | None:
        cursor = await db.conn.execute(
            "SELECT * FROM contacts WHERE public_key = ?", (public_key,)
        )
        row = await cursor.fetchone()
        return ContactRepository._row_to_contact(row) if row else None

    @staticmethod
    async def get_by_key_prefix(prefix: str) -> Contact | None:
        cursor = await db.conn.execute(
            "SELECT * FROM contacts WHERE public_key LIKE ? LIMIT 1",
            (f"{prefix}%",),
        )
        row = await cursor.fetchone()
        return ContactRepository._row_to_contact(row) if row else None

    @staticmethod
    async def get_by_key_or_prefix(key_or_prefix: str) -> Contact | None:
        """Get a contact by exact key match, falling back to prefix match.

        Useful when the input might be a full 64-char public key or a shorter prefix.
        """
        contact = await ContactRepository.get_by_key(key_or_prefix)
        if not contact:
            contact = await ContactRepository.get_by_key_prefix(key_or_prefix)
        return contact

    @staticmethod
    async def get_all(limit: int = 100, offset: int = 0) -> list[Contact]:
        cursor = await db.conn.execute(
            "SELECT * FROM contacts ORDER BY COALESCE(name, public_key) LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def get_recent_non_repeaters(limit: int = 200) -> list[Contact]:
        """Get the most recently active non-repeater contacts.

        Orders by most recent activity (last_contacted or last_advert),
        excluding repeaters (type=2).
        """
        cursor = await db.conn.execute(
            """
            SELECT * FROM contacts
            WHERE type != 2
            ORDER BY COALESCE(last_contacted, 0) DESC, COALESCE(last_advert, 0) DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def update_path(public_key: str, path: str, path_len: int) -> None:
        await db.conn.execute(
            "UPDATE contacts SET last_path = ?, last_path_len = ?, last_seen = ? WHERE public_key = ?",
            (path, path_len, int(time.time()), public_key),
        )
        await db.conn.commit()

    @staticmethod
    async def set_on_radio(public_key: str, on_radio: bool) -> None:
        await db.conn.execute(
            "UPDATE contacts SET on_radio = ? WHERE public_key = ?",
            (on_radio, public_key),
        )
        await db.conn.commit()

    @staticmethod
    async def delete(public_key: str) -> None:
        await db.conn.execute(
            "DELETE FROM contacts WHERE public_key = ?",
            (public_key,),
        )
        await db.conn.commit()

    @staticmethod
    async def update_last_contacted(public_key: str, timestamp: int | None = None) -> None:
        """Update the last_contacted timestamp for a contact."""
        ts = timestamp or int(time.time())
        await db.conn.execute(
            "UPDATE contacts SET last_contacted = ?, last_seen = ? WHERE public_key = ?",
            (ts, ts, public_key),
        )
        await db.conn.commit()

    @staticmethod
    async def clear_all_on_radio() -> None:
        """Clear the on_radio flag for all contacts."""
        await db.conn.execute("UPDATE contacts SET on_radio = 0")
        await db.conn.commit()

    @staticmethod
    async def set_multiple_on_radio(public_keys: list[str], on_radio: bool = True) -> None:
        """Set on_radio flag for multiple contacts."""
        if not public_keys:
            return
        placeholders = ",".join("?" * len(public_keys))
        await db.conn.execute(
            f"UPDATE contacts SET on_radio = ? WHERE public_key IN ({placeholders})",
            [on_radio] + public_keys,
        )
        await db.conn.commit()


class ChannelRepository:
    @staticmethod
    async def upsert(key: str, name: str, is_hashtag: bool = False, on_radio: bool = False) -> None:
        """Upsert a channel. Key is 32-char hex string."""
        await db.conn.execute(
            """
            INSERT INTO channels (key, name, is_hashtag, on_radio)
            VALUES (?, ?, ?, ?)
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
            "SELECT key, name, is_hashtag, on_radio FROM channels WHERE key = ?",
            (key.upper(),)
        )
        row = await cursor.fetchone()
        if row:
            return Channel(
                key=row["key"],
                name=row["name"],
                is_hashtag=bool(row["is_hashtag"]),
                on_radio=bool(row["on_radio"]),
            )
        return None

    @staticmethod
    async def get_all() -> list[Channel]:
        cursor = await db.conn.execute(
            "SELECT key, name, is_hashtag, on_radio FROM channels ORDER BY name"
        )
        rows = await cursor.fetchall()
        return [
            Channel(
                key=row["key"],
                name=row["name"],
                is_hashtag=bool(row["is_hashtag"]),
                on_radio=bool(row["on_radio"]),
            )
            for row in rows
        ]

    @staticmethod
    async def get_by_name(name: str) -> Channel | None:
        """Get a channel by name."""
        cursor = await db.conn.execute(
            "SELECT key, name, is_hashtag, on_radio FROM channels WHERE name = ?", (name,)
        )
        row = await cursor.fetchone()
        if row:
            return Channel(
                key=row["key"],
                name=row["name"],
                is_hashtag=bool(row["is_hashtag"]),
                on_radio=bool(row["on_radio"]),
            )
        return None

    @staticmethod
    async def delete(key: str) -> None:
        """Delete a channel by key."""
        await db.conn.execute(
            "DELETE FROM channels WHERE key = ?",
            (key.upper(),),
        )
        await db.conn.commit()


class MessageRepository:
    @staticmethod
    async def create(
        msg_type: str,
        text: str,
        received_at: int,
        conversation_key: str,
        sender_timestamp: int | None = None,
        path_len: int | None = None,
        txt_type: int = 0,
        signature: str | None = None,
        outgoing: bool = False,
    ) -> int | None:
        """Create a message, returning the ID or None if duplicate.

        Uses INSERT OR IGNORE to handle the UNIQUE constraint on
        (type, conversation_key, text, sender_timestamp). This prevents
        duplicate messages when the same message arrives via multiple RF paths.
        """
        cursor = await db.conn.execute(
            """
            INSERT OR IGNORE INTO messages (type, conversation_key, text, sender_timestamp,
                                            received_at, path_len, txt_type, signature, outgoing)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (msg_type, conversation_key, text, sender_timestamp, received_at,
             path_len, txt_type, signature, outgoing),
        )
        await db.conn.commit()
        # lastrowid is 0 if no row was inserted (duplicate)
        return cursor.lastrowid if cursor.lastrowid else None

    @staticmethod
    async def get_all(
        limit: int = 100,
        offset: int = 0,
        msg_type: str | None = None,
        conversation_key: str | None = None,
    ) -> list[Message]:
        query = "SELECT * FROM messages WHERE 1=1"
        params: list[Any] = []

        if msg_type:
            query += " AND type = ?"
            params.append(msg_type)
        if conversation_key:
            # Support both exact match and prefix match for DMs
            query += " AND conversation_key LIKE ?"
            params.append(f"{conversation_key}%")

        query += " ORDER BY received_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = await db.conn.execute(query, params)
        rows = await cursor.fetchall()
        return [
            Message(
                id=row["id"],
                type=row["type"],
                conversation_key=row["conversation_key"],
                text=row["text"],
                sender_timestamp=row["sender_timestamp"],
                received_at=row["received_at"],
                path_len=row["path_len"],
                txt_type=row["txt_type"],
                signature=row["signature"],
                outgoing=bool(row["outgoing"]),
                acked=row["acked"],
            )
            for row in rows
        ]

    @staticmethod
    async def increment_ack_count(message_id: int) -> int:
        """Increment ack count and return the new value."""
        await db.conn.execute(
            "UPDATE messages SET acked = acked + 1 WHERE id = ?", (message_id,)
        )
        await db.conn.commit()
        cursor = await db.conn.execute(
            "SELECT acked FROM messages WHERE id = ?", (message_id,)
        )
        row = await cursor.fetchone()
        return row["acked"] if row else 1

    @staticmethod
    async def find_duplicate(
        conversation_key: str,
        text: str,
        sender_timestamp: int | None,
    ) -> int | None:
        """Find existing message with same content (for deduplication).

        Returns message ID if found, None otherwise.
        Used to detect the same message arriving via multiple RF paths.
        """
        if sender_timestamp is None:
            return None

        cursor = await db.conn.execute(
            """
            SELECT id FROM messages
            WHERE conversation_key = ?
              AND text = ?
              AND sender_timestamp = ?
            LIMIT 1
            """,
            (conversation_key, text, sender_timestamp),
        )
        row = await cursor.fetchone()
        return row["id"] if row else None

    @staticmethod
    async def get_bulk(
        conversations: list[dict],
        limit_per_conversation: int = 100,
    ) -> dict[str, list["Message"]]:
        """Fetch messages for multiple conversations in one query per conversation.

        Args:
            conversations: List of {type: 'PRIV'|'CHAN', conversation_key: string}
            limit_per_conversation: Max messages to return per conversation

        Returns:
            Dict mapping 'type:conversation_key' to list of messages
        """
        result: dict[str, list[Message]] = {}

        for conv in conversations:
            msg_type = conv.get("type")
            conv_key = conv.get("conversation_key")
            if not msg_type or not conv_key:
                continue

            key = f"{msg_type}:{conv_key}"

            cursor = await db.conn.execute(
                """
                SELECT * FROM messages
                WHERE type = ? AND conversation_key LIKE ?
                ORDER BY received_at DESC
                LIMIT ?
                """,
                (msg_type, f"{conv_key}%", limit_per_conversation),
            )
            rows = await cursor.fetchall()
            result[key] = [
                Message(
                    id=row["id"],
                    type=row["type"],
                    conversation_key=row["conversation_key"],
                    text=row["text"],
                    sender_timestamp=row["sender_timestamp"],
                    received_at=row["received_at"],
                    path_len=row["path_len"],
                    txt_type=row["txt_type"],
                    signature=row["signature"],
                    outgoing=bool(row["outgoing"]),
                    acked=row["acked"],
                )
                for row in rows
            ]

        return result


class RawPacketRepository:
    @staticmethod
    async def create(data: bytes, timestamp: int | None = None) -> int | None:
        """Create a raw packet. Returns None if duplicate (same data already exists)."""
        ts = timestamp or int(time.time())

        cursor = await db.conn.execute(
            "INSERT OR IGNORE INTO raw_packets (timestamp, data) VALUES (?, ?)",
            (ts, data),
        )
        await db.conn.commit()
        # rowcount is 0 if INSERT was ignored due to duplicate, 1 if inserted
        if cursor.rowcount == 0:
            return None
        return cursor.lastrowid

    @staticmethod
    async def get_undecrypted_count() -> int:
        """Get count of undecrypted packets."""
        cursor = await db.conn.execute(
            "SELECT COUNT(*) as count FROM raw_packets WHERE decrypted = 0"
        )
        row = await cursor.fetchone()
        return row["count"] if row else 0

    @staticmethod
    async def get_all_undecrypted() -> list[tuple[int, bytes, int]]:
        """Get all undecrypted packets as (id, data, timestamp) tuples."""
        cursor = await db.conn.execute(
            "SELECT id, data, timestamp FROM raw_packets WHERE decrypted = 0 ORDER BY timestamp ASC"
        )
        rows = await cursor.fetchall()
        return [(row["id"], bytes(row["data"]), row["timestamp"]) for row in rows]

    @staticmethod
    async def mark_decrypted(packet_id: int, message_id: int) -> None:
        await db.conn.execute(
            "UPDATE raw_packets SET decrypted = 1, message_id = ? WHERE id = ?",
            (message_id, packet_id),
        )
        await db.conn.commit()

    @staticmethod
    async def get_undecrypted(limit: int = 100) -> list[RawPacket]:
        cursor = await db.conn.execute(
            """
            SELECT * FROM raw_packets
            WHERE decrypted = 0
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            RawPacket(
                id=row["id"],
                timestamp=row["timestamp"],
                data=row["data"].hex(),
                decrypted=bool(row["decrypted"]),
                message_id=row["message_id"],
                decrypt_attempts=row["decrypt_attempts"],
                last_attempt=row["last_attempt"],
            )
            for row in rows
        ]

    @staticmethod
    async def increment_attempts(packet_id: int) -> None:
        await db.conn.execute(
            """
            UPDATE raw_packets
            SET decrypt_attempts = decrypt_attempts + 1, last_attempt = ?
            WHERE id = ?
            """,
            (int(time.time()), packet_id),
        )
        await db.conn.commit()
