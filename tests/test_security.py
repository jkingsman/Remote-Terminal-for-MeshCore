"""Tests for optional app-wide HTTP Basic authentication."""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from app.config import Settings
from app.security import add_optional_basic_auth_middleware


def _auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _build_app(*, username: str = "", password: str = "") -> FastAPI:
    settings = Settings(
        serial_port="",
        tcp_host="",
        ble_address="",
        basic_auth_username=username,
        basic_auth_password=password,
    )
    app = FastAPI()
    add_optional_basic_auth_middleware(app, settings)

    @app.get("/protected")
    async def protected():
        return {"ok": True}

    @app.get("/api/hooks/sms")
    async def sms_hook():
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    return app


def test_http_request_is_denied_without_basic_auth_credentials():
    app = _build_app(username="mesh", password="secret")

    with TestClient(app) as client:
        response = client.get("/protected")

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
    assert response.headers["www-authenticate"] == 'Basic realm="RemoteTerm", charset="UTF-8"'
    assert response.headers["cache-control"] == "no-store"


def test_http_request_is_allowed_with_valid_basic_auth_credentials():
    app = _build_app(username="mesh", password="secret")

    with TestClient(app) as client:
        response = client.get("/protected", headers=_auth_header("mesh", "secret"))

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_sms_hook_bypasses_basic_auth():
    app = _build_app(username="mesh", password="secret")

    with TestClient(app) as client:
        response = client.get("/api/hooks/sms")

    assert response.status_code == 200


def test_basic_auth_bypass_does_not_match_other_hook_paths():
    app = _build_app(username="mesh", password="secret")

    with TestClient(app) as client:
        assert client.get("/api/hooks/other").status_code == 401
        assert client.get("/api/hooks/sms/extra").status_code == 401


def test_http_request_accepts_case_insensitive_basic_auth_scheme():
    app = _build_app(username="mesh", password="secret")
    header = _auth_header("mesh", "secret")
    header["Authorization"] = header["Authorization"].replace("Basic", "basic")

    with TestClient(app) as client:
        response = client.get("/protected", headers=header)

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_websocket_handshake_is_denied_without_basic_auth_credentials():
    app = _build_app(username="mesh", password="secret")

    with TestClient(app) as client:
        with pytest.raises(WebSocketDenialResponse) as exc_info:
            with client.websocket_connect("/ws"):
                pass

    response = exc_info.value
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
    assert response.headers["www-authenticate"] == 'Basic realm="RemoteTerm", charset="UTF-8"'


def test_websocket_handshake_is_allowed_with_valid_basic_auth_credentials():
    app = _build_app(username="mesh", password="secret")

    with TestClient(app) as client:
        with client.websocket_connect("/ws", headers=_auth_header("mesh", "secret")) as websocket:
            assert websocket.receive_json() == {"ok": True}
