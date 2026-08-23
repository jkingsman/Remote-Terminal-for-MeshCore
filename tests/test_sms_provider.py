import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch
from urllib.error import HTTPError

import pytest

from app.bots.library import get_library_entry
from app.bots.runtime import load_bot_code


def _provider_request(settings, destination, message):
    entry = get_library_entry("sms")
    assert entry is not None
    namespace = load_bot_code(entry["code"]).namespace
    return namespace["_provider_request"](settings, destination, message)


def _sms_namespace():
    entry = get_library_entry("sms")
    assert entry is not None
    return load_bot_code(entry["code"]).namespace


def _voipms_settings():
    return {
        "provider": "voipms",
        "api_username": "user@example.com",
        "api_password": "secret",
        "did": "5145550100",
    }


def _twilio_settings():
    return {
        "provider": "twilio",
        "twilio_account_sid": "AC123",
        "twilio_auth_token": "token",
        "twilio_from_number": "+15145550100",
    }


def _response(status, body):
    response = MagicMock()
    response.status = status
    response.read.return_value = body
    response.__enter__.return_value = response
    return response


def test_voipms_success_returns_accepted_confirmation():
    response = _response(200, b'{"status":"success","sms":"abc123"}')

    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        result = _provider_request(_voipms_settings(), "4385550100", "hello")

    assert result == {
        "ok": True,
        "provider": "voipms",
        "id": "abc123",
        "status": "accepted",
        "confirmation": "VoIP.ms accepted the message",
    }

    assert urlopen.call_args.kwargs["timeout"] == 9


def test_voipms_success_without_id_is_still_confirmed_as_accepted():
    response = _response(200, b'{"status":"success"}')

    with patch("urllib.request.urlopen", return_value=response):
        result = _provider_request(_voipms_settings(), "4385550100", "hello")

    assert result["ok"] is True
    assert result["status"] == "accepted"
    assert result["confirmation"] == "VoIP.ms accepted the message"


def test_twilio_success_returns_queued_confirmation_and_uses_messages_api():
    response = _response(201, b'{"sid":"SM123","status":"queued"}')

    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        result = _provider_request(_twilio_settings(), "4385550100", "hello")

    assert result == {
        "ok": True,
        "provider": "twilio",
        "id": "SM123",
        "status": "queued",
        "confirmation": "Twilio queued the message",
    }
    sent_request = urlopen.call_args.args[0]
    assert sent_request.full_url.endswith("/Accounts/AC123/Messages.json")
    assert sent_request.get_method() == "POST"
    assert sent_request.get_header("Authorization") == "Basic QUMxMjM6dG9rZW4="
    assert sent_request.data == b"From=%2B15145550100&To=%2B14385550100&Body=hello"


def test_twilio_http_rejection_reports_provider_error_not_unknown():
    error = HTTPError("https://api.twilio.test", 400, "Bad Request", {}, None)
    error.read = MagicMock(
        return_value=b'{"code":21211,"message":"The To phone number is not valid"}'
    )

    with patch("urllib.request.urlopen", side_effect=error):
        result = _provider_request(_twilio_settings(), "4385550100", "hello")

    assert result == {"ok": False, "error": "The To phone number is not valid"}
    assert "uncertain" not in result


async def test_success_result_persists_and_replies_with_provider_confirmation(tmp_path):
    namespace = _sms_namespace()

    async def run_sync(function, *args, **kwargs):
        return function(*args, **kwargs)

    ctx = SimpleNamespace(
        settings={"db_path": str(tmp_path / "sms.db")},
        reply=AsyncMock(),
        log=MagicMock(),
    )
    msg = SimpleNamespace(
        is_dm=True,
        sender_key="a" * 64,
        sender_name="Mesh User",
        channel_name=None,
    )
    result = {
        "ok": True,
        "provider": "voipms",
        "id": "provider-123",
        "status": "accepted",
        "confirmation": "VoIP.ms accepted the message",
    }

    with patch.dict(
        namespace["_send_sms"].__globals__,
        {
            "asyncio": SimpleNamespace(
                to_thread=run_sync, create_task=asyncio.create_task, shield=asyncio.shield
            ),
            "_provider_request": MagicMock(return_value=result),
        },
    ):
        await namespace["_send_sms"](ctx, msg, "5145550101", "hello")

    conn = namespace["_db"](ctx.settings)
    try:
        row = conn.execute("SELECT provider_id, status, error FROM sms_outgoing").fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("provider-123", "accepted", "")
    assert ctx.reply.await_args_list == [
        call("📱 VoIP.ms accepted the message ✅ | (514) 555-0101 | provider-123")
    ]
    assert all("status unknown" not in args[0] for args, _kwargs in ctx.reply.await_args_list)


def _inbound_ctx(namespace, tmp_path):
    return SimpleNamespace(
        settings={"db_path": str(tmp_path / "sms.db"), "fallback_channel": "#test"},
        send=AsyncMock(),
        send_dm=AsyncMock(),
        log=MagicMock(),
        split_text=lambda text: [text],
    )


async def test_twilio_provider_id_controls_inbound_dedup(tmp_path):
    namespace = _sms_namespace()
    ctx = _inbound_ctx(namespace, tmp_path)
    first = {"From": "+15145550100", "Body": "same body", "MessageSid": "SM-one"}
    second = {**first, "MessageSid": "SM-two"}

    async def run_sync(function, *args, **kwargs):
        return function(*args, **kwargs)

    with patch.dict(
        namespace["incoming_sms"].__globals__,
        {"asyncio": SimpleNamespace(to_thread=run_sync, shield=asyncio.shield)},
    ):
        await namespace["incoming_sms"](ctx, first)
        await namespace["incoming_sms"](ctx, second)
        await namespace["incoming_sms"](ctx, second)

    conn = namespace["_db"](ctx.settings)
    try:
        rows = conn.execute(
            "SELECT provider_id, state FROM sms_incoming ORDER BY provider_id"
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(row) for row in rows] == [("SM-one", "complete"), ("SM-two", "complete")]
    assert ctx.send.await_count == 4  # preview + routing prompt for each unique provider message


async def test_failed_inbound_route_is_reclaimable_on_provider_retry(tmp_path):
    namespace = _sms_namespace()
    ctx = _inbound_ctx(namespace, tmp_path)
    namespace["_save_conversation"](
        ctx.settings,
        phone="5145550100",
        mesh_sender="Mesh User",
        actor_id="name:mesh user",
        delivery_mode="channel",
        channel_name="#origin",
    )
    ctx.send.side_effect = [TimeoutError("TX busy"), TimeoutError("fallback busy"), None]
    payload = {"From": "+15145550100", "Body": "retry me", "MessageSid": "SM-retry"}

    async def run_sync(function, *args, **kwargs):
        return function(*args, **kwargs)

    with patch.dict(
        namespace["incoming_sms"].__globals__,
        {"asyncio": SimpleNamespace(to_thread=run_sync, shield=asyncio.shield)},
    ):
        with pytest.raises(TimeoutError, match="fallback busy"):
            await namespace["incoming_sms"](ctx, payload)
        await namespace["incoming_sms"](ctx, payload)

    conn = namespace["_db"](ctx.settings)
    try:
        rows = conn.execute("SELECT unique_id, state FROM sms_incoming").fetchall()
    finally:
        conn.close()
    assert [tuple(row) for row in rows] == [("sms:SM-retry", "complete")]
    assert "retry me" in ctx.send.await_args_list[-1].args[1]


async def test_failed_private_route_never_leaks_body_to_fallback_channel(tmp_path):
    namespace = _sms_namespace()
    ctx = _inbound_ctx(namespace, tmp_path)
    namespace["_save_conversation"](
        ctx.settings,
        phone="5145550100",
        mesh_sender="Private User",
        actor_id=f"dm:{'a' * 64}",
        delivery_mode="private",
        private_contact_name="Private User",
        private_contact_key="a" * 64,
    )
    ctx.send_dm.side_effect = TimeoutError("private TX failed")
    payload = {
        "From": "+15145550100",
        "Body": "PRIVATE BODY",
        "MessageSid": "SM-private",
    }

    async def run_sync(function, *args, **kwargs):
        return function(*args, **kwargs)

    with patch.dict(
        namespace["incoming_sms"].__globals__,
        {"asyncio": SimpleNamespace(to_thread=run_sync, shield=asyncio.shield)},
    ):
        await namespace["incoming_sms"](ctx, payload)

    assert ctx.send.await_count == 1
    assert "PRIVATE BODY" not in ctx.send.await_args.args[1]


async def test_slow_success_continues_persistence_after_handler_cancellation(tmp_path):
    namespace = _sms_namespace()
    ctx = SimpleNamespace(
        settings={"db_path": str(tmp_path / "sms.db")}, reply=AsyncMock(), log=MagicMock()
    )
    msg = SimpleNamespace(
        is_dm=True, sender_key="a" * 64, sender_name="Mesh User", channel_name=None
    )

    def slow_success(*_args):
        return {
            "ok": True,
            "provider": "voipms",
            "id": "slow-accepted",
            "status": "accepted",
            "confirmation": "VoIP.ms accepted the message",
        }

    async def delayed_to_thread(function, *args, **kwargs):
        if function is slow_success:
            await asyncio.sleep(0.05)
        return function(*args, **kwargs)

    fake_asyncio = SimpleNamespace(
        to_thread=delayed_to_thread, create_task=asyncio.create_task, shield=asyncio.shield
    )
    with patch.dict(
        namespace["_send_sms"].__globals__,
        {"asyncio": fake_asyncio, "_provider_request": slow_success},
    ):
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                namespace["_send_sms"](ctx, msg, "5145550101", "hello"), timeout=0.01
            )
        await asyncio.sleep(0.1)

    conn = namespace["_db"](ctx.settings)
    try:
        row = conn.execute("SELECT provider_id, status FROM sms_outgoing").fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("slow-accepted", "accepted")
    ctx.reply.assert_awaited_once()
