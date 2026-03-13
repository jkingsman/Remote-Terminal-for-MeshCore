import time
from collections.abc import Mapping
from typing import Any

from app.database import db
from app.models import (
    Contact,
    ContactAdvertPath,
    ContactAdvertPathSummary,
    ContactNameHistory,
    ContactUpsert,
)
from app.path_utils import first_hop_hex, normalize_contact_route, normalize_route_override


class AmbiguousPublicKeyPrefixError(ValueError):
    """Raised when a public key prefix matches multiple contacts."""

    def __init__(self, prefix: str, matches: list[str]):
        self.prefix = prefix.lower()
        self.matches = matches
        super().__init__(f"Ambiguous public key prefix '{self.prefix}'")


class ContactRepository:
    @staticmethod
    def _coerce_contact_upsert(
        contact: ContactUpsert | Contact | Mapping[str, Any],
    ) -> ContactUpsert:
        if isinstance(contact, ContactUpsert):
            return contact
        if isinstance(contact, Contact):
            return contact.to_upsert()
        return ContactUpsert.model_validate(contact)

    @staticmethod
    async def upsert(contact: ContactUpsert | Contact | Mapping[str, Any]) -> None:
        contact_row = ContactRepository._coerce_contact_upsert(contact)
        last_path, last_path_len, out_path_hash_mode = normalize_contact_route(
            contact_row.last_path,
            contact_row.last_path_len,
            contact_row.out_path_hash_mode,
        )
        route_override_path, route_override_len, route_override_hash_mode = (
            normalize_route_override(
                contact_row.route_override_path,
                contact_row.route_override_len,
                contact_row.route_override_hash_mode,
            )
        )

        await db.conn.execute(
            """
            INSERT INTO contacts (public_key, name, type, flags, last_path, last_path_len,
                                  out_path_hash_mode,
                                  route_override_path, route_override_len,
                                  route_override_hash_mode,
                                  last_advert, lat, lon, last_seen,
                                  on_radio, last_contacted, first_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(public_key) DO UPDATE SET
                name = COALESCE(excluded.name, contacts.name),
                type = CASE WHEN excluded.type = 0 THEN contacts.type ELSE excluded.type END,
                flags = excluded.flags,
                last_path = COALESCE(excluded.last_path, contacts.last_path),
                last_path_len = excluded.last_path_len,
                out_path_hash_mode = excluded.out_path_hash_mode,
                route_override_path = COALESCE(
                    excluded.route_override_path, contacts.route_override_path
                ),
                route_override_len = COALESCE(
                    excluded.route_override_len, contacts.route_override_len
                ),
                route_override_hash_mode = COALESCE(
                    excluded.route_override_hash_mode, contacts.route_override_hash_mode
                ),
                last_advert = COALESCE(excluded.last_advert, contacts.last_advert),
                lat = COALESCE(excluded.lat, contacts.lat),
                lon = COALESCE(excluded.lon, contacts.lon),
                last_seen = excluded.last_seen,
                on_radio = COALESCE(excluded.on_radio, contacts.on_radio),
                last_contacted = COALESCE(excluded.last_contacted, contacts.last_contacted),
                first_seen = COALESCE(contacts.first_seen, excluded.first_seen)
            """,
            (
                contact_row.public_key.lower(),
                contact_row.name,
                contact_row.type,
                contact_row.flags,
                last_path,
                last_path_len,
                out_path_hash_mode,
                route_override_path,
                route_override_len,
                route_override_hash_mode,
                contact_row.last_advert,
                contact_row.lat,
                contact_row.lon,
                contact_row.last_seen if contact_row.last_seen is not None else int(time.time()),
                contact_row.on_radio,
                contact_row.last_contacted,
                contact_row.first_seen,
            ),
        )
        await db.conn.commit()

    @staticmethod
    def _row_to_contact(row) -> Contact:
        """Convert a database row to a Contact model."""
        last_path, last_path_len, out_path_hash_mode = normalize_contact_route(
            row["last_path"],
            row["last_path_len"],
            row["out_path_hash_mode"],
        )
        available_columns = set(row.keys())
        route_override_path = (
            row["route_override_path"] if "route_override_path" in available_columns else None
        )
        route_override_len = (
            row["route_override_len"] if "route_override_len" in available_columns else None
        )
        route_override_hash_mode = (
            row["route_override_hash_mode"]
            if "route_override_hash_mode" in available_columns
            else None
        )
        route_override_path, route_override_len, route_override_hash_mode = (
            normalize_route_override(
                route_override_path,
                route_override_len,
                route_override_hash_mode,
            )
        )
        return Contact(
            public_key=row["public_key"],
            name=row["name"],
            type=row["type"],
            flags=row["flags"],
            last_path=last_path,
            last_path_len=last_path_len,
            out_path_hash_mode=out_path_hash_mode,
            route_override_path=route_override_path,
            route_override_len=route_override_len,
            route_override_hash_mode=route_override_hash_mode,
            last_advert=row["last_advert"],
            lat=row["lat"],
            lon=row["lon"],
            last_seen=row["last_seen"],
            on_radio=bool(row["on_radio"]),
            last_contacted=row["last_contacted"],
            last_read_at=row["last_read_at"],
            first_seen=row["first_seen"],
        )

    @staticmethod
    async def get_by_key(public_key: str) -> Contact | None:
        cursor = await db.conn.execute(
            "SELECT * FROM contacts WHERE public_key = ?", (public_key.lower(),)
        )
        row = await cursor.fetchone()
        return ContactRepository._row_to_contact(row) if row else None

    @staticmethod
    async def get_by_key_prefix(prefix: str) -> Contact | None:
        """Get a contact by key prefix only if it resolves uniquely.

        Returns None when no contacts match OR when multiple contacts match
        the prefix (to avoid silently selecting the wrong contact).
        """
        normalized_prefix = prefix.lower()
        exact = await ContactRepository.get_by_key(normalized_prefix)
        if exact:
            return exact
        cursor = await db.conn.execute(
            "SELECT * FROM contacts WHERE public_key LIKE ? ORDER BY public_key LIMIT 2",
            (f"{normalized_prefix}%",),
        )
        rows = list(await cursor.fetchall())
        if len(rows) != 1:
            return None
        return ContactRepository._row_to_contact(rows[0])

    @staticmethod
    async def _get_prefix_matches(prefix: str, limit: int = 2) -> list[Contact]:
        """Get contacts matching a key prefix, up to limit."""
        cursor = await db.conn.execute(
            "SELECT * FROM contacts WHERE public_key LIKE ? ORDER BY public_key LIMIT ?",
            (f"{prefix.lower()}%", limit),
        )
        rows = list(await cursor.fetchall())
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def get_by_key_or_prefix(key_or_prefix: str) -> Contact | None:
        """Get a contact by exact key match, falling back to prefix match.

        Useful when the input might be a full 64-char public key or a shorter prefix.
        """
        contact = await ContactRepository.get_by_key(key_or_prefix)
        if contact:
            return contact

        matches = await ContactRepository._get_prefix_matches(key_or_prefix, limit=2)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousPublicKeyPrefixError(
                key_or_prefix,
                [m.public_key for m in matches],
            )
        return None

    @staticmethod
    async def get_by_name(name: str) -> list[Contact]:
        """Get all contacts with the given exact name."""
        cursor = await db.conn.execute("SELECT * FROM contacts WHERE name = ?", (name,))
        rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def resolve_prefixes(prefixes: list[str]) -> dict[str, Contact]:
        """Resolve multiple key prefixes to contacts in a single query.

        Returns a dict mapping each prefix to its Contact, only for prefixes
        that resolve uniquely (exactly one match). Ambiguous or unmatched
        prefixes are omitted.
        """
        if not prefixes:
            return {}
        normalized = [p.lower() for p in prefixes]
        conditions = " OR ".join(["public_key LIKE ?"] * len(normalized))
        params = [f"{p}%" for p in normalized]
        cursor = await db.conn.execute(f"SELECT * FROM contacts WHERE {conditions}", params)
        rows = await cursor.fetchall()
        # Group by which prefix each row matches
        prefix_to_rows: dict[str, list] = {p: [] for p in normalized}
        for row in rows:
            pk = row["public_key"]
            for p in normalized:
                if pk.startswith(p):
                    prefix_to_rows[p].append(row)
        # Only include uniquely-resolved prefixes
        result: dict[str, Contact] = {}
        for p in normalized:
            if len(prefix_to_rows[p]) == 1:
                result[p] = ContactRepository._row_to_contact(prefix_to_rows[p][0])
        return result

    @staticmethod
    async def get_all(limit: int = 100, offset: int = 0) -> list[Contact]:
        cursor = await db.conn.execute(
            "SELECT * FROM contacts ORDER BY COALESCE(name, public_key) LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def get_recently_contacted_non_repeaters(limit: int = 200) -> list[Contact]:
        """Get recently interacted-with non-repeater contacts."""
        cursor = await db.conn.execute(
            """
            SELECT * FROM contacts
            WHERE type != 2 AND last_contacted IS NOT NULL AND length(public_key) = 64
            ORDER BY last_contacted DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def get_recently_advertised_non_repeaters(limit: int = 200) -> list[Contact]:
        """Get recently advert-heard non-repeater contacts."""
        cursor = await db.conn.execute(
            """
            SELECT * FROM contacts
            WHERE type != 2 AND last_advert IS NOT NULL AND length(public_key) = 64
            ORDER BY last_advert DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def update_path(
        public_key: str,
        path: str,
        path_len: int,
        out_path_hash_mode: int | None = None,
    ) -> None:
        normalized_path, normalized_path_len, normalized_hash_mode = normalize_contact_route(
            path,
            path_len,
            out_path_hash_mode,
        )
        await db.conn.execute(
            """UPDATE contacts SET last_path = ?, last_path_len = ?,
               out_path_hash_mode = COALESCE(?, out_path_hash_mode),
               last_seen = ? WHERE public_key = ?""",
            (
                normalized_path,
                normalized_path_len,
                normalized_hash_mode,
                int(time.time()),
                public_key.lower(),
            ),
        )
        await db.conn.commit()

    @staticmethod
    async def set_routing_override(
        public_key: str,
        path: str | None,
        path_len: int | None,
        out_path_hash_mode: int | None = None,
    ) -> None:
        normalized_path, normalized_len, normalized_hash_mode = normalize_route_override(
            path,
            path_len,
            out_path_hash_mode,
        )
        await db.conn.execute(
            """
            UPDATE contacts
            SET route_override_path = ?, route_override_len = ?, route_override_hash_mode = ?
            WHERE public_key = ?
            """,
            (
                normalized_path,
                normalized_len,
                normalized_hash_mode,
                public_key.lower(),
            ),
        )
        await db.conn.commit()

    @staticmethod
    async def clear_routing_override(public_key: str) -> None:
        await db.conn.execute(
            """
            UPDATE contacts
            SET route_override_path = NULL,
                route_override_len = NULL,
                route_override_hash_mode = NULL
            WHERE public_key = ?
            """,
            (public_key.lower(),),
        )
        await db.conn.commit()

    @staticmethod
    async def clear_on_radio_except(keep_keys: list[str]) -> None:
        """Set on_radio=False for all contacts NOT in keep_keys."""
        if not keep_keys:
            await db.conn.execute("UPDATE contacts SET on_radio = 0 WHERE on_radio = 1")
        else:
            placeholders = ",".join("?" * len(keep_keys))
            await db.conn.execute(
                f"UPDATE contacts SET on_radio = 0 WHERE on_radio = 1 AND public_key NOT IN ({placeholders})",
                keep_keys,
            )
        await db.conn.commit()

    @staticmethod
    async def delete(public_key: str) -> None:
        normalized = public_key.lower()
        await db.conn.execute(
            "DELETE FROM contact_name_history WHERE public_key = ?", (normalized,)
        )
        await db.conn.execute(
            "DELETE FROM contact_advert_paths WHERE public_key = ?", (normalized,)
        )
        await db.conn.execute("DELETE FROM contacts WHERE public_key = ?", (normalized,))
        await db.conn.commit()

    @staticmethod
    async def update_last_contacted(public_key: str, timestamp: int | None = None) -> None:
        """Update the last_contacted timestamp for a contact."""
        ts = timestamp if timestamp is not None else int(time.time())
        await db.conn.execute(
            "UPDATE contacts SET last_contacted = ?, last_seen = ? WHERE public_key = ?",
            (ts, ts, public_key.lower()),
        )
        await db.conn.commit()

    @staticmethod
    async def update_last_read_at(public_key: str, timestamp: int | None = None) -> bool:
        """Update the last_read_at timestamp for a contact.

        Returns True if a row was updated, False if contact not found.
        """
        ts = timestamp if timestamp is not None else int(time.time())
        cursor = await db.conn.execute(
            "UPDATE contacts SET last_read_at = ? WHERE public_key = ?",
            (ts, public_key.lower()),
        )
        await db.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    async def promote_prefix_placeholders(full_key: str) -> list[str]:
        """Promote prefix-only placeholder contacts to a resolved full key.

        Returns the placeholder public keys that were merged into the full key.
        """
        normalized_full_key = full_key.lower()
        cursor = await db.conn.execute(
            """
            SELECT public_key, last_seen, last_contacted, first_seen, last_read_at
            FROM contacts
            WHERE length(public_key) < 64
              AND ? LIKE public_key || '%'
            ORDER BY length(public_key) DESC, public_key
            """,
            (normalized_full_key,),
        )
        rows = list(await cursor.fetchall())
        if not rows:
            return []

        promoted_keys: list[str] = []
        full_exists = await ContactRepository.get_by_key(normalized_full_key) is not None

        for row in rows:
            old_key = row["public_key"]
            if old_key == normalized_full_key:
                continue

            match_cursor = await db.conn.execute(
                """
                SELECT COUNT(*) AS match_count
                FROM contacts
                WHERE length(public_key) = 64
                  AND public_key LIKE ? || '%'
                """,
                (old_key,),
            )
            match_row = await match_cursor.fetchone()
            if (match_row["match_count"] if match_row is not None else 0) != 1:
                continue

            if full_exists:
                await db.conn.execute(
                    """
                    UPDATE contacts
                    SET last_seen = CASE
                            WHEN contacts.last_seen IS NULL THEN ?
                            WHEN ? IS NULL THEN contacts.last_seen
                            WHEN ? > contacts.last_seen THEN ?
                            ELSE contacts.last_seen
                        END,
                        last_contacted = CASE
                            WHEN contacts.last_contacted IS NULL THEN ?
                            WHEN ? IS NULL THEN contacts.last_contacted
                            WHEN ? > contacts.last_contacted THEN ?
                            ELSE contacts.last_contacted
                        END,
                        first_seen = CASE
                            WHEN contacts.first_seen IS NULL THEN ?
                            WHEN ? IS NULL THEN contacts.first_seen
                            WHEN ? < contacts.first_seen THEN ?
                            ELSE contacts.first_seen
                        END,
                        last_read_at = COALESCE(contacts.last_read_at, ?)
                    WHERE public_key = ?
                    """,
                    (
                        row["last_seen"],
                        row["last_seen"],
                        row["last_seen"],
                        row["last_seen"],
                        row["last_contacted"],
                        row["last_contacted"],
                        row["last_contacted"],
                        row["last_contacted"],
                        row["first_seen"],
                        row["first_seen"],
                        row["first_seen"],
                        row["first_seen"],
                        row["last_read_at"],
                        normalized_full_key,
                    ),
                )
                await db.conn.execute("DELETE FROM contacts WHERE public_key = ?", (old_key,))
            else:
                await db.conn.execute(
                    "UPDATE contacts SET public_key = ? WHERE public_key = ?",
                    (normalized_full_key, old_key),
                )
                full_exists = True

            promoted_keys.append(old_key)

        await db.conn.commit()
        return promoted_keys

    @staticmethod
    async def mark_all_read(timestamp: int) -> None:
        """Mark all contacts as read at the given timestamp."""
        await db.conn.execute("UPDATE contacts SET last_read_at = ?", (timestamp,))
        await db.conn.commit()

    @staticmethod
    async def get_by_pubkey_first_byte(hex_byte: str) -> list[Contact]:
        """Get contacts whose public key starts with the given hex byte (2 chars)."""
        cursor = await db.conn.execute(
            "SELECT * FROM contacts WHERE substr(public_key, 1, 2) = ?",
            (hex_byte.lower(),),
        )
        rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]


class ContactAdvertPathRepository:
    """Repository for recent unique advertisement paths per contact."""

    @staticmethod
    def _row_to_path(row) -> ContactAdvertPath:
        path = row["path_hex"] or ""
        path_len = row["path_len"]
        next_hop = first_hop_hex(path, path_len)
        return ContactAdvertPath(
            path=path,
            path_len=path_len,
            next_hop=next_hop,
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            heard_count=row["heard_count"],
        )

    @staticmethod
    async def record_observation(
        public_key: str,
        path_hex: str,
        timestamp: int,
        max_paths: int = 10,
        hop_count: int | None = None,
    ) -> None:
        """
        Upsert a unique advert path observation for a contact and prune to N most recent.
        """
        if max_paths < 1:
            max_paths = 1

        normalized_key = public_key.lower()
        normalized_path = path_hex.lower()
        path_len = hop_count if hop_count is not None else len(normalized_path) // 2

        await db.conn.execute(
            """
            INSERT INTO contact_advert_paths
                (public_key, path_hex, path_len, first_seen, last_seen, heard_count)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(public_key, path_hex, path_len) DO UPDATE SET
                last_seen = MAX(contact_advert_paths.last_seen, excluded.last_seen),
                heard_count = contact_advert_paths.heard_count + 1
            """,
            (normalized_key, normalized_path, path_len, timestamp, timestamp),
        )

        # Keep only the N most recent unique paths per contact.
        await db.conn.execute(
            """
            DELETE FROM contact_advert_paths
            WHERE public_key = ?
              AND id NOT IN (
                  SELECT id
                  FROM contact_advert_paths
                  WHERE public_key = ?
                  ORDER BY last_seen DESC, heard_count DESC, path_len ASC, path_hex ASC
                  LIMIT ?
              )
            """,
            (normalized_key, normalized_key, max_paths),
        )
        await db.conn.commit()

    @staticmethod
    async def get_recent_for_contact(public_key: str, limit: int = 10) -> list[ContactAdvertPath]:
        cursor = await db.conn.execute(
            """
            SELECT path_hex, path_len, first_seen, last_seen, heard_count
            FROM contact_advert_paths
            WHERE public_key = ?
            ORDER BY last_seen DESC, heard_count DESC, path_len ASC, path_hex ASC
            LIMIT ?
            """,
            (public_key.lower(), limit),
        )
        rows = await cursor.fetchall()
        return [ContactAdvertPathRepository._row_to_path(row) for row in rows]

    @staticmethod
    async def get_recent_for_all_contacts(
        limit_per_contact: int = 10,
    ) -> list[ContactAdvertPathSummary]:
        cursor = await db.conn.execute(
            """
            SELECT public_key, path_hex, path_len, first_seen, last_seen, heard_count
            FROM contact_advert_paths
            ORDER BY public_key ASC, last_seen DESC, heard_count DESC, path_len ASC, path_hex ASC
            """
        )
        rows = await cursor.fetchall()

        grouped: dict[str, list[ContactAdvertPath]] = {}
        for row in rows:
            key = row["public_key"]
            paths = grouped.get(key)
            if paths is None:
                paths = []
                grouped[key] = paths
            if len(paths) >= limit_per_contact:
                continue
            paths.append(ContactAdvertPathRepository._row_to_path(row))

        return [
            ContactAdvertPathSummary(public_key=key, paths=paths) for key, paths in grouped.items()
        ]


class ContactNameHistoryRepository:
    """Repository for contact name change history."""

    @staticmethod
    async def record_name(public_key: str, name: str, timestamp: int) -> None:
        """Record a name observation. Upserts: updates last_seen if name already known."""
        await db.conn.execute(
            """
            INSERT INTO contact_name_history (public_key, name, first_seen, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(public_key, name) DO UPDATE SET
                last_seen = MAX(contact_name_history.last_seen, excluded.last_seen)
            """,
            (public_key.lower(), name, timestamp, timestamp),
        )
        await db.conn.commit()

    @staticmethod
    async def get_history(public_key: str) -> list[ContactNameHistory]:
        cursor = await db.conn.execute(
            """
            SELECT name, first_seen, last_seen
            FROM contact_name_history
            WHERE public_key = ?
            ORDER BY last_seen DESC
            """,
            (public_key.lower(),),
        )
        rows = await cursor.fetchall()
        return [
            ContactNameHistory(
                name=row["name"], first_seen=row["first_seen"], last_seen=row["last_seen"]
            )
            for row in rows
        ]
