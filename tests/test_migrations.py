"""Tests for database migrations."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version


class TestMigrationSystem:
    """Test the migration version tracking system."""

    @pytest.mark.asyncio
    async def test_get_version_returns_zero_for_new_db(self):
        """New database has user_version=0."""
        conn = await aiosqlite.connect(":memory:")
        try:
            version = await get_version(conn)
            assert version == 0
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_set_version_updates_pragma(self):
        """Setting version updates the user_version pragma."""
        conn = await aiosqlite.connect(":memory:")
        try:
            await set_version(conn, 5)
            version = await get_version(conn)
            assert version == 5
        finally:
            await conn.close()


class TestMigration001:
    """Test migration 001: add last_read_at columns."""

    @pytest.mark.asyncio
    async def test_migration_adds_last_read_at_to_contacts(self):
        """Migration adds last_read_at column to contacts table."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            # Create schema without last_read_at (simulating pre-migration state)
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT,
                    type INTEGER DEFAULT 0,
                    flags INTEGER DEFAULT 0,
                    last_path TEXT,
                    last_path_len INTEGER DEFAULT -1,
                    last_advert INTEGER,
                    lat REAL,
                    lon REAL,
                    last_seen INTEGER,
                    on_radio INTEGER DEFAULT 0,
                    last_contacted INTEGER
                )
            """)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_hashtag INTEGER DEFAULT 0,
                    on_radio INTEGER DEFAULT 0
                )
            """)
            # Raw packets table with old schema (for migrations 2 and 3)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    decrypted INTEGER DEFAULT 0,
                    message_id INTEGER,
                    decrypt_attempts INTEGER DEFAULT 0,
                    last_attempt INTEGER
                )
            """)
            await conn.execute("CREATE INDEX idx_raw_packets_decrypted ON raw_packets(decrypted)")
            # Messages table with old schema (for migrations 6 and 7)
            await conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sender_timestamp INTEGER,
                    received_at INTEGER NOT NULL,
                    path_len INTEGER,
                    txt_type INTEGER DEFAULT 0,
                    signature TEXT,
                    outgoing INTEGER DEFAULT 0,
                    acked INTEGER DEFAULT 0,
                    UNIQUE(type, conversation_key, text, sender_timestamp)
                )
            """)
            await conn.commit()

            # Run migrations
            applied = await run_migrations(conn)

            assert applied == 37  # All migrations run
            assert await get_version(conn) == 37

            # Verify columns exist by inserting and selecting
            await conn.execute(
                "INSERT INTO contacts (public_key, name, last_read_at) VALUES (?, ?, ?)",
                ("abc123", "Test", 12345),
            )
            await conn.execute(
                "INSERT INTO channels (key, name, last_read_at) VALUES (?, ?, ?)",
                ("KEY123", "#test", 67890),
            )
            await conn.commit()

            cursor = await conn.execute(
                "SELECT last_read_at FROM contacts WHERE public_key = ?", ("abc123",)
            )
            row = await cursor.fetchone()
            assert row["last_read_at"] == 12345

            cursor = await conn.execute(
                "SELECT last_read_at FROM channels WHERE key = ?", ("KEY123",)
            )
            row = await cursor.fetchone()
            assert row["last_read_at"] == 67890
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self):
        """Running migration multiple times is safe."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            # Create schema without last_read_at
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT
                )
            """)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                )
            """)
            # Raw packets table with old schema (for migrations 2 and 3)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    decrypted INTEGER DEFAULT 0,
                    message_id INTEGER,
                    decrypt_attempts INTEGER DEFAULT 0,
                    last_attempt INTEGER
                )
            """)
            await conn.execute("CREATE INDEX idx_raw_packets_decrypted ON raw_packets(decrypted)")
            # Messages table with old schema (for migrations 6 and 7)
            await conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sender_timestamp INTEGER,
                    received_at INTEGER NOT NULL,
                    path_len INTEGER,
                    txt_type INTEGER DEFAULT 0,
                    signature TEXT,
                    outgoing INTEGER DEFAULT 0,
                    acked INTEGER DEFAULT 0,
                    UNIQUE(type, conversation_key, text, sender_timestamp)
                )
            """)
            await conn.commit()

            # Run migrations twice
            applied1 = await run_migrations(conn)
            applied2 = await run_migrations(conn)

            assert applied1 == 37  # All migrations run
            assert applied2 == 0  # No migrations on second run
            assert await get_version(conn) == 37
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_handles_column_already_exists(self):
        """Migration handles case where column already exists."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            # Create schema with last_read_at already present
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT,
                    last_read_at INTEGER
                )
            """)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    last_read_at INTEGER
                )
            """)
            # Raw packets table with old schema (for migrations 2 and 3)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    decrypted INTEGER DEFAULT 0,
                    message_id INTEGER,
                    decrypt_attempts INTEGER DEFAULT 0,
                    last_attempt INTEGER
                )
            """)
            await conn.execute("CREATE INDEX idx_raw_packets_decrypted ON raw_packets(decrypted)")
            # Messages table with old schema (for migrations 6 and 7)
            await conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sender_timestamp INTEGER,
                    received_at INTEGER NOT NULL,
                    path_len INTEGER,
                    txt_type INTEGER DEFAULT 0,
                    signature TEXT,
                    outgoing INTEGER DEFAULT 0,
                    acked INTEGER DEFAULT 0,
                    UNIQUE(type, conversation_key, text, sender_timestamp)
                )
            """)
            await conn.commit()

            # Run migrations - should not fail
            applied = await run_migrations(conn)

            # All migrations applied (version incremented) but no error
            assert applied == 37
            assert await get_version(conn) == 37
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_existing_data_preserved_after_migration(self):
        """Migration preserves existing contact and channel data."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            # Create schema and insert data before migration
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT,
                    type INTEGER DEFAULT 0
                )
            """)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_hashtag INTEGER DEFAULT 0
                )
            """)
            # Raw packets table with old schema (for migrations 2 and 3)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    decrypted INTEGER DEFAULT 0,
                    message_id INTEGER,
                    decrypt_attempts INTEGER DEFAULT 0,
                    last_attempt INTEGER
                )
            """)
            await conn.execute("CREATE INDEX idx_raw_packets_decrypted ON raw_packets(decrypted)")
            # Messages table with old schema (for migrations 6 and 7)
            await conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sender_timestamp INTEGER,
                    received_at INTEGER NOT NULL,
                    path_len INTEGER,
                    txt_type INTEGER DEFAULT 0,
                    signature TEXT,
                    outgoing INTEGER DEFAULT 0,
                    acked INTEGER DEFAULT 0,
                    UNIQUE(type, conversation_key, text, sender_timestamp)
                )
            """)
            await conn.execute(
                "INSERT INTO contacts (public_key, name, type) VALUES (?, ?, ?)",
                ("existingkey", "ExistingContact", 1),
            )
            await conn.execute(
                "INSERT INTO channels (key, name, is_hashtag) VALUES (?, ?, ?)",
                ("EXISTINGCHAN", "#existing", 1),
            )
            await conn.commit()

            # Run migrations
            await run_migrations(conn)

            # Verify data is preserved
            cursor = await conn.execute(
                "SELECT public_key, name, type, last_read_at FROM contacts WHERE public_key = ?",
                ("existingkey",),
            )
            row = await cursor.fetchone()
            assert row["public_key"] == "existingkey"
            assert row["name"] == "ExistingContact"
            assert row["type"] == 1
            assert row["last_read_at"] is None  # New column defaults to NULL

            cursor = await conn.execute(
                "SELECT key, name, is_hashtag, last_read_at FROM channels WHERE key = ?",
                ("EXISTINGCHAN",),
            )
            row = await cursor.fetchone()
            assert row["key"] == "EXISTINGCHAN"
            assert row["name"] == "#existing"
            assert row["is_hashtag"] == 1
            assert row["last_read_at"] is None
        finally:
            await conn.close()


class TestMigration013:
    """Test migration 013: convert bot_enabled/bot_code to multi-bot format."""

    @pytest.mark.asyncio
    async def test_migration_converts_existing_bot_to_array(self):
        """Migration converts existing bot_enabled/bot_code to bots array."""
        import json

        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            # Set version to 12 (just before migration 13)
            await set_version(conn, 12)

            # Create app_settings with old bot columns
            await conn.execute("""
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    max_radio_contacts INTEGER DEFAULT 50,
                    favorites TEXT DEFAULT '[]',
                    auto_decrypt_dm_on_advert INTEGER DEFAULT 0,
                    sidebar_sort_order TEXT DEFAULT 'recent',
                    last_message_times TEXT DEFAULT '{}',
                    preferences_migrated INTEGER DEFAULT 0,
                    advert_interval INTEGER DEFAULT 0,
                    last_advert_time INTEGER DEFAULT 0,
                    bot_enabled INTEGER DEFAULT 0,
                    bot_code TEXT DEFAULT ''
                )
            """)
            await conn.execute(
                "INSERT INTO app_settings (id, bot_enabled, bot_code) VALUES (1, 1, 'def bot(): return \"hello\"')"
            )
            await conn.commit()

            # Run migration 13 (plus 14-34 which also run)
            applied = await run_migrations(conn)
            assert applied == 25
            assert await get_version(conn) == 37

            # Verify bots array was created with migrated data
            cursor = await conn.execute("SELECT bots FROM app_settings WHERE id = 1")
            row = await cursor.fetchone()
            bots = json.loads(row["bots"])

            assert len(bots) == 1
            assert bots[0]["name"] == "Bot 1"
            assert bots[0]["enabled"] is True
            assert bots[0]["code"] == 'def bot(): return "hello"'
            assert "id" in bots[0]  # Should have a UUID
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_creates_empty_array_when_no_bot(self):
        """Migration creates empty bots array when no existing bot data."""
        import json

        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 12)

            await conn.execute("""
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    max_radio_contacts INTEGER DEFAULT 50,
                    favorites TEXT DEFAULT '[]',
                    auto_decrypt_dm_on_advert INTEGER DEFAULT 0,
                    sidebar_sort_order TEXT DEFAULT 'recent',
                    last_message_times TEXT DEFAULT '{}',
                    preferences_migrated INTEGER DEFAULT 0,
                    advert_interval INTEGER DEFAULT 0,
                    last_advert_time INTEGER DEFAULT 0,
                    bot_enabled INTEGER DEFAULT 0,
                    bot_code TEXT DEFAULT ''
                )
            """)
            await conn.execute(
                "INSERT INTO app_settings (id, bot_enabled, bot_code) VALUES (1, 0, '')"
            )
            await conn.commit()

            await run_migrations(conn)

            cursor = await conn.execute("SELECT bots FROM app_settings WHERE id = 1")
            row = await cursor.fetchone()
            bots = json.loads(row["bots"])

            assert bots == []
        finally:
            await conn.close()


class TestMigration018:
    """Test migration 018: drop UNIQUE(data) from raw_packets."""

    @pytest.mark.asyncio
    async def test_migration_drops_data_unique_constraint(self):
        """Migration rebuilds raw_packets without UNIQUE(data), preserving data."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 17)

            # Create raw_packets WITH UNIQUE(data) — simulates production schema
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL UNIQUE,
                    message_id INTEGER,
                    payload_hash TEXT
                )
            """)
            await conn.execute(
                "CREATE UNIQUE INDEX idx_raw_packets_payload_hash ON raw_packets(payload_hash)"
            )
            await conn.execute("CREATE INDEX idx_raw_packets_message_id ON raw_packets(message_id)")

            # Insert test data
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
                (1000, b"\x01\x02\x03", "hash_a"),
            )
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, message_id, payload_hash) VALUES (?, ?, ?, ?)",
                (2000, b"\x04\x05\x06", 42, "hash_b"),
            )
            # Create messages table stub (needed for migration 19)
            await conn.execute("""
                CREATE TABLE messages (
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
            await conn.execute(
                """CREATE UNIQUE INDEX idx_messages_dedup_null_safe
                   ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))"""
            )
            await conn.commit()

            # Verify autoindex exists before migration
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_autoindex_raw_packets_1'"
            )
            assert await cursor.fetchone() is not None

            await run_migrations(conn)
            assert await get_version(conn) == 37

            # Verify autoindex is gone
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_autoindex_raw_packets_1'"
            )
            assert await cursor.fetchone() is None

            # Verify data is preserved
            cursor = await conn.execute("SELECT COUNT(*) FROM raw_packets")
            assert (await cursor.fetchone())[0] == 2

            cursor = await conn.execute(
                "SELECT timestamp, data, message_id, payload_hash FROM raw_packets ORDER BY id"
            )
            rows = await cursor.fetchall()
            assert rows[0]["timestamp"] == 1000
            assert bytes(rows[0]["data"]) == b"\x01\x02\x03"
            assert rows[0]["message_id"] is None
            # payload_hash was converted from TEXT to BLOB by migration 28;
            # "hash_a" is not valid hex so gets sha256-hashed
            from hashlib import sha256

            assert bytes(rows[0]["payload_hash"]) == sha256(b"hash_a").digest()
            assert rows[1]["message_id"] == 42

            # Verify payload_hash unique index still works
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='idx_raw_packets_payload_hash'"
            )
            assert await cursor.fetchone() is not None
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_skips_when_no_unique_constraint(self):
        """Migration is a no-op when UNIQUE(data) is already absent."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 17)

            # Create raw_packets WITHOUT UNIQUE(data) — fresh install schema
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    message_id INTEGER,
                    payload_hash TEXT
                )
            """)
            await conn.execute(
                "CREATE UNIQUE INDEX idx_raw_packets_payload_hash ON raw_packets(payload_hash)"
            )
            # Messages stub for migration 19
            await conn.execute("""
                CREATE TABLE messages (
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
            await conn.execute(
                """CREATE UNIQUE INDEX idx_messages_dedup_null_safe
                   ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))"""
            )
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == 20  # Migrations 18-37 run (18+19 skip internally)
            assert await get_version(conn) == 37
        finally:
            await conn.close()


class TestMigration019:
    """Test migration 019: drop UNIQUE constraint from messages."""

    @pytest.mark.asyncio
    async def test_migration_drops_messages_unique_constraint(self):
        """Migration rebuilds messages without UNIQUE, preserving data and dedup index."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 17)

            # raw_packets stub (no UNIQUE on data, so migration 18 skips)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    message_id INTEGER,
                    payload_hash TEXT
                )
            """)
            # Create messages WITH UNIQUE constraint — simulates production schema
            await conn.execute("""
                CREATE TABLE messages (
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
                    paths TEXT,
                    UNIQUE(type, conversation_key, text, sender_timestamp)
                )
            """)
            await conn.execute(
                "CREATE INDEX idx_messages_conversation ON messages(type, conversation_key)"
            )
            await conn.execute("CREATE INDEX idx_messages_received ON messages(received_at)")
            await conn.execute(
                """CREATE UNIQUE INDEX idx_messages_dedup_null_safe
                   ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))"""
            )

            # Insert test data
            await conn.execute(
                "INSERT INTO messages (type, conversation_key, text, sender_timestamp, received_at, paths) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("CHAN", "KEY1", "hello world", 1000, 1000, '[{"path":"ab","received_at":1000}]'),
            )
            await conn.execute(
                "INSERT INTO messages (type, conversation_key, text, sender_timestamp, received_at, outgoing) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("PRIV", "abc123", "dm text", 2000, 2000, 1),
            )
            await conn.commit()

            # Verify autoindex exists before migration
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_autoindex_messages_1'"
            )
            assert await cursor.fetchone() is not None

            await run_migrations(conn)
            assert await get_version(conn) == 37

            # Verify autoindex is gone
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_autoindex_messages_1'"
            )
            assert await cursor.fetchone() is None

            # Verify data is preserved
            cursor = await conn.execute("SELECT COUNT(*) FROM messages")
            assert (await cursor.fetchone())[0] == 2

            cursor = await conn.execute(
                "SELECT type, conversation_key, text, paths, outgoing FROM messages ORDER BY id"
            )
            rows = await cursor.fetchall()
            assert rows[0]["type"] == "CHAN"
            assert rows[0]["text"] == "hello world"
            assert rows[0]["paths"] == '[{"path":"ab","received_at":1000}]'
            assert rows[1]["type"] == "PRIV"
            assert rows[1]["outgoing"] == 1

            # Verify dedup index still works (INSERT OR IGNORE should ignore duplicates)
            cursor = await conn.execute(
                "INSERT OR IGNORE INTO messages (type, conversation_key, text, sender_timestamp, received_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("CHAN", "KEY1", "hello world", 1000, 9999),
            )
            assert cursor.rowcount == 0  # Duplicate ignored

            # Verify dedup index exists
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='idx_messages_dedup_null_safe'"
            )
            assert await cursor.fetchone() is not None
        finally:
            await conn.close()


class TestMigration020:
    """Test migration 020: enable WAL mode and incremental auto-vacuum."""

    @pytest.mark.asyncio
    async def test_migration_enables_wal_and_incremental_auto_vacuum(self, tmp_path):
        """Migration switches journal mode to WAL and auto_vacuum to INCREMENTAL."""
        db_path = str(tmp_path / "test.db")
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 19)

            # Create minimal tables so migration 20 can run
            await conn.execute(
                "CREATE TABLE raw_packets (id INTEGER PRIMARY KEY, data BLOB NOT NULL)"
            )
            await conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, text TEXT NOT NULL)")
            await conn.commit()

            # Verify defaults before migration
            cursor = await conn.execute("PRAGMA auto_vacuum")
            assert (await cursor.fetchone())[0] == 0  # NONE

            cursor = await conn.execute("PRAGMA journal_mode")
            assert (await cursor.fetchone())[0] == "delete"

            applied = await run_migrations(conn)
            assert applied == 18  # Migrations 20-35
            assert await get_version(conn) == 37

            # Verify WAL mode
            cursor = await conn.execute("PRAGMA journal_mode")
            assert (await cursor.fetchone())[0] == "wal"

            # Verify incremental auto-vacuum
            cursor = await conn.execute("PRAGMA auto_vacuum")
            assert (await cursor.fetchone())[0] == 2  # INCREMENTAL
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self, tmp_path):
        """Running migration 20 twice doesn't error or re-VACUUM."""
        db_path = str(tmp_path / "test.db")
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        try:
            # Set up as if already at version 20 with WAL + incremental
            await conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute(
                "CREATE TABLE raw_packets (id INTEGER PRIMARY KEY, data BLOB NOT NULL)"
            )
            await conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, text TEXT NOT NULL)")
            await conn.commit()
            await set_version(conn, 20)

            applied = await run_migrations(conn)
            assert applied == 17  # Migrations 21-35 still run

            # Still WAL + INCREMENTAL
            cursor = await conn.execute("PRAGMA journal_mode")
            assert (await cursor.fetchone())[0] == "wal"
            cursor = await conn.execute("PRAGMA auto_vacuum")
            assert (await cursor.fetchone())[0] == 2
        finally:
            await conn.close()


class TestMigration028:
    """Test migration 028: convert payload_hash from TEXT to BLOB."""

    @pytest.mark.asyncio
    async def test_migration_converts_hex_text_to_blob(self):
        """Migration converts 64-char hex TEXT payload_hash values to 32-byte BLOBs."""
        from hashlib import sha256

        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 27)

            # Create raw_packets with TEXT payload_hash (pre-migration schema)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    message_id INTEGER,
                    payload_hash TEXT
                )
            """)
            await conn.execute(
                "CREATE UNIQUE INDEX idx_raw_packets_payload_hash ON raw_packets(payload_hash)"
            )
            await conn.execute("CREATE INDEX idx_raw_packets_message_id ON raw_packets(message_id)")

            # Insert rows with hex TEXT hashes (as produced by .hexdigest())
            hash_a = sha256(b"packet_a").hexdigest()
            hash_b = sha256(b"packet_b").hexdigest()
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
                (1000, b"\x01\x02", hash_a),
            )
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, message_id, payload_hash) VALUES (?, ?, ?, ?)",
                (2000, b"\x03\x04", 42, hash_b),
            )
            # Row with NULL payload_hash
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data) VALUES (?, ?)",
                (3000, b"\x05\x06"),
            )
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == 10
            assert await get_version(conn) == 37

            # Verify payload_hash column is now BLOB
            cursor = await conn.execute("PRAGMA table_info(raw_packets)")
            cols = {row[1]: row[2] for row in await cursor.fetchall()}
            assert cols["payload_hash"] == "BLOB"

            # Verify data is preserved and converted correctly
            cursor = await conn.execute(
                "SELECT id, timestamp, data, message_id, payload_hash FROM raw_packets ORDER BY id"
            )
            rows = await cursor.fetchall()
            assert len(rows) == 3

            assert rows[0]["timestamp"] == 1000
            assert bytes(rows[0]["data"]) == b"\x01\x02"
            assert bytes(rows[0]["payload_hash"]) == sha256(b"packet_a").digest()
            assert rows[0]["message_id"] is None

            assert rows[1]["timestamp"] == 2000
            assert bytes(rows[1]["payload_hash"]) == sha256(b"packet_b").digest()
            assert rows[1]["message_id"] == 42

            assert rows[2]["payload_hash"] is None

            # Verify unique index works
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='idx_raw_packets_payload_hash'"
            )
            assert await cursor.fetchone() is not None

            # Verify message_id index exists
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='idx_raw_packets_message_id'"
            )
            assert await cursor.fetchone() is not None
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_skips_when_already_blob(self):
        """Migration is a no-op when payload_hash is already BLOB (fresh install)."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 27)

            # Create raw_packets with BLOB payload_hash (new schema)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    message_id INTEGER,
                    payload_hash BLOB
                )
            """)
            await conn.execute(
                "CREATE UNIQUE INDEX idx_raw_packets_payload_hash ON raw_packets(payload_hash)"
            )

            # Insert a row with a BLOB hash
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
                (1000, b"\x01", b"\xab" * 32),
            )
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == 10  # Version still bumped
            assert await get_version(conn) == 37

            # Verify data unchanged
            cursor = await conn.execute("SELECT payload_hash FROM raw_packets")
            row = await cursor.fetchone()
            assert bytes(row["payload_hash"]) == b"\xab" * 32
        finally:
            await conn.close()


class TestMigration032:
    """Test migration 032: add community MQTT columns to app_settings."""

    @pytest.mark.asyncio
    async def test_migration_adds_all_community_mqtt_columns(self):
        """Migration adds enabled, iata, broker, and email columns."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 31)

            # Create app_settings without community columns (pre-migration schema)
            await conn.execute("""
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    max_radio_contacts INTEGER DEFAULT 200,
                    favorites TEXT DEFAULT '[]',
                    auto_decrypt_dm_on_advert INTEGER DEFAULT 0,
                    sidebar_sort_order TEXT DEFAULT 'recent',
                    last_message_times TEXT DEFAULT '{}',
                    preferences_migrated INTEGER DEFAULT 0,
                    advert_interval INTEGER DEFAULT 0,
                    last_advert_time INTEGER DEFAULT 0,
                    bots TEXT DEFAULT '[]',
                    mqtt_broker_host TEXT DEFAULT '',
                    mqtt_broker_port INTEGER DEFAULT 1883,
                    mqtt_username TEXT DEFAULT '',
                    mqtt_password TEXT DEFAULT '',
                    mqtt_use_tls INTEGER DEFAULT 0,
                    mqtt_tls_insecure INTEGER DEFAULT 0,
                    mqtt_topic_prefix TEXT DEFAULT 'meshcore',
                    mqtt_publish_messages INTEGER DEFAULT 0,
                    mqtt_publish_raw_packets INTEGER DEFAULT 0
                )
            """)
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == 6
            assert await get_version(conn) == 37

            # Verify all columns exist with correct defaults
            cursor = await conn.execute(
                """SELECT community_mqtt_enabled, community_mqtt_iata,
                          community_mqtt_broker_host, community_mqtt_broker_port,
                          community_mqtt_email
                   FROM app_settings WHERE id = 1"""
            )
            row = await cursor.fetchone()
            assert row["community_mqtt_enabled"] == 0
            assert row["community_mqtt_iata"] == ""
            assert row["community_mqtt_broker_host"] == "mqtt-us-v1.letsmesh.net"
            assert row["community_mqtt_broker_port"] == 443
            assert row["community_mqtt_email"] == ""
        finally:
            await conn.close()


class TestMigration034:
    """Test migration 034: add flood_scope column to app_settings."""

    @pytest.mark.asyncio
    async def test_migration_adds_flood_scope_column(self):
        """Migration adds flood_scope column with empty string default."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 33)

            # Create app_settings without flood_scope (pre-migration schema)
            await conn.execute("""
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    max_radio_contacts INTEGER DEFAULT 200,
                    favorites TEXT DEFAULT '[]',
                    auto_decrypt_dm_on_advert INTEGER DEFAULT 0,
                    sidebar_sort_order TEXT DEFAULT 'recent',
                    last_message_times TEXT DEFAULT '{}',
                    preferences_migrated INTEGER DEFAULT 0,
                    advert_interval INTEGER DEFAULT 0,
                    last_advert_time INTEGER DEFAULT 0,
                    bots TEXT DEFAULT '[]',
                    mqtt_broker_host TEXT DEFAULT '',
                    mqtt_broker_port INTEGER DEFAULT 1883,
                    mqtt_username TEXT DEFAULT '',
                    mqtt_password TEXT DEFAULT '',
                    mqtt_use_tls INTEGER DEFAULT 0,
                    mqtt_tls_insecure INTEGER DEFAULT 0,
                    mqtt_topic_prefix TEXT DEFAULT 'meshcore',
                    mqtt_publish_messages INTEGER DEFAULT 0,
                    mqtt_publish_raw_packets INTEGER DEFAULT 0,
                    community_mqtt_enabled INTEGER DEFAULT 0,
                    community_mqtt_iata TEXT DEFAULT '',
                    community_mqtt_broker_host TEXT DEFAULT 'mqtt-us-v1.letsmesh.net',
                    community_mqtt_broker_port INTEGER DEFAULT 443,
                    community_mqtt_email TEXT DEFAULT ''
                )
            """)
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            # Channels table needed for migration 33
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_hashtag INTEGER DEFAULT 0,
                    on_radio INTEGER DEFAULT 0
                )
            """)
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == 4
            assert await get_version(conn) == 37

            # Verify column exists with correct default
            cursor = await conn.execute("SELECT flood_scope FROM app_settings WHERE id = 1")
            row = await cursor.fetchone()
            assert row["flood_scope"] == ""
        finally:
            await conn.close()


class TestMigration033:
    """Test migration 033: seed #remoteterm channel."""

    @pytest.mark.asyncio
    async def test_migration_seeds_remoteterm_channel(self):
        """Migration inserts the #remoteterm channel for new installs."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 32)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_hashtag INTEGER DEFAULT 0,
                    on_radio INTEGER DEFAULT 0
                )
            """)
            # Minimal app_settings so earlier migrations don't fail
            await conn.execute("""
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    community_mqtt_enabled INTEGER DEFAULT 0,
                    community_mqtt_iata TEXT DEFAULT '',
                    community_mqtt_broker_host TEXT DEFAULT '',
                    community_mqtt_broker_port INTEGER DEFAULT 443,
                    community_mqtt_email TEXT DEFAULT ''
                )
            """)
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == 5
            assert await get_version(conn) == 37

            cursor = await conn.execute(
                "SELECT key, name, is_hashtag, on_radio FROM channels WHERE key = ?",
                ("8959AE053F2201801342A1DBDDA184F6",),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["name"] == "#remoteterm"
            assert row["is_hashtag"] == 1
            assert row["on_radio"] == 0
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_does_not_overwrite_existing_channel(self):
        """Migration is a no-op if #remoteterm already exists."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 32)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_hashtag INTEGER DEFAULT 0,
                    on_radio INTEGER DEFAULT 0
                )
            """)
            await conn.execute("""
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    community_mqtt_enabled INTEGER DEFAULT 0,
                    community_mqtt_iata TEXT DEFAULT '',
                    community_mqtt_broker_host TEXT DEFAULT '',
                    community_mqtt_broker_port INTEGER DEFAULT 443,
                    community_mqtt_email TEXT DEFAULT ''
                )
            """)
            # Pre-existing channel with on_radio=1 (user added it to radio)
            await conn.execute(
                "INSERT INTO channels (key, name, is_hashtag, on_radio) VALUES (?, ?, ?, ?)",
                ("8959AE053F2201801342A1DBDDA184F6", "#remoteterm", 1, 1),
            )
            await conn.commit()

            await run_migrations(conn)

            cursor = await conn.execute(
                "SELECT on_radio FROM channels WHERE key = ?",
                ("8959AE053F2201801342A1DBDDA184F6",),
            )
            row = await cursor.fetchone()
            assert row["on_radio"] == 1  # Not overwritten
        finally:
            await conn.close()
