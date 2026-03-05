"""
Database migrations using SQLite's user_version pragma.

Migrations run automatically on startup. The user_version pragma tracks
which migrations have been applied (defaults to 0 for existing databases).

This approach is safe for existing users - their databases have user_version=0,
so all migrations run in order on first startup after upgrade.
"""

import logging
from hashlib import sha256

import aiosqlite

logger = logging.getLogger(__name__)


async def get_version(conn: aiosqlite.Connection) -> int:
    """Get current schema version from SQLite user_version pragma."""
    cursor = await conn.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def set_version(conn: aiosqlite.Connection, version: int) -> None:
    """Set schema version using SQLite user_version pragma."""
    await conn.execute(f"PRAGMA user_version = {version}")


async def run_migrations(conn: aiosqlite.Connection) -> int:
    """
    Run all pending migrations.

    Returns the number of migrations applied.
    """
    version = await get_version(conn)
    applied = 0

    # Migration 1: Add last_read_at columns for server-side read tracking
    if version < 1:
        logger.info("Applying migration 1: add last_read_at columns")
        await _migrate_001_add_last_read_at(conn)
        await set_version(conn, 1)
        applied += 1

    # Migration 2: Drop unused decrypt_attempts and last_attempt columns
    if version < 2:
        logger.info("Applying migration 2: drop decrypt_attempts and last_attempt columns")
        await _migrate_002_drop_decrypt_attempt_columns(conn)
        await set_version(conn, 2)
        applied += 1

    # Migration 3: Drop decrypted column (redundant with message_id), update index
    if version < 3:
        logger.info("Applying migration 3: drop decrypted column, add message_id index")
        await _migrate_003_drop_decrypted_column(conn)
        await set_version(conn, 3)
        applied += 1

    # Migration 4: Add payload_hash column for deduplication
    if version < 4:
        logger.info("Applying migration 4: add payload_hash column")
        await _migrate_004_add_payload_hash_column(conn)
        await set_version(conn, 4)
        applied += 1

    # Migration 5: Backfill payload hashes and deduplicate existing packets
    if version < 5:
        logger.info("Applying migration 5: backfill payload hashes and dedupe")
        await _migrate_005_backfill_payload_hashes(conn)
        await set_version(conn, 5)
        applied += 1

    # Migration 6: Replace path_len with path column in messages
    if version < 6:
        logger.info("Applying migration 6: replace path_len with path column")
        await _migrate_006_replace_path_len_with_path(conn)
        await set_version(conn, 6)
        applied += 1

    # Migration 7: Backfill path from raw_packets for decrypted messages
    if version < 7:
        logger.info("Applying migration 7: backfill path from raw_packets")
        await _migrate_007_backfill_message_paths(conn)
        await set_version(conn, 7)
        applied += 1

    # Migration 8: Convert path column to paths JSON array for multiple delivery paths
    if version < 8:
        logger.info("Applying migration 8: convert path to paths JSON array")
        await _migrate_008_convert_path_to_paths_array(conn)
        await set_version(conn, 8)
        applied += 1

    # Migration 9: Create app_settings table for persistent preferences
    if version < 9:
        logger.info("Applying migration 9: create app_settings table")
        await _migrate_009_create_app_settings_table(conn)
        await set_version(conn, 9)
        applied += 1

    # Migration 10: Add advert_interval column to app_settings
    if version < 10:
        logger.info("Applying migration 10: add advert_interval column")
        await _migrate_010_add_advert_interval(conn)
        await set_version(conn, 10)
        applied += 1

    # Migration 11: Add last_advert_time column to app_settings
    if version < 11:
        logger.info("Applying migration 11: add last_advert_time column")
        await _migrate_011_add_last_advert_time(conn)
        await set_version(conn, 11)
        applied += 1

    # Migration 12: Add bot_enabled and bot_code columns to app_settings
    if version < 12:
        logger.info("Applying migration 12: add bot settings columns")
        await _migrate_012_add_bot_settings(conn)
        await set_version(conn, 12)
        applied += 1

    # Migration 13: Convert bot_enabled/bot_code to bots JSON array
    if version < 13:
        logger.info("Applying migration 13: convert to multi-bot format")
        await _migrate_013_convert_to_multi_bot(conn)
        await set_version(conn, 13)
        applied += 1

    # Migration 14: Lowercase all contact public keys and related data
    if version < 14:
        logger.info("Applying migration 14: lowercase all contact public keys")
        await _migrate_014_lowercase_public_keys(conn)
        await set_version(conn, 14)
        applied += 1

    # Migration 15: Fix NULL sender_timestamp and add null-safe dedup index
    if version < 15:
        logger.info("Applying migration 15: fix NULL sender_timestamp values")
        await _migrate_015_fix_null_sender_timestamp(conn)
        await set_version(conn, 15)
        applied += 1

    # Migration 16: Add experimental_channel_double_send setting
    if version < 16:
        logger.info("Applying migration 16: add experimental_channel_double_send column")
        await _migrate_016_add_experimental_channel_double_send(conn)
        await set_version(conn, 16)
        applied += 1

    # Migration 17: Drop experimental_channel_double_send column (replaced by user-triggered resend)
    if version < 17:
        logger.info("Applying migration 17: drop experimental_channel_double_send column")
        await _migrate_017_drop_experimental_channel_double_send(conn)
        await set_version(conn, 17)
        applied += 1

    # Migration 18: Drop UNIQUE(data) constraint on raw_packets (redundant with payload_hash)
    if version < 18:
        logger.info("Applying migration 18: drop raw_packets UNIQUE(data) constraint")
        await _migrate_018_drop_raw_packets_data_unique(conn)
        await set_version(conn, 18)
        applied += 1

    # Migration 19: Drop UNIQUE constraint on messages (redundant with dedup_null_safe index)
    if version < 19:
        logger.info("Applying migration 19: drop messages UNIQUE constraint")
        await _migrate_019_drop_messages_unique_constraint(conn)
        await set_version(conn, 19)
        applied += 1

    # Migration 20: Enable WAL journal mode and incremental auto-vacuum
    if version < 20:
        logger.info("Applying migration 20: enable WAL mode and incremental auto-vacuum")
        await _migrate_020_enable_wal_and_auto_vacuum(conn)
        await set_version(conn, 20)
        applied += 1

    # Migration 21: Enforce minimum 1-hour advert interval
    if version < 21:
        logger.info("Applying migration 21: enforce minimum 1-hour advert interval")
        await _migrate_021_enforce_min_advert_interval(conn)
        await set_version(conn, 21)
        applied += 1

    # Migration 22: Track recent unique advert paths per repeater
    if version < 22:
        logger.info("Applying migration 22: add repeater_advert_paths table")
        await _migrate_022_add_repeater_advert_paths(conn)
        await set_version(conn, 22)
        applied += 1

    # Migration 23: Add first_seen column to contacts
    if version < 23:
        logger.info("Applying migration 23: add first_seen column to contacts")
        await _migrate_023_add_first_seen(conn)
        await set_version(conn, 23)
        applied += 1

    # Migration 24: Create contact_name_history table
    if version < 24:
        logger.info("Applying migration 24: create contact_name_history table")
        await _migrate_024_create_contact_name_history(conn)
        await set_version(conn, 24)
        applied += 1

    # Migration 25: Add sender_name and sender_key to messages
    if version < 25:
        logger.info("Applying migration 25: add sender_name and sender_key to messages")
        await _migrate_025_add_sender_columns(conn)
        await set_version(conn, 25)
        applied += 1

    # Migration 26: Rename repeater_advert_paths to contact_advert_paths
    if version < 26:
        logger.info("Applying migration 26: rename repeater_advert_paths to contact_advert_paths")
        await _migrate_026_rename_advert_paths_table(conn)
        await set_version(conn, 26)
        applied += 1

    # Migration 27: Backfill first_seen from advert paths
    if version < 27:
        logger.info("Applying migration 27: backfill first_seen from advert paths")
        await _migrate_027_backfill_first_seen_from_advert_paths(conn)
        await set_version(conn, 27)
        applied += 1

    # Migration 28: Convert payload_hash from 64-char hex TEXT to 32-byte BLOB
    if version < 28:
        logger.info("Applying migration 28: convert payload_hash from TEXT to BLOB")
        await _migrate_028_payload_hash_text_to_blob(conn)
        await set_version(conn, 28)
        applied += 1

    # Migration 29: Add covering index for unread counts query
    if version < 29:
        logger.info("Applying migration 29: add covering index for unread counts query")
        await _migrate_029_add_unread_covering_index(conn)
        await set_version(conn, 29)
        applied += 1

    # Migration 30: Add pagination index, drop redundant idx_messages_conversation
    if version < 30:
        logger.info("Applying migration 30: add pagination index for message queries")
        await _migrate_030_add_pagination_index(conn)
        await set_version(conn, 30)
        applied += 1

    # Migration 31: Add MQTT configuration columns to app_settings
    if version < 31:
        logger.info("Applying migration 31: add MQTT columns to app_settings")
        await _migrate_031_add_mqtt_columns(conn)
        await set_version(conn, 31)
        applied += 1

    # Migration 32: Add community MQTT columns to app_settings
    if version < 32:
        logger.info("Applying migration 32: add community MQTT columns to app_settings")
        await _migrate_032_add_community_mqtt_columns(conn)
        await set_version(conn, 32)
        applied += 1

    # Migration 33: Seed #remoteterm channel on initial install
    if version < 33:
        logger.info("Applying migration 33: seed #remoteterm channel")
        await _migrate_033_seed_remoteterm_channel(conn)
        await set_version(conn, 33)
        applied += 1

    # Migration 34: Add flood_scope column to app_settings
    if version < 34:
        logger.info("Applying migration 34: add flood_scope column to app_settings")
        await _migrate_034_add_flood_scope(conn)
        await set_version(conn, 34)
        applied += 1

    # Migration 35: Add blocked_keys and blocked_names columns to app_settings
    if version < 35:
        logger.info("Applying migration 35: add blocked_keys and blocked_names to app_settings")
        await _migrate_035_add_block_lists(conn)
        await set_version(conn, 35)
        applied += 1

    # Migration 36: Add Apprise external notification settings
    if version < 36:
        logger.info("Applying migration 36: add apprise notification settings")
        await _migrate_036_add_apprise_settings(conn)
        await set_version(conn, 36)
        applied += 1

    # Migration 37: Add apprise_preserve_identity setting
    if version < 37:
        logger.info("Applying migration 37: add apprise preserve identity setting")
        await _migrate_037_add_apprise_preserve_identity(conn)
        await set_version(conn, 37)
        applied += 1

    # Migration 38: Repair missing app_settings columns from historical version drift
    if version < 38:
        logger.info("Applying migration 38: repair missing app_settings columns")
        await _migrate_038_repair_app_settings_columns(conn)
        await set_version(conn, 38)
        applied += 1

    if applied > 0:
        logger.info(
            "Applied %d migration(s), schema now at version %d", applied, await get_version(conn)
        )
    else:
        logger.debug("Schema up to date at version %d", version)

    return applied


async def _migrate_001_add_last_read_at(conn: aiosqlite.Connection) -> None:
    """
    Add last_read_at column to contacts and channels tables.

    This enables server-side read state tracking, replacing the localStorage
    approach for consistent read state across devices.

    ALTER TABLE ADD COLUMN is safe - it preserves existing data and handles
    the "column already exists" case gracefully.
    """
    # Add to contacts table
    try:
        await conn.execute("ALTER TABLE contacts ADD COLUMN last_read_at INTEGER")
        logger.debug("Added last_read_at to contacts table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("contacts.last_read_at already exists, skipping")
        else:
            raise

    # Add to channels table
    try:
        await conn.execute("ALTER TABLE channels ADD COLUMN last_read_at INTEGER")
        logger.debug("Added last_read_at to channels table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("channels.last_read_at already exists, skipping")
        else:
            raise

    await conn.commit()


async def _migrate_002_drop_decrypt_attempt_columns(conn: aiosqlite.Connection) -> None:
    """
    Drop unused decrypt_attempts and last_attempt columns from raw_packets.

    These columns were added for a retry-limiting feature that was never implemented.
    They are written to but never read, so we can safely remove them.

    SQLite 3.35.0+ supports ALTER TABLE DROP COLUMN. For older versions,
    we silently skip (the columns will remain but are harmless).
    """
    for column in ["decrypt_attempts", "last_attempt"]:
        try:
            await conn.execute(f"ALTER TABLE raw_packets DROP COLUMN {column}")
            logger.debug("Dropped %s from raw_packets table", column)
        except aiosqlite.OperationalError as e:
            error_msg = str(e).lower()
            if "no such column" in error_msg:
                logger.debug("raw_packets.%s already dropped, skipping", column)
            elif "syntax error" in error_msg or "drop column" in error_msg:
                # SQLite version doesn't support DROP COLUMN - harmless, column stays
                logger.debug("SQLite doesn't support DROP COLUMN, %s column will remain", column)
            else:
                raise

    await conn.commit()


async def _migrate_003_drop_decrypted_column(conn: aiosqlite.Connection) -> None:
    """
    Drop the decrypted column and update indexes.

    The decrypted column is redundant with message_id - a packet is decrypted
    iff message_id IS NOT NULL. We replace the decrypted index with a message_id index.

    SQLite 3.35.0+ supports ALTER TABLE DROP COLUMN. For older versions,
    we silently skip the column drop but still update the index.
    """
    # First, drop the old index on decrypted (safe even if it doesn't exist)
    try:
        await conn.execute("DROP INDEX IF EXISTS idx_raw_packets_decrypted")
        logger.debug("Dropped idx_raw_packets_decrypted index")
    except aiosqlite.OperationalError:
        pass  # Index didn't exist

    # Create new index on message_id for efficient undecrypted packet queries
    try:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_packets_message_id ON raw_packets(message_id)"
        )
        logger.debug("Created idx_raw_packets_message_id index")
    except aiosqlite.OperationalError as e:
        if "already exists" not in str(e).lower():
            raise

    # Try to drop the decrypted column
    try:
        await conn.execute("ALTER TABLE raw_packets DROP COLUMN decrypted")
        logger.debug("Dropped decrypted from raw_packets table")
    except aiosqlite.OperationalError as e:
        error_msg = str(e).lower()
        if "no such column" in error_msg:
            logger.debug("raw_packets.decrypted already dropped, skipping")
        elif "syntax error" in error_msg or "drop column" in error_msg:
            # SQLite version doesn't support DROP COLUMN - harmless, column stays
            logger.debug("SQLite doesn't support DROP COLUMN, decrypted column will remain")
        else:
            raise

    await conn.commit()


async def _migrate_004_add_payload_hash_column(conn: aiosqlite.Connection) -> None:
    """
    Add payload_hash column to raw_packets for deduplication.

    This column stores the SHA-256 hash of the packet payload (excluding routing/path info).
    It will be used with a unique index to prevent duplicate packets from being stored.
    """
    try:
        await conn.execute("ALTER TABLE raw_packets ADD COLUMN payload_hash TEXT")
        logger.debug("Added payload_hash column to raw_packets table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("raw_packets.payload_hash already exists, skipping")
        else:
            raise

    await conn.commit()


def _extract_payload_for_hash(raw_packet: bytes) -> bytes | None:
    """
    Extract payload from a raw packet for hashing (migration-local copy of decoder logic).

    Returns the payload bytes, or None if packet is malformed.
    """
    if len(raw_packet) < 2:
        return None

    try:
        header = raw_packet[0]
        route_type = header & 0x03
        offset = 1

        # Skip transport codes if present (TRANSPORT_FLOOD=0, TRANSPORT_DIRECT=3)
        if route_type in (0x00, 0x03):
            if len(raw_packet) < offset + 4:
                return None
            offset += 4

        # Get path length
        if len(raw_packet) < offset + 1:
            return None
        path_length = raw_packet[offset]
        offset += 1

        # Skip path bytes
        if len(raw_packet) < offset + path_length:
            return None
        offset += path_length

        # Rest is payload (may be empty, matching decoder.py behavior)
        return raw_packet[offset:]
    except (IndexError, ValueError):
        return None


async def _migrate_005_backfill_payload_hashes(conn: aiosqlite.Connection) -> None:
    """
    Backfill payload_hash for existing packets and remove duplicates.

    This may take a while for large databases. Progress is logged.
    After backfilling, a unique index is created to prevent future duplicates.
    """
    # Get count first
    cursor = await conn.execute("SELECT COUNT(*) FROM raw_packets WHERE payload_hash IS NULL")
    row = await cursor.fetchone()
    total = row[0] if row else 0

    if total == 0:
        logger.debug("No packets need hash backfill")
    else:
        logger.info("Backfilling payload hashes for %d packets. This may take a while...", total)

        # Process in batches to avoid memory issues
        batch_size = 1000
        processed = 0
        duplicates_deleted = 0

        # Track seen hashes to identify duplicates (keep oldest = lowest ID)
        seen_hashes: dict[str, int] = {}  # hash -> oldest packet ID

        # First pass: compute hashes and identify duplicates
        cursor = await conn.execute("SELECT id, data FROM raw_packets ORDER BY id ASC")

        packets_to_update: list[tuple[str, int]] = []  # (hash, id)
        ids_to_delete: list[int] = []

        while True:
            rows = await cursor.fetchmany(batch_size)
            if not rows:
                break

            for row in rows:
                packet_id = row[0]
                packet_data = bytes(row[1])

                # Extract payload and compute hash
                payload = _extract_payload_for_hash(packet_data)
                if payload:
                    payload_hash = sha256(payload).hexdigest()
                else:
                    # For malformed packets, hash the full data
                    payload_hash = sha256(packet_data).hexdigest()

                if payload_hash in seen_hashes:
                    # Duplicate - mark for deletion (we keep the older one)
                    ids_to_delete.append(packet_id)
                    duplicates_deleted += 1
                else:
                    # New hash - keep this packet
                    seen_hashes[payload_hash] = packet_id
                    packets_to_update.append((payload_hash, packet_id))

                processed += 1

            if processed % 10000 == 0:
                logger.info("Processed %d/%d packets...", processed, total)

        # Second pass: update hashes for packets we're keeping
        total_updates = len(packets_to_update)
        logger.info("Updating %d packets with hashes...", total_updates)
        for idx, (payload_hash, packet_id) in enumerate(packets_to_update, 1):
            await conn.execute(
                "UPDATE raw_packets SET payload_hash = ? WHERE id = ?",
                (payload_hash, packet_id),
            )
            if idx % 10000 == 0:
                logger.info("Updated %d/%d packets...", idx, total_updates)

        # Third pass: delete duplicates
        if ids_to_delete:
            total_deletes = len(ids_to_delete)
            logger.info("Removing %d duplicate packets...", total_deletes)
            deleted_count = 0
            # Delete in batches to avoid "too many SQL variables" error
            for i in range(0, len(ids_to_delete), 500):
                batch = ids_to_delete[i : i + 500]
                placeholders = ",".join("?" * len(batch))
                await conn.execute(f"DELETE FROM raw_packets WHERE id IN ({placeholders})", batch)
                deleted_count += len(batch)
                if deleted_count % 10000 < 500:  # Log roughly every 10k
                    logger.info("Removed %d/%d duplicates...", deleted_count, total_deletes)

        await conn.commit()
        logger.info(
            "Hash backfill complete: %d packets updated, %d duplicates removed",
            len(packets_to_update),
            duplicates_deleted,
        )

    # Create unique index on payload_hash (this enforces uniqueness going forward)
    try:
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_packets_payload_hash "
            "ON raw_packets(payload_hash)"
        )
        logger.debug("Created unique index on payload_hash")
    except aiosqlite.OperationalError as e:
        if "already exists" not in str(e).lower():
            raise

    await conn.commit()


async def _migrate_006_replace_path_len_with_path(conn: aiosqlite.Connection) -> None:
    """
    Replace path_len INTEGER column with path TEXT column in messages table.

    The path column stores the hex-encoded routing path bytes. Path length can
    be derived from the hex string (2 chars per byte = 1 hop).

    SQLite 3.35.0+ supports ALTER TABLE DROP COLUMN. For older versions,
    we silently skip the drop (the column will remain but is unused).
    """
    # First, add the new path column
    try:
        await conn.execute("ALTER TABLE messages ADD COLUMN path TEXT")
        logger.debug("Added path column to messages table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("messages.path already exists, skipping")
        else:
            raise

    # Try to drop the old path_len column
    try:
        await conn.execute("ALTER TABLE messages DROP COLUMN path_len")
        logger.debug("Dropped path_len from messages table")
    except aiosqlite.OperationalError as e:
        error_msg = str(e).lower()
        if "no such column" in error_msg:
            logger.debug("messages.path_len already dropped, skipping")
        elif "syntax error" in error_msg or "drop column" in error_msg:
            # SQLite version doesn't support DROP COLUMN - harmless, column stays
            logger.debug("SQLite doesn't support DROP COLUMN, path_len column will remain")
        else:
            raise

    await conn.commit()


def _extract_path_from_packet(raw_packet: bytes) -> str | None:
    """
    Extract path hex string from a raw packet (migration-local copy of decoder logic).

    Returns the path as a hex string, or None if packet is malformed.
    """
    if len(raw_packet) < 2:
        return None

    try:
        header = raw_packet[0]
        route_type = header & 0x03
        offset = 1

        # Skip transport codes if present (TRANSPORT_FLOOD=0, TRANSPORT_DIRECT=3)
        if route_type in (0x00, 0x03):
            if len(raw_packet) < offset + 4:
                return None
            offset += 4

        # Get path length
        if len(raw_packet) < offset + 1:
            return None
        path_length = raw_packet[offset]
        offset += 1

        # Extract path bytes
        if len(raw_packet) < offset + path_length:
            return None
        path_bytes = raw_packet[offset : offset + path_length]

        return path_bytes.hex()
    except (IndexError, ValueError):
        return None


async def _migrate_007_backfill_message_paths(conn: aiosqlite.Connection) -> None:
    """
    Backfill path column for messages that have linked raw_packets.

    For each message with a linked raw_packet (via message_id), extract the
    path from the raw packet and update the message.

    Only updates incoming messages (outgoing=0) since outgoing messages
    don't have meaningful path data.
    """
    # Get count of messages that need backfill
    cursor = await conn.execute(
        """
        SELECT COUNT(*)
        FROM messages m
        JOIN raw_packets rp ON rp.message_id = m.id
        WHERE m.path IS NULL AND m.outgoing = 0
        """
    )
    row = await cursor.fetchone()
    total = row[0] if row else 0

    if total == 0:
        logger.debug("No messages need path backfill")
        return

    logger.info("Backfilling path for %d messages. This may take a while...", total)

    # Process in batches
    batch_size = 1000
    processed = 0
    updated = 0

    cursor = await conn.execute(
        """
        SELECT m.id, rp.data
        FROM messages m
        JOIN raw_packets rp ON rp.message_id = m.id
        WHERE m.path IS NULL AND m.outgoing = 0
        ORDER BY m.id ASC
        """
    )

    updates: list[tuple[str, int]] = []  # (path, message_id)

    while True:
        rows = await cursor.fetchmany(batch_size)
        if not rows:
            break

        for row in rows:
            message_id = row[0]
            packet_data = bytes(row[1])

            path_hex = _extract_path_from_packet(packet_data)
            if path_hex is not None:
                updates.append((path_hex, message_id))

            processed += 1

        if processed % 10000 == 0:
            logger.info("Processed %d/%d messages...", processed, total)

    # Apply updates in batches
    if updates:
        logger.info("Updating %d messages with path data...", len(updates))
        for idx, (path_hex, message_id) in enumerate(updates, 1):
            await conn.execute(
                "UPDATE messages SET path = ? WHERE id = ?",
                (path_hex, message_id),
            )
            updated += 1
            if idx % 10000 == 0:
                logger.info("Updated %d/%d messages...", idx, len(updates))

    await conn.commit()
    logger.info("Path backfill complete: %d messages updated", updated)


async def _migrate_008_convert_path_to_paths_array(conn: aiosqlite.Connection) -> None:
    """
    Convert path TEXT column to paths TEXT column storing JSON array.

    The new format stores multiple paths as a JSON array of objects:
    [{"path": "1A2B", "received_at": 1234567890}, ...]

    This enables tracking multiple delivery paths for the same message
    (e.g., when a message is received via different repeater routes).
    """
    import json

    # First, add the new paths column
    try:
        await conn.execute("ALTER TABLE messages ADD COLUMN paths TEXT")
        logger.debug("Added paths column to messages table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("messages.paths already exists, skipping column add")
        else:
            raise

    # Migrate existing path data to paths array format
    cursor = await conn.execute(
        "SELECT id, path, received_at FROM messages WHERE path IS NOT NULL AND paths IS NULL"
    )
    rows = list(await cursor.fetchall())

    if rows:
        logger.info("Converting %d messages from path to paths array format...", len(rows))
        for row in rows:
            message_id = row[0]
            old_path = row[1]
            received_at = row[2]

            # Convert single path to array format
            paths_json = json.dumps([{"path": old_path, "received_at": received_at}])
            await conn.execute(
                "UPDATE messages SET paths = ? WHERE id = ?",
                (paths_json, message_id),
            )

        logger.info("Converted %d messages to paths array format", len(rows))

    # Try to drop the old path column (SQLite 3.35.0+ only)
    try:
        await conn.execute("ALTER TABLE messages DROP COLUMN path")
        logger.debug("Dropped path column from messages table")
    except aiosqlite.OperationalError as e:
        error_msg = str(e).lower()
        if "no such column" in error_msg:
            logger.debug("messages.path already dropped, skipping")
        elif "syntax error" in error_msg or "drop column" in error_msg:
            # SQLite version doesn't support DROP COLUMN - harmless, column stays
            logger.debug("SQLite doesn't support DROP COLUMN, path column will remain")
        else:
            raise

    await conn.commit()


async def _migrate_009_create_app_settings_table(conn: aiosqlite.Connection) -> None:
    """
    Create app_settings table for persistent application preferences.

    This table stores:
    - max_radio_contacts: Max non-repeater contacts to keep on radio for DM ACKs
    - favorites: JSON array of favorite conversations [{type, id}, ...]
    - auto_decrypt_dm_on_advert: Whether to attempt historical DM decryption on new contact
    - sidebar_sort_order: 'recent' or 'alpha' for sidebar sorting
    - last_message_times: JSON object mapping conversation keys to timestamps
    - preferences_migrated: Flag to track if localStorage has been migrated

    The table uses a single-row pattern (id=1) for simplicity.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            max_radio_contacts INTEGER DEFAULT 200,
            favorites TEXT DEFAULT '[]',
            auto_decrypt_dm_on_advert INTEGER DEFAULT 0,
            sidebar_sort_order TEXT DEFAULT 'recent',
            last_message_times TEXT DEFAULT '{}',
            preferences_migrated INTEGER DEFAULT 0
        )
        """
    )

    # Initialize with default row
    await conn.execute(
        """
        INSERT OR IGNORE INTO app_settings (id, max_radio_contacts, favorites, auto_decrypt_dm_on_advert, sidebar_sort_order, last_message_times, preferences_migrated)
        VALUES (1, 200, '[]', 0, 'recent', '{}', 0)
        """
    )

    await conn.commit()
    logger.debug("Created app_settings table with default values")


async def _migrate_010_add_advert_interval(conn: aiosqlite.Connection) -> None:
    """
    Add advert_interval column to app_settings table.

    This enables configurable periodic advertisement interval (default 0 = disabled).
    """
    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN advert_interval INTEGER DEFAULT 0")
        logger.debug("Added advert_interval column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.debug("advert_interval column already exists, skipping")
        else:
            raise

    await conn.commit()


async def _migrate_011_add_last_advert_time(conn: aiosqlite.Connection) -> None:
    """
    Add last_advert_time column to app_settings table.

    This tracks when the last advertisement was sent, ensuring we never
    advertise faster than the configured advert_interval.
    """
    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN last_advert_time INTEGER DEFAULT 0")
        logger.debug("Added last_advert_time column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.debug("last_advert_time column already exists, skipping")
        else:
            raise

    await conn.commit()


async def _migrate_012_add_bot_settings(conn: aiosqlite.Connection) -> None:
    """
    Add bot_enabled and bot_code columns to app_settings table.

    This enables user-defined Python code to be executed when messages are received,
    allowing for custom bot responses.
    """
    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN bot_enabled INTEGER DEFAULT 0")
        logger.debug("Added bot_enabled column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.debug("bot_enabled column already exists, skipping")
        else:
            raise

    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN bot_code TEXT DEFAULT ''")
        logger.debug("Added bot_code column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.debug("bot_code column already exists, skipping")
        else:
            raise

    await conn.commit()


async def _migrate_013_convert_to_multi_bot(conn: aiosqlite.Connection) -> None:
    """
    Convert single bot_enabled/bot_code to multi-bot format.

    Adds a 'bots' TEXT column storing a JSON array of bot configs:
    [{"id": "uuid", "name": "Bot 1", "enabled": true, "code": "..."}]

    If existing bot_code is non-empty OR bot_enabled is true, migrates
    to a single bot named "Bot 1". Otherwise, creates empty array.

    Attempts to drop the old bot_enabled and bot_code columns.
    """
    import json
    import uuid

    # Add new bots column
    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN bots TEXT DEFAULT '[]'")
        logger.debug("Added bots column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.debug("bots column already exists, skipping")
        else:
            raise

    # Migrate existing bot data
    cursor = await conn.execute("SELECT bot_enabled, bot_code FROM app_settings WHERE id = 1")
    row = await cursor.fetchone()

    if row:
        bot_enabled = bool(row[0]) if row[0] is not None else False
        bot_code = row[1] or ""

        # If there's existing bot data, migrate it
        if bot_code.strip() or bot_enabled:
            bots = [
                {
                    "id": str(uuid.uuid4()),
                    "name": "Bot 1",
                    "enabled": bot_enabled,
                    "code": bot_code,
                }
            ]
            bots_json = json.dumps(bots)
            logger.info("Migrating existing bot to multi-bot format: enabled=%s", bot_enabled)
        else:
            bots_json = "[]"

        await conn.execute(
            "UPDATE app_settings SET bots = ? WHERE id = 1",
            (bots_json,),
        )

    # Try to drop old columns (SQLite 3.35.0+ only)
    for column in ["bot_enabled", "bot_code"]:
        try:
            await conn.execute(f"ALTER TABLE app_settings DROP COLUMN {column}")
            logger.debug("Dropped %s column from app_settings", column)
        except aiosqlite.OperationalError as e:
            error_msg = str(e).lower()
            if "no such column" in error_msg:
                logger.debug("app_settings.%s already dropped, skipping", column)
            elif "syntax error" in error_msg or "drop column" in error_msg:
                # SQLite version doesn't support DROP COLUMN - harmless, column stays
                logger.debug("SQLite doesn't support DROP COLUMN, %s column will remain", column)
            else:
                raise

    await conn.commit()


async def _migrate_014_lowercase_public_keys(conn: aiosqlite.Connection) -> None:
    """
    Lowercase all contact public keys and related data for case-insensitive matching.

    Updates:
    - contacts.public_key (PRIMARY KEY) via temp table swap
    - messages.conversation_key for PRIV messages
    - app_settings.favorites (contact IDs)
    - app_settings.last_message_times (contact- prefixed keys)

    Handles case collisions by keeping the most-recently-seen contact.
    """
    import json

    # 1. Lowercase message conversation keys for private messages
    try:
        await conn.execute(
            "UPDATE messages SET conversation_key = lower(conversation_key) WHERE type = 'PRIV'"
        )
        logger.debug("Lowercased PRIV message conversation_keys")
    except aiosqlite.OperationalError as e:
        if "no such table" in str(e).lower():
            logger.debug("messages table does not exist yet, skipping conversation_key lowercase")
        else:
            raise

    # 2. Check if contacts table exists before proceeding
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
    )
    if not await cursor.fetchone():
        logger.debug("contacts table does not exist yet, skipping key lowercase")
        await conn.commit()
        return

    # 3. Handle contacts table - check for case collisions first
    cursor = await conn.execute(
        "SELECT lower(public_key) as lk, COUNT(*) as cnt "
        "FROM contacts GROUP BY lower(public_key) HAVING COUNT(*) > 1"
    )
    collisions = list(await cursor.fetchall())

    if collisions:
        logger.warning(
            "Found %d case-colliding contact groups, keeping most-recently-seen",
            len(collisions),
        )
        for row in collisions:
            lower_key = row[0]
            # Delete all but the most recently seen
            await conn.execute(
                """DELETE FROM contacts WHERE public_key IN (
                    SELECT public_key FROM contacts
                    WHERE lower(public_key) = ?
                    ORDER BY COALESCE(last_seen, 0) DESC
                    LIMIT -1 OFFSET 1
                )""",
                (lower_key,),
            )

    # 3. Rebuild contacts with lowercased keys
    # Get the actual column names from the table (handles different schema versions)
    cursor = await conn.execute("PRAGMA table_info(contacts)")
    columns_info = await cursor.fetchall()
    all_columns = [col[1] for col in columns_info]  # col[1] is column name

    # Build column lists, lowering public_key
    select_cols = ", ".join(f"lower({c})" if c == "public_key" else c for c in all_columns)
    col_defs = []
    for col in columns_info:
        name, col_type, _notnull, default, pk = col[1], col[2], col[3], col[4], col[5]
        parts = [name, col_type or "TEXT"]
        if pk:
            parts.append("PRIMARY KEY")
        if default is not None:
            parts.append(f"DEFAULT {default}")
        col_defs.append(" ".join(parts))

    create_sql = f"CREATE TABLE contacts_new ({', '.join(col_defs)})"
    await conn.execute(create_sql)
    await conn.execute(f"INSERT INTO contacts_new SELECT {select_cols} FROM contacts")
    await conn.execute("DROP TABLE contacts")
    await conn.execute("ALTER TABLE contacts_new RENAME TO contacts")

    # Recreate the on_radio index (if column exists)
    if "on_radio" in all_columns:
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_on_radio ON contacts(on_radio)")

    # 4. Lowercase contact IDs in favorites JSON (if app_settings exists)
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
    )
    if not await cursor.fetchone():
        await conn.commit()
        logger.info("Lowercased all contact public keys (no app_settings table)")
        return

    cursor = await conn.execute("SELECT favorites FROM app_settings WHERE id = 1")
    row = await cursor.fetchone()
    if row and row[0]:
        try:
            favorites = json.loads(row[0])
            updated = False
            for fav in favorites:
                if fav.get("type") == "contact" and fav.get("id"):
                    new_id = fav["id"].lower()
                    if new_id != fav["id"]:
                        fav["id"] = new_id
                        updated = True
            if updated:
                await conn.execute(
                    "UPDATE app_settings SET favorites = ? WHERE id = 1",
                    (json.dumps(favorites),),
                )
                logger.debug("Lowercased contact IDs in favorites")
        except (json.JSONDecodeError, TypeError):
            pass

    # 5. Lowercase contact keys in last_message_times JSON
    cursor = await conn.execute("SELECT last_message_times FROM app_settings WHERE id = 1")
    row = await cursor.fetchone()
    if row and row[0]:
        try:
            times = json.loads(row[0])
            new_times = {}
            updated = False
            for key, val in times.items():
                if key.startswith("contact-"):
                    new_key = "contact-" + key[8:].lower()
                    if new_key != key:
                        updated = True
                    new_times[new_key] = val
                else:
                    new_times[key] = val
            if updated:
                await conn.execute(
                    "UPDATE app_settings SET last_message_times = ? WHERE id = 1",
                    (json.dumps(new_times),),
                )
                logger.debug("Lowercased contact keys in last_message_times")
        except (json.JSONDecodeError, TypeError):
            pass

    await conn.commit()
    logger.info("Lowercased all contact public keys")


async def _migrate_015_fix_null_sender_timestamp(conn: aiosqlite.Connection) -> None:
    """
    Fix NULL sender_timestamp values and add null-safe dedup index.

    1. Set sender_timestamp = received_at for any messages with NULL sender_timestamp
    2. Create a null-safe unique index as belt-and-suspenders protection
    """
    # Check if messages table exists
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    if not await cursor.fetchone():
        logger.debug("messages table does not exist yet, skipping NULL sender_timestamp fix")
        await conn.commit()
        return

    # Backfill NULL sender_timestamps with received_at
    cursor = await conn.execute(
        "UPDATE messages SET sender_timestamp = received_at WHERE sender_timestamp IS NULL"
    )
    if cursor.rowcount > 0:
        logger.info("Backfilled %d messages with NULL sender_timestamp", cursor.rowcount)

    # Try to create null-safe dedup index (may fail if existing duplicates exist)
    try:
        await conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_dedup_null_safe
               ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))"""
        )
        logger.debug("Created null-safe dedup index")
    except aiosqlite.IntegrityError:
        logger.warning(
            "Could not create null-safe dedup index due to existing duplicates - "
            "the application-level dedup will handle these"
        )

    await conn.commit()


async def _migrate_016_add_experimental_channel_double_send(conn: aiosqlite.Connection) -> None:
    """
    Add experimental_channel_double_send column to app_settings table.

    When enabled, channel sends perform an immediate byte-perfect duplicate send
    using the same timestamp bytes.
    """
    try:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN experimental_channel_double_send INTEGER DEFAULT 0"
        )
        logger.debug("Added experimental_channel_double_send column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.debug("experimental_channel_double_send column already exists, skipping")
        else:
            raise

    await conn.commit()


async def _migrate_017_drop_experimental_channel_double_send(conn: aiosqlite.Connection) -> None:
    """
    Drop experimental_channel_double_send column from app_settings.

    This feature is replaced by a user-triggered resend button.
    SQLite 3.35.0+ supports ALTER TABLE DROP COLUMN. For older versions,
    we silently skip (the column will remain but is unused).
    """
    try:
        await conn.execute("ALTER TABLE app_settings DROP COLUMN experimental_channel_double_send")
        logger.debug("Dropped experimental_channel_double_send from app_settings")
    except aiosqlite.OperationalError as e:
        error_msg = str(e).lower()
        if "no such column" in error_msg:
            logger.debug("app_settings.experimental_channel_double_send already dropped, skipping")
        elif "syntax error" in error_msg or "drop column" in error_msg:
            logger.debug(
                "SQLite doesn't support DROP COLUMN, "
                "experimental_channel_double_send column will remain"
            )
        else:
            raise

    await conn.commit()


async def _migrate_018_drop_raw_packets_data_unique(conn: aiosqlite.Connection) -> None:
    """
    Drop the UNIQUE constraint on raw_packets.data via table rebuild.

    This constraint creates a large autoindex (~30 MB on a 340K-row database) that
    stores a complete copy of every raw packet BLOB in a B-tree. Deduplication is
    already handled by the unique index on payload_hash, making the data UNIQUE
    constraint pure storage overhead.

    Requires table recreation since SQLite doesn't support DROP CONSTRAINT.
    """
    # Check if the autoindex exists (indicates UNIQUE constraint on data)
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='sqlite_autoindex_raw_packets_1'"
    )
    if not await cursor.fetchone():
        logger.debug("raw_packets.data UNIQUE constraint already absent, skipping rebuild")
        await conn.commit()
        return

    logger.info("Rebuilding raw_packets table to remove UNIQUE(data) constraint...")

    # Get current columns from the existing table
    cursor = await conn.execute("PRAGMA table_info(raw_packets)")
    old_cols = {col[1] for col in await cursor.fetchall()}

    # Target schema without UNIQUE on data
    await conn.execute("""
        CREATE TABLE raw_packets_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            data BLOB NOT NULL,
            message_id INTEGER,
            payload_hash TEXT,
            FOREIGN KEY (message_id) REFERENCES messages(id)
        )
    """)

    # Copy only columns that exist in both old and new tables
    new_cols = {"id", "timestamp", "data", "message_id", "payload_hash"}
    copy_cols = ", ".join(sorted(c for c in new_cols if c in old_cols))

    await conn.execute(
        f"INSERT INTO raw_packets_new ({copy_cols}) SELECT {copy_cols} FROM raw_packets"
    )
    await conn.execute("DROP TABLE raw_packets")
    await conn.execute("ALTER TABLE raw_packets_new RENAME TO raw_packets")

    # Recreate indexes
    await conn.execute(
        "CREATE UNIQUE INDEX idx_raw_packets_payload_hash ON raw_packets(payload_hash)"
    )
    await conn.execute("CREATE INDEX idx_raw_packets_message_id ON raw_packets(message_id)")

    await conn.commit()
    logger.info("raw_packets table rebuilt without UNIQUE(data) constraint")


async def _migrate_019_drop_messages_unique_constraint(conn: aiosqlite.Connection) -> None:
    """
    Drop the UNIQUE(type, conversation_key, text, sender_timestamp) constraint on messages.

    This constraint creates a large autoindex (~13 MB on a 112K-row database) that
    stores the full message text in a B-tree. The idx_messages_dedup_null_safe unique
    index already provides identical dedup protection — no rows have NULL
    sender_timestamp since migration 15 backfilled them all.

    INSERT OR IGNORE still works correctly because it checks all unique constraints,
    including unique indexes like idx_messages_dedup_null_safe.

    Requires table recreation since SQLite doesn't support DROP CONSTRAINT.
    """
    # Check if the autoindex exists (indicates UNIQUE constraint)
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='sqlite_autoindex_messages_1'"
    )
    if not await cursor.fetchone():
        logger.debug("messages UNIQUE constraint already absent, skipping rebuild")
        await conn.commit()
        return

    logger.info("Rebuilding messages table to remove UNIQUE constraint...")

    # Get current columns from the existing table
    cursor = await conn.execute("PRAGMA table_info(messages)")
    old_cols = {col[1] for col in await cursor.fetchall()}

    # Target schema without the UNIQUE table constraint
    await conn.execute("""
        CREATE TABLE messages_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            conversation_key TEXT NOT NULL,
            text TEXT NOT NULL,
            sender_timestamp INTEGER,
            received_at INTEGER NOT NULL,
            txt_type INTEGER DEFAULT 0,
            signature TEXT,
            outgoing INTEGER DEFAULT 0,
            acked INTEGER DEFAULT 0,
            paths TEXT
        )
    """)

    # Copy only columns that exist in both old and new tables
    new_cols = {
        "id",
        "type",
        "conversation_key",
        "text",
        "sender_timestamp",
        "received_at",
        "txt_type",
        "signature",
        "outgoing",
        "acked",
        "paths",
    }
    copy_cols = ", ".join(sorted(c for c in new_cols if c in old_cols))

    await conn.execute(f"INSERT INTO messages_new ({copy_cols}) SELECT {copy_cols} FROM messages")
    await conn.execute("DROP TABLE messages")
    await conn.execute("ALTER TABLE messages_new RENAME TO messages")

    # Recreate indexes
    await conn.execute("CREATE INDEX idx_messages_conversation ON messages(type, conversation_key)")
    await conn.execute("CREATE INDEX idx_messages_received ON messages(received_at)")
    await conn.execute(
        """CREATE UNIQUE INDEX idx_messages_dedup_null_safe
           ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))"""
    )

    await conn.commit()
    logger.info("messages table rebuilt without UNIQUE constraint")


async def _migrate_020_enable_wal_and_auto_vacuum(conn: aiosqlite.Connection) -> None:
    """
    Enable WAL journal mode and incremental auto-vacuum.

    WAL (Write-Ahead Logging):
    - Faster writes: appends to a WAL file instead of rewriting the main DB
    - Concurrent reads during writes (readers don't block writers)
    - No journal file create/delete churn on every commit

    Incremental auto-vacuum:
    - Pages freed by DELETE become reclaimable without a full VACUUM
    - Call PRAGMA incremental_vacuum to reclaim on demand
    - Less overhead than FULL auto-vacuum (which reorganizes on every commit)

    auto_vacuum mode change requires a VACUUM to restructure the file.
    The VACUUM is performed before switching to WAL so it runs under the
    current journal mode; WAL is then set as the final step.
    """
    # Check current auto_vacuum mode
    cursor = await conn.execute("PRAGMA auto_vacuum")
    row = await cursor.fetchone()
    current_auto_vacuum = row[0] if row else 0

    if current_auto_vacuum != 2:  # 2 = INCREMENTAL
        logger.info("Switching auto_vacuum to INCREMENTAL (requires VACUUM)...")
        await conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        await conn.execute("VACUUM")
        logger.info("VACUUM complete, auto_vacuum set to INCREMENTAL")
    else:
        logger.debug("auto_vacuum already INCREMENTAL, skipping VACUUM")

    # Enable WAL mode (idempotent — returns current mode)
    cursor = await conn.execute("PRAGMA journal_mode = WAL")
    row = await cursor.fetchone()
    mode = row[0] if row else "unknown"
    logger.info("Journal mode set to %s", mode)

    await conn.commit()


async def _migrate_021_enforce_min_advert_interval(conn: aiosqlite.Connection) -> None:
    """
    Enforce minimum 1-hour advert interval.

    Any advert_interval between 1 and 3599 is clamped up to 3600 (1 hour).
    Zero (disabled) is left unchanged.
    """
    # Guard: app_settings table may not exist if running against a very old schema
    # (it's created in migration 9). The UPDATE is harmless if the table exists
    # but has no rows, but will error if the table itself is missing.
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
    )
    if await cursor.fetchone() is None:
        logger.debug("app_settings table does not exist yet, skipping advert_interval clamp")
        return

    await conn.execute(
        "UPDATE app_settings SET advert_interval = 3600 WHERE advert_interval > 0 AND advert_interval < 3600"
    )
    await conn.commit()
    logger.debug("Clamped advert_interval to minimum 3600 seconds")


async def _migrate_022_add_repeater_advert_paths(conn: aiosqlite.Connection) -> None:
    """
    Create table for recent unique advert paths per repeater.

    This keeps path diversity for repeater advertisements without changing the
    existing payload-hash raw packet dedup policy.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS repeater_advert_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repeater_key TEXT NOT NULL,
            path_hex TEXT NOT NULL,
            path_len INTEGER NOT NULL,
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            heard_count INTEGER NOT NULL DEFAULT 1,
            UNIQUE(repeater_key, path_hex),
            FOREIGN KEY (repeater_key) REFERENCES contacts(public_key)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_repeater_advert_paths_recent "
        "ON repeater_advert_paths(repeater_key, last_seen DESC)"
    )
    await conn.commit()
    logger.debug("Ensured repeater_advert_paths table and indexes exist")


async def _migrate_023_add_first_seen(conn: aiosqlite.Connection) -> None:
    """
    Add first_seen column to contacts table.

    Backfill strategy:
    1. Set first_seen = last_seen for all contacts (baseline).
    2. For contacts with PRIV messages, set first_seen = MIN(messages.received_at)
       if that timestamp is earlier.
    """
    # Guard: skip if contacts table doesn't exist (e.g. partial test schemas)
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
    )
    if not await cursor.fetchone():
        return

    try:
        await conn.execute("ALTER TABLE contacts ADD COLUMN first_seen INTEGER")
        logger.debug("Added first_seen to contacts table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("contacts.first_seen already exists, skipping")
        else:
            raise

    # Baseline: set first_seen = last_seen for all contacts
    # Check if last_seen column exists (should in production, may not in minimal test schemas)
    cursor = await conn.execute("PRAGMA table_info(contacts)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "last_seen" in columns:
        await conn.execute("UPDATE contacts SET first_seen = last_seen WHERE first_seen IS NULL")

    # Refine: for contacts with PRIV messages, use earliest message timestamp if earlier
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    if await cursor.fetchone():
        await conn.execute(
            """
            UPDATE contacts SET first_seen = (
                SELECT MIN(m.received_at) FROM messages m
                WHERE m.type = 'PRIV' AND m.conversation_key = contacts.public_key
            )
            WHERE EXISTS (
                SELECT 1 FROM messages m
                WHERE m.type = 'PRIV' AND m.conversation_key = contacts.public_key
                  AND m.received_at < COALESCE(contacts.first_seen, 9999999999)
            )
            """
        )

    await conn.commit()
    logger.debug("Added and backfilled first_seen column")


async def _migrate_024_create_contact_name_history(conn: aiosqlite.Connection) -> None:
    """
    Create contact_name_history table and seed with current contact names.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_name_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_key TEXT NOT NULL,
            name TEXT NOT NULL,
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            UNIQUE(public_key, name),
            FOREIGN KEY (public_key) REFERENCES contacts(public_key)
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_contact_name_history_key "
        "ON contact_name_history(public_key, last_seen DESC)"
    )

    # Seed: one row per contact from current data (skip if contacts table doesn't exist
    # or lacks needed columns)
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
    )
    if await cursor.fetchone():
        cursor = await conn.execute("PRAGMA table_info(contacts)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "name" in cols and "public_key" in cols:
            first_seen_expr = "first_seen" if "first_seen" in cols else "0"
            last_seen_expr = "last_seen" if "last_seen" in cols else "0"
            await conn.execute(
                f"""
                INSERT OR IGNORE INTO contact_name_history (public_key, name, first_seen, last_seen)
                SELECT public_key, name,
                       COALESCE({first_seen_expr}, {last_seen_expr}, 0),
                       COALESCE({last_seen_expr}, 0)
                FROM contacts
                WHERE name IS NOT NULL AND name != ''
                """
            )

    await conn.commit()
    logger.debug("Created contact_name_history table and seeded from contacts")


async def _migrate_025_add_sender_columns(conn: aiosqlite.Connection) -> None:
    """
    Add sender_name and sender_key columns to messages table.

    Backfill:
    - sender_name for CHAN messages: extract from "Name: message" format
    - sender_key for CHAN messages: match name to contact (skip ambiguous)
    - sender_key for incoming PRIV messages: set to conversation_key
    """
    # Guard: skip if messages table doesn't exist
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    if not await cursor.fetchone():
        return

    for column in ["sender_name", "sender_key"]:
        try:
            await conn.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT")
            logger.debug("Added %s to messages table", column)
        except aiosqlite.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.debug("messages.%s already exists, skipping", column)
            else:
                raise

    # Check which columns the messages table has (may be minimal in test environments)
    cursor = await conn.execute("PRAGMA table_info(messages)")
    msg_cols = {row[1] for row in await cursor.fetchall()}

    # Only backfill if the required columns exist
    if "type" in msg_cols and "text" in msg_cols:
        # Count messages to backfill for progress reporting
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM messages WHERE type = 'CHAN' AND sender_name IS NULL"
        )
        row = await cursor.fetchone()
        chan_count = row[0] if row else 0
        if chan_count > 0:
            logger.info("Backfilling sender_name for %d channel messages...", chan_count)

        # Backfill sender_name for CHAN messages from "Name: message" format
        # Only extract if colon position is valid (> 1 and < 51, i.e. name is 1-50 chars)
        cursor = await conn.execute(
            """
            UPDATE messages SET sender_name = SUBSTR(text, 1, INSTR(text, ': ') - 1)
            WHERE type = 'CHAN' AND sender_name IS NULL
              AND INSTR(text, ': ') > 1 AND INSTR(text, ': ') < 52
            """
        )
        if cursor.rowcount > 0:
            logger.info("Backfilled sender_name for %d channel messages", cursor.rowcount)

        # Backfill sender_key for incoming PRIV messages
        if "outgoing" in msg_cols and "conversation_key" in msg_cols:
            cursor = await conn.execute(
                """
                UPDATE messages SET sender_key = conversation_key
                WHERE type = 'PRIV' AND outgoing = 0 AND sender_key IS NULL
                """
            )
            if cursor.rowcount > 0:
                logger.info("Backfilled sender_key for %d DM messages", cursor.rowcount)

        # Backfill sender_key for CHAN messages: match sender_name to contacts
        # Build name->key map, skip ambiguous names (multiple contacts with same name)
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
        )
        if await cursor.fetchone():
            cursor = await conn.execute(
                "SELECT public_key, name FROM contacts WHERE name IS NOT NULL AND name != ''"
            )
            rows = await cursor.fetchall()

            name_to_keys: dict[str, list[str]] = {}
            for row in rows:
                name = row["name"]
                key = row["public_key"]
                if name not in name_to_keys:
                    name_to_keys[name] = []
                name_to_keys[name].append(key)

            # Only use unambiguous names (single contact per name)
            unambiguous = {n: ks[0] for n, ks in name_to_keys.items() if len(ks) == 1}
            if unambiguous:
                logger.info(
                    "Matching sender_key for %d unique contact names...",
                    len(unambiguous),
                )
                # Use a temp table for a single bulk UPDATE instead of N individual queries
                await conn.execute(
                    "CREATE TEMP TABLE _name_key_map (name TEXT PRIMARY KEY, public_key TEXT NOT NULL)"
                )
                await conn.executemany(
                    "INSERT INTO _name_key_map (name, public_key) VALUES (?, ?)",
                    list(unambiguous.items()),
                )
                cursor = await conn.execute(
                    """
                    UPDATE messages SET sender_key = (
                        SELECT public_key FROM _name_key_map WHERE _name_key_map.name = messages.sender_name
                    )
                    WHERE type = 'CHAN' AND sender_key IS NULL
                      AND sender_name IN (SELECT name FROM _name_key_map)
                    """
                )
                updated = cursor.rowcount
                await conn.execute("DROP TABLE _name_key_map")
                if updated > 0:
                    logger.info("Backfilled sender_key for %d channel messages", updated)

    # Create index on sender_key for per-contact channel message counts
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender_key ON messages(sender_key)")

    await conn.commit()
    logger.debug("Added sender_name and sender_key columns with backfill")


async def _migrate_026_rename_advert_paths_table(conn: aiosqlite.Connection) -> None:
    """
    Rename repeater_advert_paths to contact_advert_paths with column
    repeater_key -> public_key.

    Uses table rebuild since ALTER TABLE RENAME COLUMN may not be available
    in older SQLite versions.
    """
    # Check if old table exists
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='repeater_advert_paths'"
    )
    if not await cursor.fetchone():
        # Already renamed or doesn't exist — ensure new table exists
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_advert_paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT NOT NULL,
                path_hex TEXT NOT NULL,
                path_len INTEGER NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                heard_count INTEGER NOT NULL DEFAULT 1,
                UNIQUE(public_key, path_hex),
                FOREIGN KEY (public_key) REFERENCES contacts(public_key)
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contact_advert_paths_recent "
            "ON contact_advert_paths(public_key, last_seen DESC)"
        )
        await conn.commit()
        logger.debug("contact_advert_paths already exists or old table missing, skipping rename")
        return

    # Create new table (IF NOT EXISTS in case SCHEMA already created it)
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_advert_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_key TEXT NOT NULL,
            path_hex TEXT NOT NULL,
            path_len INTEGER NOT NULL,
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            heard_count INTEGER NOT NULL DEFAULT 1,
            UNIQUE(public_key, path_hex),
            FOREIGN KEY (public_key) REFERENCES contacts(public_key)
        )
        """
    )

    # Copy data (INSERT OR IGNORE in case of duplicates)
    await conn.execute(
        """
        INSERT OR IGNORE INTO contact_advert_paths (public_key, path_hex, path_len, first_seen, last_seen, heard_count)
        SELECT repeater_key, path_hex, path_len, first_seen, last_seen, heard_count
        FROM repeater_advert_paths
        """
    )

    # Drop old table
    await conn.execute("DROP TABLE repeater_advert_paths")

    # Create index
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_contact_advert_paths_recent "
        "ON contact_advert_paths(public_key, last_seen DESC)"
    )

    await conn.commit()
    logger.info("Renamed repeater_advert_paths to contact_advert_paths")


async def _migrate_027_backfill_first_seen_from_advert_paths(conn: aiosqlite.Connection) -> None:
    """
    Backfill contacts.first_seen from contact_advert_paths where advert path
    first_seen is earlier than the contact's current first_seen.
    """
    # Guard: skip if either table doesn't exist
    for table in ("contacts", "contact_advert_paths"):
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        if not await cursor.fetchone():
            return

    await conn.execute(
        """
        UPDATE contacts SET first_seen = (
            SELECT MIN(cap.first_seen) FROM contact_advert_paths cap
            WHERE cap.public_key = contacts.public_key
        )
        WHERE EXISTS (
            SELECT 1 FROM contact_advert_paths cap
            WHERE cap.public_key = contacts.public_key
              AND cap.first_seen < COALESCE(contacts.first_seen, 9999999999)
        )
        """
    )

    await conn.commit()
    logger.debug("Backfilled first_seen from contact_advert_paths")


async def _migrate_028_payload_hash_text_to_blob(conn: aiosqlite.Connection) -> None:
    """
    Convert payload_hash from 64-char hex TEXT to 32-byte BLOB.

    Halves storage for both the column data and its UNIQUE index.
    Uses Python bytes.fromhex() for the conversion since SQLite's unhex()
    requires 3.41.0+ which may not be available on all deployments.
    """
    # Guard: skip if raw_packets table doesn't exist
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_packets'"
    )
    if not await cursor.fetchone():
        logger.debug("raw_packets table does not exist, skipping payload_hash conversion")
        await conn.commit()
        return

    # Check column types — skip if payload_hash doesn't exist or is already BLOB
    cursor = await conn.execute("PRAGMA table_info(raw_packets)")
    cols = {row[1]: row[2] for row in await cursor.fetchall()}
    if "payload_hash" not in cols:
        logger.debug("payload_hash column does not exist, skipping conversion")
        await conn.commit()
        return
    if cols["payload_hash"].upper() == "BLOB":
        logger.debug("payload_hash is already BLOB, skipping conversion")
        await conn.commit()
        return

    logger.info("Rebuilding raw_packets to convert payload_hash TEXT → BLOB...")

    # Create new table with BLOB type
    await conn.execute("""
        CREATE TABLE raw_packets_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            data BLOB NOT NULL,
            message_id INTEGER,
            payload_hash BLOB,
            FOREIGN KEY (message_id) REFERENCES messages(id)
        )
    """)

    # Batch-convert rows: read TEXT hashes, convert to bytes, insert into new table
    batch_size = 5000
    cursor = await conn.execute(
        "SELECT id, timestamp, data, message_id, payload_hash FROM raw_packets ORDER BY id"
    )

    total = 0
    while True:
        rows = await cursor.fetchmany(batch_size)
        if not rows:
            break

        batch: list[tuple[int, int, bytes, int | None, bytes | None]] = []
        for row in rows:
            rid, ts, data, mid, ph = row[0], row[1], row[2], row[3], row[4]
            if ph is not None and isinstance(ph, str):
                try:
                    ph = bytes.fromhex(ph)
                except ValueError:
                    # Not a valid hex string — hash the value to produce a valid BLOB
                    ph = sha256(ph.encode()).digest()
            batch.append((rid, ts, data, mid, ph))

        await conn.executemany(
            "INSERT INTO raw_packets_new (id, timestamp, data, message_id, payload_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            batch,
        )
        total += len(batch)

        if total % 50000 == 0:
            logger.info("Converted %d rows...", total)

    # Preserve autoincrement sequence
    cursor = await conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'raw_packets'")
    seq_row = await cursor.fetchone()
    if seq_row is not None:
        await conn.execute(
            "INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES ('raw_packets_new', ?)",
            (seq_row[0],),
        )

    await conn.execute("DROP TABLE raw_packets")
    await conn.execute("ALTER TABLE raw_packets_new RENAME TO raw_packets")

    # Clean up the sqlite_sequence entry for the old temp name
    await conn.execute("DELETE FROM sqlite_sequence WHERE name = 'raw_packets_new'")

    # Recreate indexes
    await conn.execute(
        "CREATE UNIQUE INDEX idx_raw_packets_payload_hash ON raw_packets(payload_hash)"
    )
    await conn.execute("CREATE INDEX idx_raw_packets_message_id ON raw_packets(message_id)")

    await conn.commit()
    logger.info("Converted %d payload_hash values from TEXT to BLOB", total)


async def _migrate_029_add_unread_covering_index(conn: aiosqlite.Connection) -> None:
    """
    Add a covering index for the unread counts query.

    The /api/read-state/unreads endpoint runs three queries against messages.
    The last-message-times query (GROUP BY type, conversation_key + MAX(received_at))
    was doing a full table scan. This covering index lets SQLite resolve the
    grouping and MAX entirely from the index without touching the table.
    It also improves the unread count queries which filter on outgoing and received_at.
    """
    # Guard: table or columns may not exist in partial-schema test setups
    cursor = await conn.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in await cursor.fetchall()}
    required = {"type", "conversation_key", "outgoing", "received_at"}
    if required <= columns:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_unread_covering "
            "ON messages(type, conversation_key, outgoing, received_at)"
        )
    await conn.commit()


async def _migrate_030_add_pagination_index(conn: aiosqlite.Connection) -> None:
    """
    Add a composite index for message pagination and drop the now-redundant
    idx_messages_conversation.

    The pagination query (ORDER BY received_at DESC, id DESC LIMIT N) hits a
    temp B-tree sort without this index. With it, SQLite walks the index in
    order and stops after N rows — critical for channels with 30K+ messages.

    idx_messages_conversation(type, conversation_key) is a strict prefix of
    both this index and idx_messages_unread_covering, so SQLite never picks it.
    Dropping it saves ~6 MB and one index to maintain per INSERT.
    """
    # Guard: table or columns may not exist in partial-schema test setups
    cursor = await conn.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in await cursor.fetchall()}
    required = {"type", "conversation_key", "received_at", "id"}
    if required <= columns:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_pagination "
            "ON messages(type, conversation_key, received_at DESC, id DESC)"
        )
        await conn.execute("DROP INDEX IF EXISTS idx_messages_conversation")
    await conn.commit()


async def _migrate_031_add_mqtt_columns(conn: aiosqlite.Connection) -> None:
    """Add MQTT configuration columns to app_settings."""
    # Guard: app_settings may not exist in partial-schema test setups
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
    )
    if not await cursor.fetchone():
        await conn.commit()
        return

    cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await cursor.fetchall()}

    new_columns = [
        ("mqtt_broker_host", "TEXT DEFAULT ''"),
        ("mqtt_broker_port", "INTEGER DEFAULT 1883"),
        ("mqtt_username", "TEXT DEFAULT ''"),
        ("mqtt_password", "TEXT DEFAULT ''"),
        ("mqtt_use_tls", "INTEGER DEFAULT 0"),
        ("mqtt_tls_insecure", "INTEGER DEFAULT 0"),
        ("mqtt_topic_prefix", "TEXT DEFAULT 'meshcore'"),
        ("mqtt_publish_messages", "INTEGER DEFAULT 0"),
        ("mqtt_publish_raw_packets", "INTEGER DEFAULT 0"),
    ]

    for col_name, col_def in new_columns:
        if col_name not in columns:
            await conn.execute(f"ALTER TABLE app_settings ADD COLUMN {col_name} {col_def}")

    await conn.commit()


async def _migrate_032_add_community_mqtt_columns(conn: aiosqlite.Connection) -> None:
    """Add community MQTT configuration columns to app_settings."""
    # Guard: app_settings may not exist in partial-schema test setups
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
    )
    if not await cursor.fetchone():
        await conn.commit()
        return

    cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await cursor.fetchall()}

    new_columns = [
        ("community_mqtt_enabled", "INTEGER DEFAULT 0"),
        ("community_mqtt_iata", "TEXT DEFAULT ''"),
        ("community_mqtt_broker_host", "TEXT DEFAULT 'mqtt-us-v1.letsmesh.net'"),
        ("community_mqtt_broker_port", "INTEGER DEFAULT 443"),
        ("community_mqtt_email", "TEXT DEFAULT ''"),
    ]

    for col_name, col_def in new_columns:
        if col_name not in columns:
            await conn.execute(f"ALTER TABLE app_settings ADD COLUMN {col_name} {col_def}")

    await conn.commit()


async def _migrate_033_seed_remoteterm_channel(conn: aiosqlite.Connection) -> None:
    """Seed the #remoteterm hashtag channel so new installs have it by default.

    Uses INSERT OR IGNORE so it's a no-op if the channel already exists
    (e.g. existing users who already added it manually). The channels table
    is created by the base schema before migrations run, so it always exists
    in production.
    """
    try:
        await conn.execute(
            "INSERT OR IGNORE INTO channels (key, name, is_hashtag, on_radio) VALUES (?, ?, ?, ?)",
            ("8959AE053F2201801342A1DBDDA184F6", "#remoteterm", 1, 0),
        )
        await conn.commit()
    except Exception:
        logger.debug("Skipping #remoteterm seed (channels table not ready)")


async def _migrate_034_add_flood_scope(conn: aiosqlite.Connection) -> None:
    """Add flood_scope column to app_settings for outbound region tagging.

    Empty string means disabled (no scope set, messages sent unscoped).
    """
    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN flood_scope TEXT DEFAULT ''")
        await conn.commit()
    except Exception as e:
        error_msg = str(e).lower()
        if "duplicate column" in error_msg:
            logger.debug("flood_scope column already exists, skipping")
        elif "no such table" in error_msg:
            logger.debug("app_settings table not ready, skipping flood_scope migration")
        else:
            raise


async def _migrate_035_add_block_lists(conn: aiosqlite.Connection) -> None:
    """Add blocked_keys and blocked_names columns to app_settings.

    These store JSON arrays of blocked public keys and display names.
    Blocking hides messages from the UI but does not affect MQTT or bots.
    """
    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN blocked_keys TEXT DEFAULT '[]'")
    except Exception as e:
        error_msg = str(e).lower()
        if "duplicate column" in error_msg:
            logger.debug("blocked_keys column already exists, skipping")
        elif "no such table" in error_msg:
            logger.debug("app_settings table not ready, skipping blocked_keys migration")
        else:
            raise

    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN blocked_names TEXT DEFAULT '[]'")
    except Exception as e:
        error_msg = str(e).lower()
        if "duplicate column" in error_msg:
            logger.debug("blocked_names column already exists, skipping")
        elif "no such table" in error_msg:
            logger.debug("app_settings table not ready, skipping blocked_names migration")
        else:
            raise

    await conn.commit()


async def _migrate_036_add_apprise_settings(conn: aiosqlite.Connection) -> None:
    """Add Apprise external notification settings columns to app_settings."""
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
    )
    if await cursor.fetchone() is None:
        logger.debug("app_settings table does not exist yet, skipping apprise settings migration")
        return

    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN apprise_enabled INTEGER DEFAULT 0")
        logger.debug("Added apprise_enabled column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("app_settings.apprise_enabled already exists, skipping")
        else:
            raise

    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN apprise_url TEXT DEFAULT ''")
        logger.debug("Added apprise_url column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("app_settings.apprise_url already exists, skipping")
        else:
            raise

    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN apprise_mode TEXT DEFAULT 'all'")
        logger.debug("Added apprise_mode column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("app_settings.apprise_mode already exists, skipping")
        else:
            raise

    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN apprise_targets TEXT DEFAULT '[]'")
        logger.debug("Added apprise_targets column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("app_settings.apprise_targets already exists, skipping")
        else:
            raise

    await conn.commit()


async def _migrate_037_add_apprise_preserve_identity(conn: aiosqlite.Connection) -> None:
    """Add apprise_preserve_identity to app_settings."""
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
    )
    if await cursor.fetchone() is None:
        logger.debug(
            "app_settings table does not exist yet, skipping apprise preserve identity migration"
        )
        return

    try:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN apprise_preserve_identity INTEGER DEFAULT 1"
        )
        logger.debug("Added apprise_preserve_identity column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("app_settings.apprise_preserve_identity already exists, skipping")
        else:
            raise

    await conn.commit()


async def _migrate_038_repair_app_settings_columns(conn: aiosqlite.Connection) -> None:
    """Backfill any missing app_settings columns when schema version drift occurred.

    This is intentionally defensive: some historical upgrades ended up with advanced
    user_version values while one or more app_settings ALTERs were never applied.
    Re-running these idempotent column migrations repairs the table safely.
    """
    await _migrate_031_add_mqtt_columns(conn)
    await _migrate_032_add_community_mqtt_columns(conn)
    await _migrate_034_add_flood_scope(conn)
    await _migrate_035_add_block_lists(conn)
    await _migrate_036_add_apprise_settings(conn)
    await _migrate_037_add_apprise_preserve_identity(conn)
