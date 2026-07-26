"""Tests addressing fanout hitlist gaps: BotModule params, migrations 036-038,
disable_bots PATCH guard, and community MQTT IATA validation."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
from fastapi import HTTPException

from app.migrations import set_version

# ---------------------------------------------------------------------------
# T1: BotModule parameter extraction
# ---------------------------------------------------------------------------


class TestBotModuleParameterExtraction:
    """Verify BotModule._run_for_message extracts params from broadcast data."""

    @pytest.mark.asyncio
    async def test_channel_is_outgoing_propagated(self):
        """Channel messages with outgoing=True pass is_outgoing=True to bot code."""
        from app.fanout.bot import BotModule

        captured = {}

        def fake_execute(
            code,
            sender_name,
            sender_key,
            message_text,
            is_dm,
            channel_key,
            channel_name,
            sender_timestamp,
            path,
            is_outgoing,
            path_bytes_per_hop,
            packet_hash,
            region,
            scoped,
        ):
            captured["is_outgoing"] = is_outgoing
            captured["is_dm"] = is_dm
            return None

        mod = BotModule("test", {"code": "def bot(**k): pass"}, name="Test")

        with (
            patch("app.fanout.bot_exec.execute_bot_code", side_effect=fake_execute),
            patch(
                "app.fanout.bot_exec._bot_semaphore",
                MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()),
            ),
            patch("app.fanout.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("app.repository.ChannelRepository") as mock_chan,
        ):
            mock_chan.get_by_key = AsyncMock(return_value=MagicMock(name="#test"))
            await mod._run_for_message(
                {
                    "type": "CHAN",
                    "conversation_key": "ch1",
                    "text": "Alice: hello",
                    "sender_name": "Alice",
                    "outgoing": True,
                }
            )

        assert captured["is_outgoing"] is True
        assert captured["is_dm"] is False

    @pytest.mark.asyncio
    async def test_channel_is_outgoing_false_by_default(self):
        """Channel messages without outgoing field default to is_outgoing=False."""
        from app.fanout.bot import BotModule

        captured = {}

        def fake_execute(
            code,
            sender_name,
            sender_key,
            message_text,
            is_dm,
            channel_key,
            channel_name,
            sender_timestamp,
            path,
            is_outgoing,
            path_bytes_per_hop,
            packet_hash,
            region,
            scoped,
        ):
            captured["is_outgoing"] = is_outgoing
            return None

        mod = BotModule("test", {"code": "def bot(**k): pass"}, name="Test")

        with (
            patch("app.fanout.bot_exec.execute_bot_code", side_effect=fake_execute),
            patch(
                "app.fanout.bot_exec._bot_semaphore",
                MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()),
            ),
            patch("app.fanout.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("app.repository.ChannelRepository") as mock_chan,
        ):
            mock_chan.get_by_key = AsyncMock(return_value=MagicMock(name="#test"))
            await mod._run_for_message(
                {
                    "type": "CHAN",
                    "conversation_key": "ch1",
                    "text": "Bob: hi",
                    "sender_name": "Bob",
                }
            )

        assert captured["is_outgoing"] is False

    @pytest.mark.asyncio
    async def test_path_extracted_from_paths_list(self):
        """Path is extracted from paths list-of-dicts format."""
        from app.fanout.bot import BotModule

        captured = {}

        def fake_execute(
            code,
            sender_name,
            sender_key,
            message_text,
            is_dm,
            channel_key,
            channel_name,
            sender_timestamp,
            path,
            is_outgoing,
            path_bytes_per_hop,
            packet_hash,
            region,
            scoped,
        ):
            captured["path"] = path
            captured["path_bytes_per_hop"] = path_bytes_per_hop
            return None

        mod = BotModule("test", {"code": "def bot(**k): pass"}, name="Test")

        with (
            patch("app.fanout.bot_exec.execute_bot_code", side_effect=fake_execute),
            patch(
                "app.fanout.bot_exec._bot_semaphore",
                MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()),
            ),
            patch("app.fanout.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("app.repository.ContactRepository") as mock_contact,
        ):
            mock_contact.get_by_key = AsyncMock(return_value=MagicMock(name="Alice"))
            await mod._run_for_message(
                {
                    "type": "PRIV",
                    "conversation_key": "pk1",
                    "text": "hello",
                    "paths": [{"path": "aabbccdd", "path_len": 2, "rssi": -50}],
                }
            )

        assert captured["path"] == "aabbccdd"
        assert captured["path_bytes_per_hop"] == 2

    @pytest.mark.asyncio
    async def test_region_extracted_from_payload(self):
        """A channel message's resolved region is pulled from data and forwarded (#300)."""
        from app.fanout.bot import BotModule

        captured = {}

        def fake_execute(
            code,
            sender_name,
            sender_key,
            message_text,
            is_dm,
            channel_key,
            channel_name,
            sender_timestamp,
            path,
            is_outgoing,
            path_bytes_per_hop,
            packet_hash,
            region,
            scoped,
        ):
            captured["region"] = region
            return None

        mod = BotModule("test", {"code": "def bot(**k): pass"}, name="Test")

        with (
            patch("app.fanout.bot_exec.execute_bot_code", side_effect=fake_execute),
            patch(
                "app.fanout.bot_exec._bot_semaphore",
                MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()),
            ),
            patch("app.fanout.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("app.repository.ChannelRepository") as mock_chan,
        ):
            mock_chan.get_by_key = AsyncMock(return_value=MagicMock(name="#test"))
            await mod._run_for_message(
                {
                    "type": "CHAN",
                    "conversation_key": "ch1",
                    "text": "Alice: hello",
                    "sender_name": "Alice",
                    "channel_name": "#test",
                    "region": "EU",
                }
            )

        assert captured["region"] == "EU"

    @pytest.mark.asyncio
    async def test_region_absent_defaults_to_none(self):
        """A message without a region key forwards region=None to bot execution."""
        from app.fanout.bot import BotModule

        captured = {}

        def fake_execute(
            code,
            sender_name,
            sender_key,
            message_text,
            is_dm,
            channel_key,
            channel_name,
            sender_timestamp,
            path,
            is_outgoing,
            path_bytes_per_hop,
            packet_hash,
            region,
            scoped,
        ):
            captured["region"] = region
            return None

        mod = BotModule("test", {"code": "def bot(**k): pass"}, name="Test")

        with (
            patch("app.fanout.bot_exec.execute_bot_code", side_effect=fake_execute),
            patch(
                "app.fanout.bot_exec._bot_semaphore",
                MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()),
            ),
            patch("app.fanout.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("app.repository.ContactRepository") as mock_contact,
        ):
            mock_contact.get_by_key = AsyncMock(return_value=MagicMock(name="Alice"))
            await mod._run_for_message(
                {
                    "type": "PRIV",
                    "conversation_key": "pk1",
                    "text": "hello",
                }
            )

        assert captured["region"] is None

    @pytest.mark.asyncio
    async def test_scoped_true_when_transport_code_present(self):
        """A message with a transport_code forwards scoped=True (#300)."""
        from app.fanout.bot import BotModule

        captured = {}

        def fake_execute(
            code,
            sender_name,
            sender_key,
            message_text,
            is_dm,
            channel_key,
            channel_name,
            sender_timestamp,
            path,
            is_outgoing,
            path_bytes_per_hop,
            packet_hash,
            region,
            scoped,
        ):
            captured["scoped"] = scoped
            return None

        mod = BotModule("test", {"code": "def bot(**k): pass"}, name="Test")

        with (
            patch("app.fanout.bot_exec.execute_bot_code", side_effect=fake_execute),
            patch(
                "app.fanout.bot_exec._bot_semaphore",
                MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()),
            ),
            patch("app.fanout.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("app.repository.ChannelRepository") as mock_chan,
        ):
            mock_chan.get_by_key = AsyncMock(return_value=MagicMock(name="#test"))
            await mod._run_for_message(
                {
                    "type": "CHAN",
                    "conversation_key": "ch1",
                    "text": "Alice: hello",
                    "sender_name": "Alice",
                    "channel_name": "#test",
                    # Scoped but region unresolved: scoped must still be True.
                    "transport_code": 6789,
                    "region": None,
                }
            )

        assert captured["scoped"] is True

    @pytest.mark.asyncio
    async def test_scoped_false_when_transport_code_absent(self):
        """A message without a transport_code forwards scoped=False (unscoped flood)."""
        from app.fanout.bot import BotModule

        captured = {}

        def fake_execute(
            code,
            sender_name,
            sender_key,
            message_text,
            is_dm,
            channel_key,
            channel_name,
            sender_timestamp,
            path,
            is_outgoing,
            path_bytes_per_hop,
            packet_hash,
            region,
            scoped,
        ):
            captured["scoped"] = scoped
            return None

        mod = BotModule("test", {"code": "def bot(**k): pass"}, name="Test")

        with (
            patch("app.fanout.bot_exec.execute_bot_code", side_effect=fake_execute),
            patch(
                "app.fanout.bot_exec._bot_semaphore",
                MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()),
            ),
            patch("app.fanout.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("app.repository.ContactRepository") as mock_contact,
        ):
            mock_contact.get_by_key = AsyncMock(return_value=MagicMock(name="Alice"))
            await mod._run_for_message(
                {
                    "type": "PRIV",
                    "conversation_key": "pk1",
                    "text": "hello",
                }
            )

        assert captured["scoped"] is False

    @pytest.mark.asyncio
    async def test_channel_sender_prefix_stripped(self):
        """Channel message text has 'SenderName: ' prefix stripped."""
        from app.fanout.bot import BotModule

        captured = {}

        def fake_execute(
            code,
            sender_name,
            sender_key,
            message_text,
            is_dm,
            channel_key,
            channel_name,
            sender_timestamp,
            path,
            is_outgoing,
            path_bytes_per_hop,
            packet_hash,
            region,
            scoped,
        ):
            captured["message_text"] = message_text
            captured["sender_name"] = sender_name
            return None

        mod = BotModule("test", {"code": "def bot(**k): pass"}, name="Test")

        with (
            patch("app.fanout.bot_exec.execute_bot_code", side_effect=fake_execute),
            patch(
                "app.fanout.bot_exec._bot_semaphore",
                MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()),
            ),
            patch("app.fanout.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("app.repository.ChannelRepository") as mock_chan,
        ):
            mock_chan.get_by_key = AsyncMock(return_value=MagicMock(name="#general"))
            await mod._run_for_message(
                {
                    "type": "CHAN",
                    "conversation_key": "ch1",
                    "text": "Alice: the actual message",
                    "sender_name": "Alice",
                }
            )

        assert captured["message_text"] == "the actual message"
        assert captured["sender_name"] == "Alice"

    @pytest.mark.asyncio
    async def test_channel_name_uses_payload_before_db_lookup(self):
        """Channel fanout payload channel_name is preserved even if the DB lookup misses."""
        from app.fanout.bot import BotModule

        captured = {}

        def fake_execute(
            code,
            sender_name,
            sender_key,
            message_text,
            is_dm,
            channel_key,
            channel_name,
            sender_timestamp,
            path,
            is_outgoing,
            path_bytes_per_hop,
            packet_hash,
            region,
            scoped,
        ):
            captured["channel_name"] = channel_name
            return None

        mod = BotModule("test", {"code": "def bot(**k): pass"}, name="Test")

        with (
            patch("app.fanout.bot_exec.execute_bot_code", side_effect=fake_execute),
            patch(
                "app.fanout.bot_exec._bot_semaphore",
                MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()),
            ),
            patch("app.fanout.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("app.repository.ChannelRepository") as mock_chan,
        ):
            mock_chan.get_by_key = AsyncMock(return_value=None)
            await mod._run_for_message(
                {
                    "type": "CHAN",
                    "conversation_key": "ch1",
                    "channel_name": "#payload",
                    "text": "Alice: hello",
                    "sender_name": "Alice",
                }
            )

        assert captured["channel_name"] == "#payload"

    @pytest.mark.asyncio
    async def test_dm_sender_name_uses_payload_before_db_lookup(self):
        """Incoming DM sender_name from the message payload should be preserved."""
        from app.fanout.bot import BotModule

        captured = {}

        def fake_execute(
            code,
            sender_name,
            sender_key,
            message_text,
            is_dm,
            channel_key,
            channel_name,
            sender_timestamp,
            path,
            is_outgoing,
            path_bytes_per_hop,
            packet_hash,
            region,
            scoped,
        ):
            captured["sender_name"] = sender_name
            captured["sender_key"] = sender_key
            return None

        mod = BotModule("test", {"code": "def bot(**k): pass"}, name="Test")

        with (
            patch("app.fanout.bot_exec.execute_bot_code", side_effect=fake_execute),
            patch(
                "app.fanout.bot_exec._bot_semaphore",
                MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()),
            ),
            patch("app.fanout.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("app.repository.ContactRepository") as mock_contact,
        ):
            mock_contact.get_by_key = AsyncMock(return_value=None)
            await mod._run_for_message(
                {
                    "type": "PRIV",
                    "conversation_key": "pk1",
                    "sender_name": "PayloadAlice",
                    "sender_key": "pk1",
                    "text": "hello",
                    "outgoing": False,
                }
            )

        assert captured["sender_name"] == "PayloadAlice"
        assert captured["sender_key"] == "pk1"


# ---------------------------------------------------------------------------
# T2: Migration 036, 037, 038 tests
# ---------------------------------------------------------------------------

# Helper to build an app_settings schema at version 35 (pre-fanout)
_APP_SETTINGS_V35 = """
CREATE TABLE app_settings (
    id INTEGER PRIMARY KEY,
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
    community_mqtt_email TEXT DEFAULT '',
    bots TEXT DEFAULT '[]'
)
"""


class TestMigration036:
    """Test migration 036: create fanout_configs and migrate MQTT settings."""

    @pytest.mark.asyncio
    async def test_migrates_private_mqtt(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 35)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute(
                """INSERT INTO app_settings (id, mqtt_broker_host, mqtt_broker_port,
                   mqtt_publish_messages, mqtt_publish_raw_packets)
                   VALUES (1, 'broker.test', 8883, 1, 0)"""
            )
            await conn.commit()

            from app.migrations._036_create_fanout_configs import (
                migrate as _migrate_036_create_fanout_configs,
            )

            await _migrate_036_create_fanout_configs(conn)

            cursor = await conn.execute("SELECT * FROM fanout_configs WHERE type = 'mqtt_private'")
            row = await cursor.fetchone()
            assert row is not None
            config = json.loads(row["config"])
            assert config["broker_host"] == "broker.test"
            assert config["broker_port"] == 8883
            assert row["enabled"] == 1
            scope = json.loads(row["scope"])
            assert scope["messages"] == "all"
            assert scope["raw_packets"] == "none"
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migrates_enabled_community_mqtt(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 35)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute(
                """INSERT INTO app_settings (id, community_mqtt_enabled,
                   community_mqtt_iata, community_mqtt_email)
                   VALUES (1, 1, 'PDX', 'user@test.com')"""
            )
            await conn.commit()

            from app.migrations._036_create_fanout_configs import (
                migrate as _migrate_036_create_fanout_configs,
            )

            await _migrate_036_create_fanout_configs(conn)

            cursor = await conn.execute(
                "SELECT * FROM fanout_configs WHERE type = 'mqtt_community'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["enabled"] == 1
            config = json.loads(row["config"])
            assert config["iata"] == "PDX"
            assert config["email"] == "user@test.com"
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_preserves_disabled_but_configured_community_mqtt(self):
        """B4 fix: disabled community MQTT with populated fields is preserved."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 35)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute(
                """INSERT INTO app_settings (id, community_mqtt_enabled,
                   community_mqtt_iata, community_mqtt_email)
                   VALUES (1, 0, 'SEA', 'test@test.com')"""
            )
            await conn.commit()

            from app.migrations._036_create_fanout_configs import (
                migrate as _migrate_036_create_fanout_configs,
            )

            await _migrate_036_create_fanout_configs(conn)

            cursor = await conn.execute(
                "SELECT * FROM fanout_configs WHERE type = 'mqtt_community'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["enabled"] == 0  # Preserved as disabled
            config = json.loads(row["config"])
            assert config["iata"] == "SEA"
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_skips_empty_settings(self):
        """No fanout rows created when MQTT is unconfigured."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 35)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            from app.migrations._036_create_fanout_configs import (
                migrate as _migrate_036_create_fanout_configs,
            )

            await _migrate_036_create_fanout_configs(conn)

            cursor = await conn.execute("SELECT COUNT(*) FROM fanout_configs")
            row = await cursor.fetchone()
            assert row[0] == 0
        finally:
            await conn.close()


class TestMigration037:
    """Test migration 037: migrate bots to fanout_configs."""

    @pytest.mark.asyncio
    async def test_migrates_bots(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 36)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS fanout_configs (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
                    enabled INTEGER DEFAULT 0, config TEXT NOT NULL DEFAULT '{}',
                    scope TEXT NOT NULL DEFAULT '{}', sort_order INTEGER DEFAULT 0,
                    created_at INTEGER NOT NULL DEFAULT 0
                )
            """)
            bots = [
                {"name": "Echo", "enabled": True, "code": "def bot(**k): return k['message_text']"},
                {"name": "Silent", "enabled": False, "code": "def bot(**k): pass"},
            ]
            await conn.execute(
                "INSERT INTO app_settings (id, bots) VALUES (1, ?)",
                (json.dumps(bots),),
            )
            await conn.commit()

            from app.migrations._037_bots_to_fanout import migrate as _migrate_037_bots_to_fanout

            await _migrate_037_bots_to_fanout(conn)

            cursor = await conn.execute(
                "SELECT * FROM fanout_configs WHERE type = 'bot' ORDER BY sort_order"
            )
            rows = await cursor.fetchall()
            assert len(rows) == 2
            assert rows[0]["name"] == "Echo"
            assert rows[0]["enabled"] == 1
            assert rows[1]["name"] == "Silent"
            assert rows[1]["enabled"] == 0
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_empty_bots_is_noop(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 36)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS fanout_configs (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
                    enabled INTEGER DEFAULT 0, config TEXT NOT NULL DEFAULT '{}',
                    scope TEXT NOT NULL DEFAULT '{}', sort_order INTEGER DEFAULT 0,
                    created_at INTEGER NOT NULL DEFAULT 0
                )
            """)
            await conn.execute("INSERT INTO app_settings (id, bots) VALUES (1, '[]')")
            await conn.commit()

            from app.migrations._037_bots_to_fanout import migrate as _migrate_037_bots_to_fanout

            await _migrate_037_bots_to_fanout(conn)

            cursor = await conn.execute("SELECT COUNT(*) FROM fanout_configs")
            row = await cursor.fetchone()
            assert row[0] == 0
        finally:
            await conn.close()


class TestMigration038:
    """Test migration 038: drop legacy columns from app_settings."""

    @pytest.mark.asyncio
    async def test_drops_legacy_columns(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 37)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            from app.migrations._038_drop_legacy_columns import (
                migrate as _migrate_038_drop_legacy_columns,
            )

            await _migrate_038_drop_legacy_columns(conn)

            cursor = await conn.execute("PRAGMA table_info(app_settings)")
            remaining = {row[1] for row in await cursor.fetchall()}
            assert "mqtt_broker_host" not in remaining
            assert "bots" not in remaining
            assert "community_mqtt_enabled" not in remaining
            # id should remain
            assert "id" in remaining
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_handles_already_dropped_columns(self):
        """Migration handles columns already dropped (idempotent)."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 37)
            # Minimal table with only id — all legacy columns already gone
            await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY)")
            await conn.commit()

            from app.migrations._038_drop_legacy_columns import (
                migrate as _migrate_038_drop_legacy_columns,
            )

            # Should not raise
            await _migrate_038_drop_legacy_columns(conn)
        finally:
            await conn.close()


# ---------------------------------------------------------------------------
# T3: PATCH /api/fanout/{id} disable_bots guard
# ---------------------------------------------------------------------------


class TestDisableBotsPatchGuard:
    """Verify PATCH /api/fanout/{id} returns 403 for bots when disabled."""

    @pytest.mark.asyncio
    async def test_bot_update_returns_403_when_disabled(self, test_db):
        """PATCH on an existing bot config returns 403 when bots are disabled."""
        from app.repository.fanout import FanoutConfigRepository
        from app.routers.fanout import FanoutConfigUpdate, update_fanout_config

        # Create a bot config first (with bots enabled)
        cfg = await FanoutConfigRepository.create(
            config_type="bot",
            name="Test Bot",
            config={"code": "def bot(**k): pass"},
            scope={"messages": "all", "raw_packets": "none"},
            enabled=False,
        )

        # Now try to update with bots disabled
        with patch(
            "app.routers.fanout.fanout_manager.get_bots_disabled_source", return_value="env"
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_fanout_config(
                    cfg["id"],
                    FanoutConfigUpdate(enabled=True),
                )

            assert exc_info.value.status_code == 403
            assert "disabled" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_mqtt_update_allowed_when_bots_disabled(self, test_db):
        """PATCH on a non-bot config is allowed even when bots are disabled."""
        from app.repository.fanout import FanoutConfigRepository
        from app.routers.fanout import FanoutConfigUpdate, update_fanout_config

        cfg = await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="Test MQTT",
            config={"broker_host": "localhost", "broker_port": 1883},
            scope={"messages": "all", "raw_packets": "all"},
            enabled=False,
        )

        with patch(
            "app.routers.fanout.fanout_manager.get_bots_disabled_source", return_value="env"
        ):
            with patch("app.fanout.manager.fanout_manager.reload_config", new_callable=AsyncMock):
                result = await update_fanout_config(
                    cfg["id"],
                    FanoutConfigUpdate(name="Renamed"),
                )

            assert result["name"] == "Renamed"


# ---------------------------------------------------------------------------
# Q4: Community MQTT IATA validation
# ---------------------------------------------------------------------------


class TestCommunityMqttIataValidation:
    """Verify community MQTT requires valid IATA when enabled."""

    def test_empty_iata_rejected(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({"iata": ""})
        assert exc_info.value.status_code == 400
        assert "IATA" in exc_info.value.detail

    def test_missing_iata_rejected(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({})
        assert exc_info.value.status_code == 400

    def test_valid_iata_accepted(self):
        from app.routers.fanout import _validate_mqtt_community_config

        # Should not raise
        _validate_mqtt_community_config({"iata": "PDX"})

    def test_non_string_iata_rejected_as_validation_error(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({"iata": 123})
        assert exc_info.value.status_code == 400
        assert "string" in exc_info.value.detail

    def test_invalid_iata_format_rejected(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException):
            _validate_mqtt_community_config({"iata": "PD"})

        with pytest.raises(HTTPException):
            _validate_mqtt_community_config({"iata": "pdx1"})

    def test_topic_template_defaults_when_missing(self):
        from app.routers.fanout import _validate_mqtt_community_config

        config = {"iata": "PDX"}
        _validate_mqtt_community_config(config)
        assert config["broker_host"] == "mqtt-us-v1.letsmesh.net"
        assert config["broker_port"] == 443
        assert config["transport"] == "websockets"
        assert config["use_tls"] is True
        assert config["tls_verify"] is True
        assert config["auth_mode"] == "token"
        assert config["username"] == ""
        assert config["password"] == ""
        assert config["token_audience"] == ""
        assert config["topic_template"] == "meshcore/{IATA}/{PUBLIC_KEY}/packets"

    def test_topic_template_normalizes_placeholder_capitalization(self):
        from app.routers.fanout import _validate_mqtt_community_config

        config = {"iata": "PDX", "topic_template": "mesh2mqtt/{iata}/node/{Public_Key}"}
        _validate_mqtt_community_config(config)
        assert config["topic_template"] == "mesh2mqtt/{IATA}/node/{PUBLIC_KEY}"

    def test_topic_template_rejects_unknown_placeholder(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config(
                {"iata": "PDX", "topic_template": "meshcore/{foo}/{PUBLIC_KEY}/packets"}
            )
        assert exc_info.value.status_code == 400
        assert "topic_template" in exc_info.value.detail

    def test_transport_rejects_unknown_value(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({"iata": "PDX", "transport": "udp"})
        assert exc_info.value.status_code == 400
        assert "transport" in exc_info.value.detail

    def test_blank_token_audience_is_preserved(self):
        from app.routers.fanout import _validate_mqtt_community_config

        config = {"iata": "PDX", "token_audience": "   "}
        _validate_mqtt_community_config(config)
        assert config["token_audience"] == ""

    def test_auth_mode_rejects_unknown_value(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({"iata": "PDX", "auth_mode": "jwt"})
        assert exc_info.value.status_code == 400
        assert "auth_mode" in exc_info.value.detail

    def test_password_auth_requires_credentials(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({"iata": "PDX", "auth_mode": "password"})
        assert exc_info.value.status_code == 400
        assert "username and password" in exc_info.value.detail


class TestFanoutConfigMutationInvariant:
    """Persisted fanout rows should always be valid and canonical."""

    @pytest.mark.asyncio
    async def test_disabled_create_still_validates_config(self, test_db):
        from app.routers.fanout import FanoutConfigCreate, create_fanout_config

        with pytest.raises(HTTPException) as exc_info:
            await create_fanout_config(
                FanoutConfigCreate(
                    type="mqtt_community",
                    name="Invalid draft",
                    config={},
                    scope={},
                    enabled=False,
                )
            )

        assert exc_info.value.status_code == 400
        assert "IATA" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_enable_only_patch_persists_normalized_config(self, test_db):
        from app.repository.fanout import FanoutConfigRepository
        from app.routers.fanout import FanoutConfigUpdate, update_fanout_config

        cfg = await FanoutConfigRepository.create(
            config_type="mqtt_community",
            name="Community MQTT",
            config={
                "iata": "PDX",
                "broker_host": " mqtt.example.com ",
                "topic_template": "mesh2mqtt/{iata}/node/{Public_Key}",
            },
            scope={"messages": "none", "raw_packets": "all"},
            enabled=False,
        )

        with patch("app.fanout.manager.fanout_manager.reload_config", new_callable=AsyncMock):
            updated = await update_fanout_config(
                cfg["id"],
                FanoutConfigUpdate(enabled=True),
            )

        assert updated["enabled"] is True
        assert updated["config"]["broker_host"] == "mqtt.example.com"
        assert updated["config"]["topic_template"] == "mesh2mqtt/{IATA}/node/{PUBLIC_KEY}"
        assert updated["config"]["transport"] == "websockets"
        assert updated["config"]["auth_mode"] == "token"
