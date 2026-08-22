"""Tests for the trickier ported library bots (alert, mowas, worldcup-live, mesh admin)."""

import asyncio
import base64
import hashlib
import json
import os
import time

from app.bots.api import BotContext, BotMessage
from app.bots.engine import BotEngine
from app.bots.library import get_library_entry
from app.bots.runtime import load_bot_code
from app.models import BotTestRequest


def _load_namespace(key: str) -> dict:
    entry = get_library_entry(key)
    assert entry is not None, f"library bot {key} missing"
    return load_bot_code(entry["code"]).namespace


class TestPulsePointDecrypt:
    def test_roundtrip_against_reference_scheme(self):
        """Encrypt a sample payload the way PulsePoint does; the bot must decrypt it."""
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad

        ns = _load_namespace("alert")
        derive_key = ns["_derive_key"]
        decrypt = ns["_decrypt"]

        payload = {"incidents": {"active": [{"ID": "1", "PulsePointIncidentCallType": "SF"}]}}
        # PulsePoint wraps the JSON in quotes with escaped inner quotes.
        plaintext = '"' + json.dumps(payload).replace('"', '\\"') + '"'
        salt = os.urandom(8)
        iv = os.urandom(16)
        cipher = AES.new(derive_key(salt), AES.MODE_CBC, iv)
        ciphertext = cipher.encrypt(pad(plaintext.encode(), 16))

        envelope = {
            "ct": base64.b64encode(ciphertext).decode(),
            "iv": iv.hex(),
            "s": salt.hex(),
        }
        assert decrypt(envelope) == payload

    def test_key_derivation_is_deterministic(self):
        ns = _load_namespace("alert")
        derive_key = ns["_derive_key"]
        salt = bytes.fromhex("0011223344556677")
        key = derive_key(salt)
        assert len(key) == 32
        assert key == derive_key(salt)
        # EVP_BytesToKey with MD5: first block is md5(password + salt).
        e = "CommonIncidents"
        password = e[13] + e[1] + e[2] + "brady" + "5" + "r" + e.lower()[6] + e[5] + "gs"
        assert key[:16] == hashlib.md5(password.encode() + salt).digest()


class TestMowasExtraction:
    def test_cap_json_shape(self):
        extract = _load_namespace("mowas")["_extract"]
        result = extract(
            {
                "info": [
                    {"language": "de-DE", "headline": "Unwetterwarnung Stufe Rot"},
                    {"language": "en-US", "headline": "Severe weather warning"},
                ]
            }
        )
        assert result == [
            ("de-de", "Unwetterwarnung Stufe Rot"),
            ("en-us", "Severe weather warning"),
        ]

    def test_cap_xml_shape(self):
        extract = _load_namespace("mowas")["_extract"]
        xml = (
            '<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">'
            "<info><language>de-DE</language><headline>Bombenfund</headline>"
            "<description>Evakuierung</description></info></alert>"
        )
        result = extract({"cap_xml": xml})
        assert result == [("de-de", "Bombenfund")]

    def test_simple_shape_and_garbage(self):
        extract = _load_namespace("mowas")["_extract"]
        assert extract({"headline": "Warnung"}) == [("de", "Warnung")]
        assert extract({}) == []
        assert extract({"cap_xml": "<not-xml"}) == []


class TestWorldcupSummarize:
    def test_summarize_event(self):
        summarize = _load_namespace("worldcup_live")["_summarize"]
        event = {
            "id": "401",
            "status": {"type": {"state": "in"}},
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "score": "2", "team": {"abbreviation": "ARG"}},
                        {"homeAway": "away", "score": "1", "team": {"abbreviation": "FRA"}},
                    ]
                }
            ],
        }
        assert summarize(event) == ("401", "ARG 2-1 FRA", "in")
        assert summarize({"competitions": []}) is None


class TestMultitest:
    async def test_counts_only_the_window_and_collapses_flood_stages(self, test_db):
        conn = test_db.conn
        now = int(time.time())

        # Outside the window — must not be counted (the old code returned the
        # newest 100 rows of all time because after= was ignored without
        # after_id=).
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, paths, outgoing)"
            " VALUES ('CHAN', ?, 'old traffic', ?, ?, 0)",
            ("AA" * 16, now - 3600, json.dumps([{"path": "beef", "received_at": now - 3600}])),
        )
        # In the window: one message heard at three flood stages of the same
        # route (each repeat appends one hop) plus one genuinely distinct route.
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, paths, outgoing)"
            " VALUES ('CHAN', ?, 'test one', ?, ?, 0)",
            (
                "AA" * 16,
                now + 5,
                json.dumps(
                    [
                        {"path": "2f52", "received_at": now + 5, "path_len": 2},
                        {"path": "2f52f0", "received_at": now + 6, "path_len": 3},
                        {"path": "2f52f0bf", "received_at": now + 7, "path_len": 4},
                        {"path": "aabb", "received_at": now + 6, "path_len": 2},
                    ]
                ),
            ),
        )
        # In the window: a zero-hop (direct) arrival.
        await conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing)"
            " VALUES ('CHAN', ?, 'test two', ?, 0)",
            ("AA" * 16, now + 5),
        )
        await conn.commit()

        entry = get_library_entry("multitest")
        assert entry is not None
        loaded = load_bot_code(entry["code"])
        loaded.namespace["WINDOW_SECONDS"] = 0  # skip the collection sleep
        handler = loaded.collector.keywords[0].handler

        ctx = BotContext(
            bot_id="mt",
            bot_name="multitest",
            settings={},
            state={},
            is_test=True,
            loop=asyncio.get_event_loop(),
            origin_is_dm=True,
            origin_sender_key="ab" * 32,
        )
        await handler(ctx, BotMessage(text="multitest", is_dm=True, sender_key="ab" * 32))

        assert len(ctx.captured_sends) == 1
        text = ctx.captured_sends[0]["text"]
        # 2f52 and 2f52f0 are flood stages of 2f52f0bf; aabb and direct stand.
        # Routes render as comma-separated repeater hops (1-byte hops here).
        assert text.startswith("3 unique path(s)")
        assert "2f,52,f0,bf" in text
        assert "aa,bb" in text
        assert "direct" in text
        assert "2f,52 |" not in text and "2f,52,f0 |" not in text
        assert "be,ef" not in text and "beef" not in text


class TestMeshAdminBots:
    async def _test_run(self, test_db, key: str, request: BotTestRequest):
        from app.repository.bots import BotRepository

        entry = get_library_entry(key)
        assert entry is not None
        bot = await BotRepository.create(name=f"{key}-porttest", code=entry["code"])
        engine = BotEngine()
        return await engine.test_run(bot, request)

    async def test_status_requires_dm(self, test_db):
        response = await self._test_run(test_db, "status", BotTestRequest(text="status"))
        assert response.matched
        assert response.replies, response.error
        assert "direct message" in response.replies[0]["text"]

    async def test_status_reports_in_dm(self, test_db):
        response = await self._test_run(
            test_db, "status", BotTestRequest(text="status", is_dm=True, sender_key="ab" * 32)
        )
        assert response.error is None
        assert any("Radio" in r["text"] for r in response.replies)
        assert any("Bot engine" in r["text"] for r in response.replies)

    async def test_neighbors_lists_zero_hop(self, test_db):
        conn = test_db.conn
        await conn.execute(
            "INSERT INTO contacts (public_key, name, type, direct_path_len, last_seen)"
            " VALUES (?, ?, ?, ?, strftime('%s','now'))",
            ("aa" * 32, "NearNode", 1, 0),
        )
        await conn.execute(
            "INSERT INTO contacts (public_key, name, type, direct_path_len) VALUES (?, ?, ?, ?)",
            ("bb" * 32, "FarNode", 1, 3),
        )
        await conn.commit()
        response = await self._test_run(test_db, "neighbors", BotTestRequest(text="neighbors"))
        assert response.error is None
        text = response.replies[0]["text"]
        assert "NearNode" in text
        assert "FarNode" not in text

    async def test_repeater_stats(self, test_db):
        conn = test_db.conn
        await conn.execute(
            "INSERT INTO contacts (public_key, name, type, direct_path_len,"
            " direct_path_hash_mode, last_seen) VALUES (?, ?, 2, 0, 2, strftime('%s','now'))",
            ("cc" * 32, "Repeater One"),
        )
        await conn.commit()
        response = await self._test_run(
            test_db,
            "repeater",
            BotTestRequest(text="repeater stats", is_dm=True, sender_key="ab" * 32),
        )
        assert response.error is None
        text = response.replies[0]["text"]
        assert "1 known" in text
        assert "1 multibyte" in text

    async def test_trace_is_guarded_in_test_runs(self, test_db):
        response = await self._test_run(
            test_db, "trace", BotTestRequest(text="trace a1b2", is_dm=True)
        )
        assert response.error is None
        assert "not transmitted" in response.replies[0]["text"]
