# app/database.py
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)

SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS contacts (
    public_key TEXT PRIMARY KEY,
    name TEXT,
    type INTEGER DEFAULT 0,
    flags INTEGER DEFAULT 0,
    direct_path TEXT,
    direct_path_len INTEGER,
    direct_path_hash_mode INTEGER,
    direct_path_updated_at INTEGER,
    route_override_path TEXT,
    route_override_len INTEGER,
    route_override_hash_mode INTEGER,
    last_advert INTEGER,
    lat REAL,
    lon REAL,
    last_seen INTEGER,
    on_radio INTEGER DEFAULT 0,
    last_contacted INTEGER,
    first_seen INTEGER,
    last_read_at INTEGER,
    favorite INTEGER DEFAULT 0,
    mcmp_enabled INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS channels (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_hashtag INTEGER DEFAULT 0,
    on_radio INTEGER DEFAULT 0,
    flood_scope_override TEXT,
    path_hash_mode_override INTEGER,
    last_read_at INTEGER,
    favorite INTEGER DEFAULT 0,
    muted INTEGER DEFAULT 0,
    mcmp_enabled INTEGER DEFAULT 0,
    mcmp_sign_enabled INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    conversation_key TEXT NOT NULL,
    text TEXT NOT NULL,
    sender_timestamp INTEGER,
    received_at INTEGER NOT NULL,
    paths TEXT,
    txt_type INTEGER DEFAULT 0,
    signature TEXT,
    outgoing INTEGER DEFAULT 0,
    acked INTEGER DEFAULT 0,
    sender_name TEXT,
    sender_key TEXT
    -- Deduplication: channel echoes/repeats use a content/time unique index so
    -- duplicate observations reconcile onto a single stored row. Legacy
    -- databases may also gain an incoming-DM content index via migration 44.
    -- Enforced via idx_messages_dedup_null_safe (unique index) rather than a table constraint
    -- to avoid the storage overhead of SQLite's autoindex duplicating every message text.
);

CREATE TABLE IF NOT EXISTS raw_packets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    data BLOB NOT NULL,
    message_id INTEGER,
    payload_hash BLOB,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS contact_advert_paths (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_key TEXT NOT NULL,
    path_hex TEXT NOT NULL,
    path_len INTEGER NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    heard_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(public_key, path_hex, path_len),
    FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS contact_name_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_key TEXT NOT NULL,
    name TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    UNIQUE(public_key, name),
    FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    max_radio_contacts INTEGER DEFAULT 200,
    favorites TEXT DEFAULT '[]',
    auto_decrypt_dm_on_advert INTEGER DEFAULT 1,
    last_message_times TEXT DEFAULT '{}',
    preferences_migrated INTEGER DEFAULT 0,
    advert_interval INTEGER DEFAULT 0,
    last_advert_time INTEGER DEFAULT 0,
    flood_scope TEXT DEFAULT '',
    blocked_keys TEXT DEFAULT '[]',
    blocked_names TEXT DEFAULT '[]',
    discovery_blocked_types TEXT DEFAULT '[]',
    tracked_telemetry_repeaters TEXT DEFAULT '[]',
    auto_resend_channel INTEGER DEFAULT 0,
    telemetry_interval_hours INTEGER DEFAULT 8,
    vapid_private_key TEXT DEFAULT '',
    vapid_public_key TEXT DEFAULT '',
    push_conversations TEXT DEFAULT '[]'
);
INSERT OR IGNORE INTO app_settings (id) VALUES (1);

CREATE TABLE IF NOT EXISTS fanout_configs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER DEFAULT 0,
    config TEXT NOT NULL DEFAULT '{}',
    scope TEXT NOT NULL DEFAULT '{}',
    sort_order INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS repeater_telemetry_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_key TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    data TEXT NOT NULL,
    FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    last_success_at INTEGER,
    failure_count INTEGER DEFAULT 0,
    UNIQUE(endpoint)
);
"""

# Indexes are created after migrations so that legacy databases have all
# required columns (e.g. sender_key, added by migration 25) before index
# creation runs.
SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_messages_received ON messages(received_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_dedup_null_safe
    ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))
    WHERE type = 'CHAN';
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_incoming_priv_dedup
    ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0), COALESCE(sender_key, ''))
    WHERE type = 'PRIV' AND outgoing = 0;
CREATE INDEX IF NOT EXISTS idx_messages_sender_key ON messages(sender_key);
CREATE INDEX IF NOT EXISTS idx_messages_pagination
    ON messages(type, conversation_key, received_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_messages_unread_covering
    ON messages(type, conversation_key, outgoing, received_at);
CREATE INDEX IF NOT EXISTS idx_raw_packets_message_id ON raw_packets(message_id);
CREATE INDEX IF NOT EXISTS idx_raw_packets_timestamp ON raw_packets(timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_packets_payload_hash ON raw_packets(payload_hash);
CREATE INDEX IF NOT EXISTS idx_contacts_type_last_seen ON contacts(type, last_seen);
CREATE INDEX IF NOT EXISTS idx_messages_type_received_conversation
    ON messages(type, received_at, conversation_key);
CREATE INDEX IF NOT EXISTS idx_contact_advert_paths_recent
    ON contact_advert_paths(public_key, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_contact_name_history_key
    ON contact_name_history(public_key, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_repeater_telemetry_pk_ts
    ON repeater_telemetry_history(public_key, timestamp);
"""


class Database:
    """Single-connection aiosqlite wrapper with coroutine-level serialization.

    Why the lock: aiosqlite runs one ``sqlite3.Connection`` on a background
    worker thread and serializes statement execution there. But SQLite's
    ``COMMIT`` fails with ``OperationalError: cannot commit transaction -
    SQL statements in progress`` whenever *any* cursor on the connection has
    a live prepared statement (a ``SELECT`` that returned ``SQLITE_ROW`` but
    hasn't been fully consumed or closed). Under concurrent coroutines, one
    task's in-flight ``fetchone()`` can still be in ``SQLITE_ROW`` state when
    another task's ``commit()`` runs on the worker — triggering the error.

    Fix: all DB work goes through ``tx()`` (writes) or ``readonly()`` (reads),
    both of which acquire ``self._lock``. The lock is non-reentrant (asyncio
    default) by design — nested ``tx()`` calls are a bug. Repository methods
    that compose multiple operations factor the raw SQL into private helpers
    that take a ``conn`` and don't lock; the public method acquires the lock
    once and calls those helpers.

    Why reads are also locked: reads must also hold the lock, because a read
    in ``SQLITE_ROW`` state is precisely the live statement that breaks a
    concurrent writer's commit. Single-connection aiosqlite cannot safely
    overlap reads and writes. If we ever split reader/writer connections in
    the future, ``readonly()`` becomes the seam to point at the reader pool.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire the connection for a write transaction.

        Commits on clean exit, rolls back on exception. Callers MUST close
        every cursor opened inside the block (use ``async with conn.execute(...)
        as cursor:``) so no prepared statement is alive when commit runs.

        The lock serializes concurrent writers AND ensures no reader's cursor
        is alive during the commit. Nested calls will deadlock — factor shared
        SQL into helpers that accept ``conn`` and do not re-enter ``tx()``.
        """
        async with self._lock:
            if self._connection is None:
                raise RuntimeError("Database not connected")
            conn = self._connection
            try:
                yield conn
            except BaseException:
                await conn.rollback()
                raise
            else:
                await conn.commit()

    @asynccontextmanager
    async def readonly(self) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire the connection for a read. No commit, no rollback.

        Locked for the same reason writes are: on a single connection, an
        active read statement blocks a concurrent writer's commit. Callers
        MUST fully consume or close cursors before the block exits (use
        ``async with conn.execute(...) as cursor:`` + ``fetchall`` /
        ``fetchone``; avoid holding a cursor across ``await`` on other IO).
        """
        async with self._lock:
            if self._connection is None:
                raise RuntimeError("Database not connected")
            yield self._connection

    async def connect(self) -> None:
        logger.info("Connecting to database at %s", self.db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row

        # WAL mode: faster writes, concurrent readers during writes, no journal file churn.
        # Persists in the DB file but we set it explicitly on every connection.
        await self._connection.execute("PRAGMA journal_mode = WAL")

        # synchronous = NORMAL is safe with WAL — only the most recent
        # transaction can be lost on an OS crash (no corruption risk).
        # Reduces fsync overhead vs. the default FULL.
        await self._connection.execute("PRAGMA synchronous = NORMAL")

        # Retry for up to 5s on lock contention instead of failing instantly.
        # Matters when a second connection (e.g. VACUUM) touches the DB.
        await self._connection.execute("PRAGMA busy_timeout = 5000")

        # Bump page cache to ~64 MB (negative value = KB). Keeps hot pages
        # in memory for read-heavy queries (unreads, pagination, search).
        await self._connection.execute("PRAGMA cache_size = -64000")

        # Keep temp tables and sort spills in memory instead of on disk.
        await self._connection.execute("PRAGMA temp_store = MEMORY")

        # Incremental auto-vacuum: freed pages are reclaimable via
        # PRAGMA incremental_vacuum without a full VACUUM. Must be set before
        # the first table is created (for new databases); for existing databases
        # migration 20 handles the one-time VACUUM to restructure the file.
        await self._connection.execute("PRAGMA auto_vacuum = INCREMENTAL")

        # Foreign key enforcement: must be set per-connection (not persisted).
        # Disabled during schema init and migrations to avoid issues with
        # historical table-rebuild migrations that may temporarily violate
        # constraints, then re-enabled for all subsequent application queries.
        await self._connection.execute("PRAGMA foreign_keys = OFF")

        await self._connection.executescript(SCHEMA_TABLES)
        await self._connection.commit()
        logger.debug("Database tables initialized")

        # Run any pending migrations before creating indexes, so that
        # legacy databases have all required columns first.
        from app.migrations import run_migrations

        await run_migrations(self._connection)

        await self._connection.executescript(SCHEMA_INDEXES)
        await self._connection.commit()
        logger.debug("Database indexes initialized")

        # Enable FK enforcement for all application queries from this point on.
        await self._connection.execute("PRAGMA foreign_keys = ON")
        logger.debug("Foreign key enforcement enabled")

    async def disconnect(self) -> None:
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.debug("Database connection closed")

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._connection:
            raise RuntimeError("Database not connected")
        return self._connection


db = Database(settings.database_path)