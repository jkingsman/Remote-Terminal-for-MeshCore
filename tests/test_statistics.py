"""Tests for the statistics repository and endpoint."""

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.repository import StatisticsRepository


class TestStatisticsEmpty:
    @pytest.mark.asyncio
    async def test_empty_database(self, test_db):
        """All counts should be zero on an empty database."""
        result = await StatisticsRepository.get_all()

        assert result["contact_count"] == 0
        assert result["repeater_count"] == 0
        assert result["channel_count"] == 1  # #remoteterm seed from migration 33
        assert result["total_packets"] == 0
        assert result["decrypted_packets"] == 0
        assert result["undecrypted_packets"] == 0
        assert result["total_dms"] == 0
        assert result["total_channel_messages"] == 0
        assert result["total_outgoing"] == 0
        assert result["busiest_channels_24h"] == []
        assert result["contacts_heard"]["last_hour"] == 0
        assert result["contacts_heard"]["last_24_hours"] == 0
        assert result["contacts_heard"]["last_week"] == 0
        assert result["repeaters_heard"]["last_hour"] == 0
        assert result["repeaters_heard"]["last_24_hours"] == 0
        assert result["repeaters_heard"]["last_week"] == 0
        assert result["known_channels_active"]["last_hour"] == 0
        assert result["known_channels_active"]["last_24_hours"] == 0
        assert result["known_channels_active"]["last_week"] == 0
        assert result["path_hash_width_24h"] == {
            "total_packets": 0,
            "single_byte": 0,
            "double_byte": 0,
            "triple_byte": 0,
            "single_byte_pct": 0.0,
            "double_byte_pct": 0.0,
            "triple_byte_pct": 0.0,
        }
        assert result["region_scope_24h"] == {
            "total_messages": 0,
            "scoped_messages": 0,
            "scoped_pct": 0.0,
            "false_positive_floor": 0.0,
            "total_senders": 0,
            "scoped_senders": 0,
            "scoped_senders_pct": 0.0,
        }
        assert result["packets_per_hour_72h"] == []


class TestStatisticsCounts:
    @pytest.mark.asyncio
    async def test_counts_contacts_and_repeaters(self, test_db):
        """Contacts and repeaters are counted separately by type."""
        now = int(time.time())
        conn = test_db.conn
        # type=1 is client, type=2 is repeater
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("aa" * 32, 1, now),
        )
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("bb" * 32, 1, now),
        )
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("cc" * 32, 2, now),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()

        assert result["contact_count"] == 2
        assert result["repeater_count"] == 1

    @pytest.mark.asyncio
    async def test_channel_count(self, test_db):
        conn = test_db.conn
        await conn.execute(
            "INSERT INTO channels (key, name) VALUES (?, ?)",
            ("AA" * 16, "test-chan"),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()
        assert result["channel_count"] == 2  # test-chan + #remoteterm seed

    @pytest.mark.asyncio
    async def test_message_type_counts(self, test_db):
        """DM, channel, and outgoing messages are counted correctly."""
        now = int(time.time())
        conn = test_db.conn
        # 2 DMs, 3 channel messages, 1 outgoing
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES (?, ?, ?, ?, ?)",
            ("PRIV", "aa" * 32, "dm1", now, 0),
        )
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES (?, ?, ?, ?, ?)",
            ("PRIV", "bb" * 32, "dm2", now, 0),
        )
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES (?, ?, ?, ?, ?)",
            ("CHAN", "CC" * 16, "ch1", now, 0),
        )
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES (?, ?, ?, ?, ?)",
            ("CHAN", "CC" * 16, "ch2", now, 0),
        )
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES (?, ?, ?, ?, ?)",
            ("CHAN", "DD" * 16, "ch3", now, 1),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()

        assert result["total_dms"] == 2
        assert result["total_channel_messages"] == 3
        assert result["total_outgoing"] == 1

    @pytest.mark.asyncio
    async def test_packet_split(self, test_db):
        """Packets are split into decrypted and undecrypted."""
        now = int(time.time())
        conn = test_db.conn
        # Insert a message to link to
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
            ("CHAN", "AA" * 16, "msg", now),
        )
        msg_id = (await (await conn.execute("SELECT last_insert_rowid() AS id")).fetchone())["id"]

        # 2 decrypted packets (linked to message), 1 undecrypted
        await conn.execute(
            "INSERT INTO raw_packets (timestamp, data, message_id, payload_hash) VALUES (?, ?, ?, ?)",
            (now, b"\x01", msg_id, b"\x01" * 32),
        )
        await conn.execute(
            "INSERT INTO raw_packets (timestamp, data, message_id, payload_hash) VALUES (?, ?, ?, ?)",
            (now, b"\x02", msg_id, b"\x02" * 32),
        )
        await conn.execute(
            "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
            (now, b"\x03", b"\x03" * 32),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()

        assert result["total_packets"] == 3
        assert result["decrypted_packets"] == 2
        assert result["undecrypted_packets"] == 1


class TestBusiestChannels:
    @pytest.mark.asyncio
    async def test_busiest_channels_returns_top_5(self, test_db):
        """Only the top 5 channels are returned, ordered by message count."""
        now = int(time.time())
        conn = test_db.conn

        # Create 6 channels with varying message counts
        for i in range(6):
            key = f"{i:02X}" * 16
            await conn.execute(
                "INSERT INTO channels (key, name) VALUES (?, ?)",
                (key, f"chan-{i}"),
            )
            for j in range(i + 1):
                await conn.execute(
                    "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
                    ("CHAN", key, f"msg-{j}", now),
                )
        await conn.commit()

        result = await StatisticsRepository.get_all()

        assert len(result["busiest_channels_24h"]) == 5
        # Most messages first
        counts = [ch["message_count"] for ch in result["busiest_channels_24h"]]
        assert counts == sorted(counts, reverse=True)
        assert counts[0] == 6  # channel 5 has 6 messages

    @pytest.mark.asyncio
    async def test_busiest_channels_excludes_old_messages(self, test_db):
        """Messages older than 24h are not counted."""
        now = int(time.time())
        old = now - 90000  # older than 24h
        conn = test_db.conn

        key = "AA" * 16
        await conn.execute("INSERT INTO channels (key, name) VALUES (?, ?)", (key, "old-chan"))
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
            ("CHAN", key, "old-msg", old),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()
        assert result["busiest_channels_24h"] == []

    @pytest.mark.asyncio
    async def test_busiest_channels_shows_key_when_no_channel_name(self, test_db):
        """When channel has no name in channels table, conversation_key is used."""
        now = int(time.time())
        conn = test_db.conn

        key = "FF" * 16
        # Don't insert into channels table
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
            ("CHAN", key, "msg", now),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()
        assert len(result["busiest_channels_24h"]) == 1
        assert result["busiest_channels_24h"][0]["channel_name"] == key


class TestActivityWindows:
    @pytest.mark.asyncio
    async def test_activity_windows(self, test_db):
        """Contacts are bucketed into time windows based on last_seen."""
        now = int(time.time())
        conn = test_db.conn

        # Contact seen 30 min ago (within 1h, 24h, 7d)
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("aa" * 32, 1, now - 1800),
        )
        # Contact seen 12h ago (within 24h, 7d but not 1h)
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("bb" * 32, 1, now - 43200),
        )
        # Contact seen 3 days ago (within 7d but not 1h or 24h)
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("cc" * 32, 1, now - 259200),
        )
        # Contact seen 10 days ago (outside all windows)
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("dd" * 32, 1, now - 864000),
        )
        # Repeater seen 30 min ago
        await conn.execute(
            "INSERT INTO contacts (public_key, type, last_seen) VALUES (?, ?, ?)",
            ("ee" * 32, 2, now - 1800),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()

        assert result["contacts_heard"]["last_hour"] == 1
        assert result["contacts_heard"]["last_24_hours"] == 2
        assert result["contacts_heard"]["last_week"] == 3

        assert result["repeaters_heard"]["last_hour"] == 1
        assert result["repeaters_heard"]["last_24_hours"] == 1
        assert result["repeaters_heard"]["last_week"] == 1

    @pytest.mark.asyncio
    async def test_known_channels_active_windows(self, test_db):
        """Known channels are counted by distinct active keys in each time window."""
        now = int(time.time())
        conn = test_db.conn

        known_1h = "AA" * 16
        known_24h = "BB" * 16
        known_7d = "CC" * 16
        unknown_key = "DD" * 16

        await conn.execute("INSERT INTO channels (key, name) VALUES (?, ?)", (known_1h, "chan-1h"))
        await conn.execute(
            "INSERT INTO channels (key, name) VALUES (?, ?)", (known_24h, "chan-24h")
        )
        await conn.execute("INSERT INTO channels (key, name) VALUES (?, ?)", (known_7d, "chan-7d"))

        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
            ("CHAN", known_1h, "recent-1", now - 1200),
        )
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
            ("CHAN", known_1h, "recent-2", now - 600),
        )
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
            ("CHAN", known_24h, "day-old", now - 43200),
        )
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
            ("CHAN", known_7d, "week-old", now - 259200),
        )
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES (?, ?, ?, ?)",
            ("CHAN", unknown_key, "unknown", now - 600),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()

        assert result["known_channels_active"]["last_hour"] == 1
        assert result["known_channels_active"]["last_24_hours"] == 2
        assert result["known_channels_active"]["last_week"] == 3


class TestPathHashWidthStats:
    @pytest.mark.asyncio
    async def test_counts_last_24h_packets_by_hash_width(self, test_db):
        """Recent raw packets are bucketed by parsed path hash width."""
        now = int(time.time())
        conn = test_db.conn

        packets = [
            (now, bytes.fromhex("0100AA"), b"\x11" * 32),
            (
                now,
                bytes.fromhex(
                    "1540cab3b15626481a5ba64247ab25766e410b026e0678a32da9f0c3946fae5b714cab170f"
                ),
                b"\x22" * 32,
            ),
            (
                now,
                bytes.fromhex("15833fa002860ccae0eed9ca78b9ab0775d477c1f6490a398bf4edc75240"),
                b"\x33" * 32,
            ),
            (now, bytes.fromhex("09C1AABBCC"), b"\x44" * 32),
            (now - 90000, bytes.fromhex("0140AA"), b"\x55" * 32),
        ]

        for timestamp, data, payload_hash in packets:
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
                (timestamp, data, payload_hash),
            )
        await conn.commit()

        result = await StatisticsRepository.get_all()
        breakdown = result["path_hash_width_24h"]

        assert breakdown["total_packets"] == 3
        assert breakdown["single_byte"] == 1
        assert breakdown["double_byte"] == 1
        assert breakdown["triple_byte"] == 1
        assert breakdown["single_byte_pct"] == pytest.approx(100 / 3, rel=1e-3)
        assert breakdown["double_byte_pct"] == pytest.approx(100 / 3, rel=1e-3)
        assert breakdown["triple_byte_pct"] == pytest.approx(100 / 3, rel=1e-3)

    @pytest.mark.asyncio
    async def test_path_hash_width_scan_fetches_all_then_buckets(self, test_db):
        """Hash-width stats should fetchall() then bucket synchronously.

        Uses real DB rows + a patched parser so it exercises the lock-aware
        readonly path. Mocking ``conn.execute`` on the pre-refactor code no
        longer reflects the actual call pattern (we use ``async with``).
        """

        now = int(time.time())
        # Seed three raw packets in the last 24h with arbitrary distinguishing bytes.
        for i, data in enumerate((b"a", b"b", b"c")):
            await test_db.conn.execute(
                "INSERT INTO raw_packets (timestamp, data) VALUES (?, ?)",
                (now - (i + 1), data),
            )
        await test_db.conn.commit()

        def fake_parse(raw_packet: bytes):
            hash_sizes = {
                b"a": 1,
                b"b": 2,
                b"c": 3,
            }
            hash_size = hash_sizes.get(raw_packet)
            if hash_size is None:
                return None
            # The scan is shared with the region-scope bucketer, so the stub has
            # to carry the envelope fields that one reads too. Plain flood
            # GroupText keeps it out of the region counters' way.
            return SimpleNamespace(
                hash_size=hash_size,
                route_type=0x01,
                payload_type=0x05,
                transport_codes=None,
            )

        with patch("app.path_utils.parse_packet_envelope", side_effect=fake_parse):
            breakdown, _region_scope = await StatisticsRepository._packet_shape_24h()

        assert breakdown["total_packets"] == 3
        assert breakdown["single_byte"] == 1
        assert breakdown["double_byte"] == 1
        assert breakdown["triple_byte"] == 1


class TestRegionScopeStats:
    """Regional flood-scope adoption counters.

    Packet fixtures are built by hand so the header bits are explicit:
    header = (payload_type << 2) | route_type, then a 4-byte transport-code
    block for TRANSPORT_* routes, then the packed path byte, then payload.
    """

    # GROUP_TEXT (0x05) flood (0x01) -> header 0x15; path byte 0x00 = 0 hops, 1-byte hashes
    UNSCOPED_GROUP_TEXT = bytes.fromhex("1500AA")
    # GROUP_TEXT (0x05) transport-flood (0x00) -> header 0x14, + codes AABB/0000
    SCOPED_GROUP_TEXT = bytes.fromhex("14AABB000000AA")
    # Undefined payload type 0x0C, transport-flood -> header 0x30. Corrupt by
    # definition, so it feeds the false-positive floor rather than the counts.
    SCOPED_UNDEFINED_TYPE = bytes.fromhex("30AABB000000AA")
    # GROUP_TEXT direct (0x02) -> header 0x16. Direct sends can never be scoped.
    DIRECT_GROUP_TEXT = bytes.fromhex("1600AA")

    async def _insert_packet(self, conn, data: bytes, tag: bytes, timestamp: int):
        await conn.execute(
            "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
            (timestamp, data, tag * 32),
        )

    @pytest.mark.asyncio
    async def test_counts_scoped_flood_group_text_only(self, test_db):
        """Only flood-routed GroupText packets count; direct sends are excluded."""
        now = int(time.time())
        conn = test_db.conn

        await self._insert_packet(conn, self.SCOPED_GROUP_TEXT, b"\x11", now)
        await self._insert_packet(conn, self.UNSCOPED_GROUP_TEXT, b"\x22", now)
        await self._insert_packet(conn, self.DIRECT_GROUP_TEXT, b"\x33", now)
        # Outside the 24h window
        await self._insert_packet(conn, self.SCOPED_GROUP_TEXT, b"\x44", now - 90000)
        await conn.commit()

        stats = (await StatisticsRepository.get_all())["region_scope_24h"]

        # Direct + stale packets excluded, so 2 in the denominator
        assert stats["total_messages"] == 2
        assert stats["scoped_messages"] == 1
        assert stats["scoped_pct"] == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_undefined_payload_types_feed_false_positive_floor(self, test_db):
        """Corrupt packets claiming an undefined type estimate the noise floor."""
        now = int(time.time())
        conn = test_db.conn

        await self._insert_packet(conn, self.UNSCOPED_GROUP_TEXT, b"\x11", now)
        # Three corrupt transport-routed packets across the undefined-type buckets
        for i, header in enumerate(("30", "34", "38")):  # types 0x0C, 0x0D, 0x0E
            await self._insert_packet(
                conn, bytes.fromhex(f"{header}AABB000000AA"), bytes([0x20 + i]), now
            )
        await conn.commit()

        stats = (await StatisticsRepository.get_all())["region_scope_24h"]

        # Garbage must not inflate the real counts...
        assert stats["total_messages"] == 1
        assert stats["scoped_messages"] == 0
        # ...but should surface as the floor: 3 packets over 3 undefined buckets
        assert stats["false_positive_floor"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_counts_distinct_senders_who_scoped(self, test_db):
        """Sender adoption is per distinct sender, not per message."""
        now = int(time.time())
        conn = test_db.conn

        # One chatty scoping sender, one quiet scoping sender, one unscoped sender.
        # Traffic share would read 4/5; sender share should read 2/3.
        rows = [
            ("alice_key", "Alice", 0xAABB),
            ("alice_key", "Alice", 0xAABB),
            ("alice_key", "Alice", 0xAABB),
            ("bob_key", "Bob", 0xCCDD),
            ("carol_key", "Carol", None),
        ]
        for i, (sender_key, sender_name, code) in enumerate(rows):
            await conn.execute(
                """INSERT INTO messages
                   (type, conversation_key, text, received_at, outgoing,
                    sender_key, sender_name, transport_code)
                   VALUES ('CHAN', ?, ?, ?, 0, ?, ?, ?)""",
                ("EE" * 16, f"msg{i}", now, sender_key, sender_name, code),
            )
        await conn.commit()

        stats = (await StatisticsRepository.get_all())["region_scope_24h"]

        assert stats["total_senders"] == 3
        assert stats["scoped_senders"] == 2
        assert stats["scoped_senders_pct"] == pytest.approx(200 / 3, rel=1e-3)

    @pytest.mark.asyncio
    async def test_sender_scoping_falls_back_to_linked_raw_packet(self, test_db):
        """Rows predating region tagging are resolved via their retained packet."""
        now = int(time.time())
        conn = test_db.conn

        async with conn.execute(
            """INSERT INTO messages
               (type, conversation_key, text, received_at, outgoing, sender_key, sender_name)
               VALUES ('CHAN', ?, 'legacy', ?, 0, 'dave_key', 'Dave')""",
            ("EE" * 16, now),
        ) as cursor:
            message_id = cursor.lastrowid
        # transport_code is NULL, but the linked packet still shows the scoping
        await conn.execute(
            """INSERT INTO raw_packets (timestamp, data, payload_hash, message_id)
               VALUES (?, ?, ?, ?)""",
            (now, self.SCOPED_GROUP_TEXT, b"\x99" * 32, message_id),
        )
        await conn.commit()

        stats = (await StatisticsRepository.get_all())["region_scope_24h"]

        assert stats["total_senders"] == 1
        assert stats["scoped_senders"] == 1

    @pytest.mark.asyncio
    async def test_senders_fall_back_to_name_without_resolved_key(self, test_db):
        """sender_key is only ~69% resolved, so name is the fallback identity."""
        now = int(time.time())
        conn = test_db.conn

        for i, code in enumerate((0xAABB, None)):
            await conn.execute(
                """INSERT INTO messages
                   (type, conversation_key, text, received_at, outgoing,
                    sender_key, sender_name, transport_code)
                   VALUES ('CHAN', ?, ?, ?, 0, NULL, ?, ?)""",
                ("EE" * 16, f"msg{i}", now, f"NamedOnly{i}", code),
            )
        # No identity at all -> not counted in either side of the fraction
        await conn.execute(
            """INSERT INTO messages
               (type, conversation_key, text, received_at, outgoing, sender_key, sender_name)
               VALUES ('CHAN', ?, 'anon', ?, 0, NULL, NULL)""",
            ("EE" * 16, now),
        )
        await conn.commit()

        stats = (await StatisticsRepository.get_all())["region_scope_24h"]

        assert stats["total_senders"] == 2
        assert stats["scoped_senders"] == 1

    @pytest.mark.asyncio
    async def test_outgoing_messages_excluded_from_sender_counts(self, test_db):
        """Our own sends are not evidence of anyone else's adoption."""
        now = int(time.time())
        conn = test_db.conn

        await conn.execute(
            """INSERT INTO messages
               (type, conversation_key, text, received_at, outgoing,
                sender_key, sender_name, transport_code)
               VALUES ('CHAN', ?, 'ours', ?, 1, 'me_key', 'Me', ?)""",
            ("EE" * 16, now, 0xAABB),
        )
        await conn.commit()

        stats = (await StatisticsRepository.get_all())["region_scope_24h"]

        assert stats["total_senders"] == 0
        assert stats["scoped_senders"] == 0
        assert stats["scoped_senders_pct"] == 0.0


class TestPacketsPerHour:
    @pytest.mark.asyncio
    async def test_buckets_packets_by_hour(self, test_db):
        """Packets within 72h are bucketed by hour."""
        now = int(time.time())
        hour_start = (now // 3600) * 3600
        conn = test_db.conn

        # 3 packets in the current hour, 1 in the previous hour
        for i in range(3):
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
                (hour_start + i, b"\x01", bytes([i]) * 32),
            )
        await conn.execute(
            "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
            (hour_start - 1800, b"\x02", b"\xaa" * 32),
        )
        # 1 packet outside the 72h window — should be excluded
        await conn.execute(
            "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
            (now - 260000, b"\x03", b"\xbb" * 32),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()
        buckets = result["packets_per_hour_72h"]

        assert len(buckets) == 2
        by_ts = {b["timestamp"]: b["count"] for b in buckets}
        assert by_ts[hour_start] == 3
        assert by_ts[hour_start - 3600] == 1

    @pytest.mark.asyncio
    async def test_empty_when_no_recent_packets(self, test_db):
        """Returns empty list when all packets are older than 72h."""
        now = int(time.time())
        conn = test_db.conn
        await conn.execute(
            "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
            (now - 300000, b"\x01", b"\x01" * 32),
        )
        await conn.commit()

        result = await StatisticsRepository.get_all()
        assert result["packets_per_hour_72h"] == []


class TestStatisticsEndpoint:
    @pytest.mark.asyncio
    async def test_statistics_endpoint_includes_noise_floor_history(self, test_db, client):
        noise_floor_history = {
            "sample_interval_seconds": 60,
            "coverage_seconds": 1800,
            "latest_noise_floor_dbm": -119,
            "latest_timestamp": 1_700_000_000,
            "samples": [
                {"timestamp": 1_699_998_200, "noise_floor_dbm": -121},
                {"timestamp": 1_700_000_000, "noise_floor_dbm": -119},
            ],
        }

        with patch(
            "app.routers.statistics.get_noise_floor_history",
            return_value=noise_floor_history,
        ):
            response = await client.get("/api/statistics")

        assert response.status_code == 200
        payload = response.json()
        assert payload["noise_floor_24h"] == noise_floor_history


class TestMultibyteRollout:
    @pytest.mark.asyncio
    async def test_counts_nodes_by_direct_route_hop_width(self, test_db):
        """Node-level multibyte adoption: counts by direct_path_hash_mode, split by type."""
        conn = test_db.conn
        rows = [
            ("aa" * 32, 1, 1),  # client, 1-byte
            ("bb" * 32, 1, 2),  # client, 2-byte
            ("cc" * 32, 2, 2),  # repeater, 2-byte
            ("dd" * 32, 2, 3),  # repeater, 3-byte
            ("ee" * 32, 2, 1),  # repeater, 1-byte
            ("ff" * 32, 1, None),  # no known route width — excluded
        ]
        for key, contact_type, hash_mode in rows:
            await conn.execute(
                "INSERT INTO contacts (public_key, type, direct_path_hash_mode) VALUES (?, ?, ?)",
                (key, contact_type, hash_mode),
            )
        await conn.commit()

        result = await StatisticsRepository.get_all()
        rollout = result["multibyte_rollout"]

        assert rollout["contacts_with_route"] == 5
        assert rollout["contacts_multibyte"] == 3
        assert rollout["single_byte"] == 2
        assert rollout["double_byte"] == 2
        assert rollout["triple_byte"] == 1
        assert rollout["repeaters_with_route"] == 3
        assert rollout["repeaters_multibyte"] == 2

    @pytest.mark.asyncio
    async def test_empty_rollout(self, test_db):
        result = await StatisticsRepository.get_all()
        assert result["multibyte_rollout"]["contacts_with_route"] == 0
        assert result["multibyte_rollout"]["contacts_multibyte"] == 0
