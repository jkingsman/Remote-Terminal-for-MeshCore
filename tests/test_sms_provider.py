from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from app.bots.library import get_library_entry
from app.bots.runtime import load_bot_code


def _provider_request(settings, destination, message):
    entry = get_library_entry("sms")
    assert entry is not None
    namespace = load_bot_code(entry["code"]).namespace
    return namespace["_provider_request"](settings, destination, message)


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

    with patch("urllib.request.urlopen", return_value=response):
        result = _provider_request(_voipms_settings(), "4385550100", "hello")

    assert result == {
        "ok": True,
        "provider": "voipms",
        "id": "abc123",
        "status": "accepted",
        "confirmation": "VoIP.ms accepted the message",
    }


def test_voipms_success_without_id_is_still_confirmed_as_accepted():
    response = _response(200, b'{"status":"success"}')

    with patch("urllib.request.urlopen", return_value=response):
        result = _provider_request(_voipms_settings(), "4385550100", "hello")

    assert result["ok"] is True
    assert result["status"] == "accepted"
    assert result["confirmation"] == "VoIP.ms accepted the message"


def test_twilio_success_returns_queued_confirmation():
    response = _response(201, b'{"sid":"SM123","status":"queued"}')

    with patch("urllib.request.urlopen", return_value=response):
        result = _provider_request(_twilio_settings(), "4385550100", "hello")

    assert result == {
        "ok": True,
        "provider": "twilio",
        "id": "SM123",
        "status": "queued",
        "confirmation": "Twilio queued the message",
    }


def test_twilio_http_rejection_reports_provider_error_not_unknown():
    error = HTTPError("https://api.twilio.test", 400, "Bad Request", {}, None)
    error.read = MagicMock(
        return_value=b'{"code":21211,"message":"The To phone number is not valid"}'
    )

    with patch("urllib.request.urlopen", side_effect=error):
        result = _provider_request(_twilio_settings(), "4385550100", "hello")

    assert result == {"ok": False, "error": "The To phone number is not valid"}
    assert "uncertain" not in result
