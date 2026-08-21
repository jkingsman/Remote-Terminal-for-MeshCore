"""Tests for the trickier ported library bots (alert, mowas, worldcup-live, mesh admin)."""

import base64
import hashlib
import json
import os

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
