"""SMS bot for RemoteTerm.

MeshCore commands:
  sms NUMBER MESSAGE
  reply MESSAGE
  smsstatus
  smsroute CODE test
  smsroute CODE bots
  smsroute CODE dm USER

Incoming HTTP:
  GET or POST /api/hooks/sms
  Authentication:
    - X-Hook-Token: <configured webhook_token>, or
    - ?token=<configured webhook_token>

For VoIP.ms DID configuration, use the SMS/MMS URL Callback shown in the
RemoteTerm SMS bot settings. RemoteTerm must be reachable on TCP port 8000.

The incoming JSON payload accepts common field variants:
  id / ID / message_id
  from / FROM / from_number
  to / TO / to_number
  message / MESSAGE / text
  date / timestamp / time

Routing:
- SMS started in a channel -> replies return to that exact channel.
- SMS started in a MeshCore DM -> replies return to that exact DM public key.
- Unknown origin -> queue it and ask for routing in the configured fallback channel.
- A failed private route never leaks the SMS body into a public channel.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from remoteterm import bot

BOT_META = {
    "key": "sms",
    "name": "SMS",
    "category": "Communication",
    "description": "VoIP.ms or Twilio SMS with direct RemoteTerm callback and channel/DM conversation routing",
    "version": "1.5.2",
    "settings_schema": [
        {
            "key": "provider",
            "label": "SMS provider",
            "type": "select",
            "default": "voipms",
            "options": [
                {"value": "voipms", "label": "VoIP.ms"},
                {"value": "twilio", "label": "Twilio"},
            ],
            "help": "Choose the service used for outgoing and incoming SMS.",
        },
        {
            "key": "api_username",
            "label": "VoIP.ms API username / email",
            "type": "text",
            "default": "",
            "help": "VoIP.ms account email used by the REST/JSON API.",
            "show_when": {"key": "provider", "value": "voipms"},
        },
        {
            "key": "api_password",
            "label": "VoIP.ms API password",
            "type": "password",
            "default": "",
            "help": "VoIP.ms API password (not your normal portal password). Create/enable it under SOAP and REST/JSON API.",
            "show_when": {"key": "provider", "value": "voipms"},
        },
        {
            "key": "did",
            "label": "VoIP.ms SMS DID / sender number",
            "type": "text",
            "default": "",
            "help": "10-digit NANPA SMS-capable VoIP.ms DID.",
            "show_when": {"key": "provider", "value": "voipms"},
        },
        {
            "key": "dialing_mode",
            "label": "Dialing mode",
            "type": "select",
            "default": "nanpa",
            "options": [
                {"value": "nanpa", "label": "NANPA (10 digits)"},
                {"value": "e164", "label": "E.164 (+1...)"},
            ],
            "show_when": {"key": "provider", "value": "voipms"},
        },
        {
            "key": "twilio_account_sid",
            "label": "Twilio Account SID",
            "type": "text",
            "default": "",
            "help": "Account SID beginning with AC from the Twilio Console.",
            "show_when": {"key": "provider", "value": "twilio"},
        },
        {
            "key": "twilio_auth_token",
            "label": "Twilio Auth Token",
            "type": "password",
            "default": "",
            "help": "Auth Token from the Twilio Console.",
            "show_when": {"key": "provider", "value": "twilio"},
        },
        {
            "key": "twilio_from_number",
            "label": "Twilio sender number",
            "type": "text",
            "default": "",
            "help": "SMS-capable Twilio number in E.164 format, for example +15145550100.",
            "show_when": {"key": "provider", "value": "twilio"},
        },
        {
            "key": "public_server",
            "label": "Public server IP or domain",
            "type": "text",
            "default": "",
            "help": "Public hostname or IP used to build the direct callback URL on TCP port 8000, for example example.com or 203.0.113.10.",
        },
        {
            "key": "webhook_token",
            "label": "Incoming webhook token",
            "type": "password",
            "default": "",
            "help": "Secret used to protect incoming SMS callbacks. Generate one with: openssl rand -hex 32. The direct callback URL sends it as the token query parameter.",
        },
        {
            "key": "callback_url",
            "label": "VoIP.ms SMS/MMS Callback URL",
            "type": "generated_url",
            "template": "http://{public_server}:8000/api/hooks/sms?token={webhook_token}&to={TO}&from={FROM}&message={MESSAGE}&id={ID}&date={TIMESTAMP}",
            "help": "Enter this URL in the VoIP.ms DID configuration under SMS/MMS URL Callback.",
            "copy_label": "Copy",
            "testable": True,
            "test_label": "Test URL",
            "warning": "⚠ Public Internet Warning: Port 8000 must be open to the Internet for incoming SMS callbacks. Opening port 8000 will also make the RemoteTerm web interface reachable from the public Internet.",
            "show_when": {"key": "provider", "value": "voipms"},
        },
        {
            "key": "twilio_callback_url",
            "label": "Twilio incoming-message webhook URL",
            "type": "generated_url",
            "template": "http://{public_server}:8000/api/hooks/sms?token={webhook_token}",
            "help": "Configure this URL with HTTP POST for the Twilio phone number's incoming messages.",
            "copy_label": "Copy",
            "testable": True,
            "test_label": "Test URL",
            "warning": "⚠ Public Internet Warning: Port 8000 must be open to the Internet for incoming SMS callbacks. Opening port 8000 will also make the RemoteTerm web interface reachable from the public Internet.",
            "show_when": {"key": "provider", "value": "twilio"},
        },
        {
            "key": "fallback_channel",
            "label": "Unrouted SMS channel",
            "type": "text",
            "default": "#test",
            "help": "Where routing prompts are sent when no conversation origin is known.",
        },
        {
            "key": "sms_header",
            "label": "Outgoing SMS header",
            "type": "text",
            "default": "MESHCORE BOT CECREVIER.CA",
            "help": "First line added to every outgoing SMS.",
        },
        {
            "key": "db_path",
            "label": "SMS database path",
            "type": "text",
            "default": "data/sms.db",
        },
        {
            "key": "max_sms_chars",
            "label": "Maximum outgoing SMS characters",
            "type": "number",
            "default": 160,
        },
        {
            "key": "include_mesh_sender",
            "label": "Include MeshCore sender in SMS (legacy)",
            "type": "select",
            "default": "yes",
            "options": [
                {"value": "yes", "label": "Yes"},
                {"value": "no", "label": "No"},
            ],
        },
    ],
    "settings": {
        "provider": "voipms",
        "api_username": "",
        "api_password": "",
        "did": "",
        "dialing_mode": "nanpa",
        "twilio_account_sid": "",
        "twilio_auth_token": "",
        "twilio_from_number": "",
        "public_server": "",
        "webhook_token": "",
        "fallback_channel": "#test",
        "sms_header": "MESHCORE BOT CECREVIER.CA",
        "db_path": "data/sms.db",
        "max_sms_chars": 420,
        "include_mesh_sender": "yes",
    },
}


# ----------------------------- helpers -------------------------------------


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalize_nanpa(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def _display_phone(value: Any) -> str:
    phone = _normalize_nanpa(value)
    if not phone:
        return str(value or "")
    return f"({phone[:3]}) {phone[3:6]}-{phone[6:]}"


def _dial_number(phone: str, mode: str) -> str:
    normalized = _normalize_nanpa(phone)
    if not normalized:
        raise ValueError("invalid phone")
    return f"+1{normalized}" if mode == "e164" else normalized


def _sender_label(msg) -> str:
    return _compact(msg.sender_name or "") or "MeshCore"


def _actor_id(msg) -> str:
    # A DM public key is stable and unambiguous. Channel senders normally do not
    # carry one, so use the normalized sender name there.
    if msg.is_dm and msg.sender_key:
        return f"dm:{str(msg.sender_key).lower()}"
    return f"name:{_sender_label(msg).casefold()}"


def _command_arg(msg, keyword: str) -> str:
    text = str(msg.text or "").strip()
    parts = text.split(None, 1)
    if parts and parts[0].casefold().lstrip("!") == keyword.casefold().lstrip("!"):
        return parts[1].strip() if len(parts) > 1 else ""
    return text


def _db_path(settings: dict[str, Any]) -> Path:
    raw = str(settings.get("db_path", "data/sms.db") or "data/sms.db")
    path = Path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _db(settings: dict[str, Any]) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(settings))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sms_conversations (
            phone TEXT PRIMARY KEY,
            mesh_sender TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            delivery_mode TEXT NOT NULL DEFAULT 'channel',
            channel_name TEXT,
            private_contact_name TEXT,
            private_contact_key TEXT,
            last_outgoing_message TEXT,
            last_incoming_message TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sms_user_last (
            actor_id TEXT PRIMARY KEY,
            phone TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sms_incoming (
            unique_id TEXT PRIMARY KEY,
            provider_id TEXT,
            phone_from TEXT NOT NULL,
            phone_to TEXT,
            message TEXT NOT NULL,
            provider_timestamp TEXT,
            received_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sms_outgoing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id TEXT,
            actor_id TEXT NOT NULL,
            mesh_sender TEXT NOT NULL,
            phone_to TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sms_unrouted (
            route_code TEXT PRIMARY KEY,
            phone TEXT NOT NULL,
            message TEXT NOT NULL,
            routed INTEGER NOT NULL DEFAULT 0,
            routed_to TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            routed_at DATETIME
        );
        """
    )
    conn.commit()
    return conn


def _save_conversation(
    settings: dict[str, Any],
    *,
    phone: str,
    mesh_sender: str,
    actor_id: str,
    delivery_mode: str,
    channel_name: str | None = None,
    private_contact_name: str | None = None,
    private_contact_key: str | None = None,
    outgoing_message: str | None = None,
) -> None:
    normalized_phone = _normalize_nanpa(phone)
    if not normalized_phone:
        return

    mode = "private" if delivery_mode == "private" else "channel"
    conn = _db(settings)
    try:
        conn.execute(
            """
            INSERT INTO sms_conversations (
                phone, mesh_sender, actor_id, delivery_mode, channel_name,
                private_contact_name, private_contact_key,
                last_outgoing_message, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(phone) DO UPDATE SET
                mesh_sender=excluded.mesh_sender,
                actor_id=excluded.actor_id,
                delivery_mode=excluded.delivery_mode,
                channel_name=excluded.channel_name,
                private_contact_name=excluded.private_contact_name,
                private_contact_key=excluded.private_contact_key,
                last_outgoing_message=COALESCE(
                    excluded.last_outgoing_message,
                    sms_conversations.last_outgoing_message
                ),
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                normalized_phone,
                mesh_sender,
                actor_id,
                mode,
                channel_name,
                private_contact_name,
                private_contact_key,
                outgoing_message,
            ),
        )
        conn.execute(
            """
            INSERT INTO sms_user_last (actor_id, phone, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(actor_id) DO UPDATE SET
                phone=excluded.phone,
                updated_at=CURRENT_TIMESTAMP
            """,
            (actor_id, normalized_phone),
        )
        conn.commit()
    finally:
        conn.close()


def _get_conversation(settings: dict[str, Any], phone: str) -> dict[str, Any] | None:
    normalized_phone = _normalize_nanpa(phone)
    if not normalized_phone:
        return None
    conn = _db(settings)
    try:
        row = conn.execute(
            "SELECT * FROM sms_conversations WHERE phone=? LIMIT 1",
            (normalized_phone,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_last_for_actor(settings: dict[str, Any], actor_id: str) -> dict[str, Any] | None:
    conn = _db(settings)
    try:
        row = conn.execute(
            "SELECT * FROM sms_user_last WHERE actor_id=? LIMIT 1",
            (actor_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _save_outgoing(
    settings: dict[str, Any],
    provider_id: str,
    actor_id: str,
    mesh_sender: str,
    phone: str,
    message: str,
    status: str,
    error: str = "",
) -> None:
    conn = _db(settings)
    try:
        conn.execute(
            """
            INSERT INTO sms_outgoing (
                provider_id, actor_id, mesh_sender, phone_to,
                message, status, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (provider_id, actor_id, mesh_sender, phone, message, status, error),
        )
        conn.commit()
    finally:
        conn.close()


def _save_incoming(
    settings: dict[str, Any],
    unique_id: str,
    provider_id: str,
    phone_from: str,
    phone_to: str,
    message: str,
    timestamp: str,
) -> bool:
    """Return True only when this is a new inbound SMS."""
    conn = _db(settings)
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO sms_incoming (
                unique_id, provider_id, phone_from, phone_to,
                message, provider_timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (unique_id, provider_id, phone_from, phone_to, message, timestamp),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _update_incoming_conversation(
    settings: dict[str, Any],
    phone: str,
    message: str,
) -> None:
    conn = _db(settings)
    try:
        conn.execute(
            """
            UPDATE sms_conversations
            SET last_incoming_message=?, updated_at=CURRENT_TIMESTAMP
            WHERE phone=?
            """,
            (message, phone),
        )
        conn.commit()
    finally:
        conn.close()


def _queue_unrouted(settings: dict[str, Any], phone: str, message: str) -> str:
    route_code = secrets.token_hex(3).upper()
    conn = _db(settings)
    try:
        conn.execute(
            "INSERT INTO sms_unrouted (route_code, phone, message) VALUES (?, ?, ?)",
            (route_code, phone, message),
        )
        conn.commit()
    finally:
        conn.close()
    return route_code


def _get_unrouted(settings: dict[str, Any], code: str) -> dict[str, Any] | None:
    conn = _db(settings)
    try:
        row = conn.execute(
            """
            SELECT * FROM sms_unrouted
            WHERE route_code=? AND routed=0
            LIMIT 1
            """,
            (str(code or "").strip().upper(),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _mark_routed(settings: dict[str, Any], code: str, routed_to: str) -> None:
    conn = _db(settings)
    try:
        conn.execute(
            """
            UPDATE sms_unrouted
            SET routed=1, routed_to=?, routed_at=CURRENT_TIMESTAMP
            WHERE route_code=?
            """,
            (routed_to, str(code or "").strip().upper()),
        )
        conn.commit()
    finally:
        conn.close()


def _unique_id(payload: dict[str, Any], phone: str, to: str, message: str, timestamp: str) -> str:
    provider_id = str(
        payload.get("id")
        or payload.get("ID")
        or payload.get("message_id")
        or payload.get("sms_id")
        or ""
    ).strip()
    if provider_id:
        return f"sms:{provider_id}"

    raw = f"{phone}|{to}|{message}|{timestamp}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _max_chars(settings: dict[str, Any]) -> int:
    try:
        value = int(settings.get("max_sms_chars", 160) or 160)
    except (TypeError, ValueError):
        value = 160
    return max(40, min(value, 420))


def _sender_label_from_value(value: Any) -> str:
    label = _compact(value)
    return label or "MESHCORE"


def _format_outgoing(settings: dict[str, Any], sender: str, message: str) -> str:
    """Keep the original MeshCore SMS presentation, including line breaks."""
    clean_message = str(message or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    clean_message = "\n".join(
        re.sub(r"[ \t]+", " ", line).strip() for line in clean_message.split("\n")
    ).strip()

    header = str(
        settings.get("sms_header", "MESHCORE BOT CECREVIER.CA") or "MESHCORE BOT CECREVIER.CA"
    ).strip()
    user = _sender_label_from_value(sender).upper()

    body = f"{header}\nUSER: {user}\nMESSAGE: {clean_message}"
    return body[: _max_chars(settings)]


# ----------------------------- SMS API -------------------------------------


def _voipms_request(settings: dict[str, Any], destination: str, message: str) -> dict[str, Any]:
    """Blocking HTTP request. Internal errors are logged/stored, never exposed over RF."""
    username = str(settings.get("api_username", "") or "").strip()
    password = str(settings.get("api_password", "") or "").strip()
    did = _normalize_nanpa(settings.get("did", ""))
    dst = _normalize_nanpa(destination)

    if not username or not password or not did:
        return {"ok": False, "error": "SMS API configuration incomplete"}
    if not dst:
        return {"ok": False, "error": "invalid destination"}

    mode = str(settings.get("dialing_mode", "nanpa") or "nanpa").casefold()
    if mode not in {"nanpa", "e164"}:
        mode = "nanpa"

    params = {
        "api_username": username,
        "api_password": password,
        "method": "sendSMS",
        "did": _dial_number(did, mode),
        "dst": _dial_number(dst, mode),
        "message": message,
        "content_type": "json",
    }

    # Provider endpoint intentionally remains internal to the bot implementation.
    url = "https://voip.ms/api/v1/rest.php?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "RemoteTerm-SMS/1.0", "Accept": "application/json"},
    )

    try:
        # Bot handlers have a 10-second execution ceiling. VoIP.ms can take
        # longer than eight seconds to return its acceptance JSON, so leave
        # only the small amount of headroom needed for persistence and reply.
        with urllib.request.urlopen(request, timeout=9) as response:
            raw = response.read()
            status_code = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status_code = int(exc.code)
    except Exception as exc:
        # The request may already have been accepted before the connection
        # failed or timed out. Retrying here could send a duplicate SMS.
        return {
            "ok": False,
            "uncertain": True,
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        # A 2xx response with an unreadable body also leaves delivery unknown.
        return {
            "ok": False,
            "uncertain": 200 <= status_code < 300,
            "error": f"{type(exc).__name__}: {exc}",
        }

    status = str(data.get("status") or "").strip().casefold() if isinstance(data, dict) else ""
    if 200 <= status_code < 300 and status == "success":
        provider_id = str(data.get("sms") or data.get("id") or data.get("message_id") or "")
        # sendSMS confirms API acceptance, not handset delivery.  VoIP.ms uses
        # "success" for that acknowledgement, so expose the lifecycle meaning
        # instead of treating a missing message id as missing confirmation.
        return {
            "ok": True,
            "provider": "voipms",
            "id": provider_id,
            "status": "accepted",
            "confirmation": "VoIP.ms accepted the message",
        }

    return {
        "ok": False,
        "error": str(
            data.get("error") or data.get("message") or data.get("status") or "request rejected"
        )
        if isinstance(data, dict)
        else "invalid response",
    }


def _twilio_request(settings: dict[str, Any], destination: str, message: str) -> dict[str, Any]:
    """Send one SMS through Twilio's Messages REST resource."""
    account_sid = str(settings.get("twilio_account_sid", "") or "").strip()
    auth_token = str(settings.get("twilio_auth_token", "") or "").strip()
    from_phone = _normalize_nanpa(settings.get("twilio_from_number", ""))
    dst = _normalize_nanpa(destination)

    if not account_sid or not auth_token or not from_phone:
        return {"ok": False, "error": "Twilio API configuration incomplete"}
    if not dst:
        return {"ok": False, "error": "invalid destination"}

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    body = urllib.parse.urlencode(
        {"From": f"+1{from_phone}", "To": f"+1{dst}", "Body": message}
    ).encode("utf-8")
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode("ascii")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "RemoteTerm-SMS/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read()
            status_code = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status_code = int(exc.code)
    except Exception as exc:
        return {"ok": False, "uncertain": True, "error": f"{type(exc).__name__}: {exc}"}

    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        return {
            "ok": False,
            "uncertain": 200 <= status_code < 300,
            "error": f"{type(exc).__name__}: {exc}",
        }

    status = str(data.get("status") or "").strip().casefold() if isinstance(data, dict) else ""
    failed_statuses = {"failed", "undelivered", "canceled"}
    if 200 <= status_code < 300 and isinstance(data, dict) and data.get("sid"):
        if status in failed_statuses:
            return {
                "ok": False,
                "error": str(data.get("error_message") or data.get("error_code") or status),
            }
        # A create response normally says queued (or accepted when a Messaging
        # Service chooses the sender).  Final delivery arrives asynchronously.
        initial_status = status or "accepted"
        return {
            "ok": True,
            "provider": "twilio",
            "id": str(data["sid"]),
            "status": initial_status,
            "confirmation": f"Twilio {initial_status} the message",
        }
    return {
        "ok": False,
        "error": str(
            data.get("message")
            or data.get("error_message")
            or data.get("code")
            or data.get("error_code")
            or data.get("status")
            or "request rejected"
        )
        if isinstance(data, dict)
        else "invalid response",
    }


def _provider_request(settings: dict[str, Any], destination: str, message: str) -> dict[str, Any]:
    provider = str(settings.get("provider", "voipms") or "voipms").strip().casefold()
    if provider == "twilio":
        return _twilio_request(settings, destination, message)
    if provider == "voipms":
        return _voipms_request(settings, destination, message)
    return {"ok": False, "error": f"unsupported SMS provider: {provider}"}


async def _send_sms(ctx, msg, destination: str, message: str) -> None:
    phone = _normalize_nanpa(destination)
    if not phone:
        await ctx.reply("📱 Numéro invalide.")
        return

    clean = _compact(message)
    if not clean:
        await ctx.reply("📱 Message vide.")
        return

    sender = _sender_label(msg)
    actor_id = _actor_id(msg)
    outgoing = _format_outgoing(ctx.settings, sender, clean)

    result = await asyncio.to_thread(
        _provider_request,
        ctx.settings,
        phone,
        outgoing,
    )

    if not result.get("ok"):
        internal_error = str(result.get("error") or "unknown error")
        uncertain = bool(result.get("uncertain"))
        log_summary = "SMS confirmation unavailable" if uncertain else "SMS send failed"
        ctx.log(f"{log_summary}: {internal_error}", level="WARNING")
        await asyncio.to_thread(
            _save_outgoing,
            ctx.settings,
            "",
            actor_id,
            sender,
            phone,
            clean,
            "unknown" if uncertain else "error",
            internal_error,
        )
        # Keep provider/API details private on MeshCore.
        if uncertain:
            await ctx.reply("📱 SMS status unknown — provider confirmation unavailable.")
        else:
            await ctx.reply("📱 SMS service unavailable. Please try again later.")
        return

    if msg.is_dm:
        delivery_mode = "private"
        channel_name = None
        private_key = str(msg.sender_key or "").strip().lower()
        private_name = sender
    else:
        delivery_mode = "channel"
        channel_name = str(msg.channel_name or "").strip()
        private_key = None
        private_name = None

    provider_status = str(result.get("status") or "accepted")
    await asyncio.to_thread(
        _save_conversation,
        ctx.settings,
        phone=phone,
        mesh_sender=sender,
        actor_id=actor_id,
        delivery_mode=delivery_mode,
        channel_name=channel_name,
        private_contact_name=private_name,
        private_contact_key=private_key,
        outgoing_message=clean,
    )
    await asyncio.to_thread(
        _save_outgoing,
        ctx.settings,
        str(result.get("id") or ""),
        actor_id,
        sender,
        phone,
        clean,
        provider_status,
        "",
    )

    confirmation = str(result.get("confirmation") or f"SMS {provider_status}")
    provider_id = str(result.get("id") or "")
    reference = f" | {provider_id}" if provider_id else ""
    await ctx.reply(f"📱 {confirmation} ✅ | {_display_phone(phone)}{reference}")


# ----------------------------- commands ------------------------------------


@bot.on_keyword("sms")
async def sms(ctx, msg):
    arg = _command_arg(msg, "sms")
    parts = arg.split(None, 1)

    if len(parts) < 2:
        await ctx.reply("📱 Usage: sms NUMERO message")
        return

    await _send_sms(ctx, msg, parts[0], parts[1])


@bot.on_keyword("reply")
async def sms_reply(ctx, msg):
    arg = _command_arg(msg, "reply")

    if not arg:
        await ctx.reply("📱 Usage: reply message")
        return

    last = await asyncio.to_thread(
        _get_last_for_actor,
        ctx.settings,
        _actor_id(msg),
    )
    if not last:
        await ctx.reply("📱 Aucune conversation SMS active.")
        return

    await _send_sms(ctx, msg, str(last["phone"]), arg)


@bot.on_keyword("smsstatus")
async def sms_status(ctx, msg):
    last = await asyncio.to_thread(
        _get_last_for_actor,
        ctx.settings,
        _actor_id(msg),
    )

    if not last:
        await ctx.reply("📱 Aucune conversation SMS active.")
        return

    await ctx.reply(f"📱 Conversation avec {_display_phone(last['phone'])}")


async def _contact_by_name(name: str):
    from app.repository import ContactRepository

    contacts = await ContactRepository.get_by_name(name)
    return contacts


@bot.on_keyword("smsroute")
async def sms_route(ctx, msg):
    arg = _command_arg(msg, "smsroute")
    parts = arg.split()

    if len(parts) < 2:
        await ctx.reply("📱 Usage: smsroute CODE dm USER | test | bots")
        return

    code = parts[0].upper()
    pending = await asyncio.to_thread(_get_unrouted, ctx.settings, code)

    if not pending:
        await ctx.reply(f"📱 SMS {code} introuvable ou déjà routé.")
        return

    destination = parts[1].casefold()
    phone = str(pending["phone"])
    message = str(pending["message"])

    if destination in {"test", "bots"}:
        channel = f"#{destination}"
        try:
            await ctx.send(channel, f"📱 SMS {_display_phone(phone)}: {message}")
        except ValueError:
            await ctx.reply(f"📱 Canal {channel} indisponible.")
            return

        actor = _actor_id(msg)
        sender = _sender_label(msg)
        await asyncio.to_thread(
            _save_conversation,
            ctx.settings,
            phone=phone,
            mesh_sender=sender,
            actor_id=actor,
            delivery_mode="channel",
            channel_name=channel,
        )
        await asyncio.to_thread(_mark_routed, ctx.settings, code, f"channel:{channel}")
        await ctx.reply(f"📱 SMS {code} routé vers {channel} ✅")
        return

    if destination in {"dm", "private", "prive", "privé"}:
        if len(parts) < 3:
            await ctx.reply(f"📱 Usage: smsroute {code} dm USER")
            return

        target_name = _compact(" ".join(parts[2:]))
        contacts = await _contact_by_name(target_name)

        if len(contacts) != 1:
            await ctx.reply("📱 Contact introuvable ou nom ambigu.")
            return

        contact = contacts[0]
        public_key = str(contact.public_key or "").strip().lower()
        if not public_key:
            await ctx.reply("📱 Contact sans clé publique.")
            return

        await ctx.send_dm(
            public_key,
            f"📱 SMS {_display_phone(phone)}: {message}",
        )

        await asyncio.to_thread(
            _save_conversation,
            ctx.settings,
            phone=phone,
            mesh_sender=target_name,
            actor_id=f"dm:{public_key}",
            delivery_mode="private",
            private_contact_name=target_name,
            private_contact_key=public_key,
        )
        await asyncio.to_thread(_mark_routed, ctx.settings, code, f"dm:{public_key}")
        await ctx.reply(f"📱 SMS {code} routé en privé ✅")
        return

    await ctx.reply("📱 Choix: dm USER | test | bots")


# ----------------------------- inbound webhook -----------------------------


def _payload_value(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


async def _notify_unrouted(ctx, code: str, phone: str, reason: str, preview: str | None) -> None:
    channel = str(ctx.settings.get("fallback_channel", "#test") or "#test").strip()
    if not channel.startswith("#") and len(channel) != 32:
        channel = f"#{channel}"

    if preview is not None:
        await ctx.send(
            channel,
            f"📱 Nouveau SMS {code} de {_display_phone(phone)}: {preview[:55]}",
        )

    await ctx.send(
        channel,
        f"📍 SMS {code} en attente ({reason}). smsroute {code} dm USER | test | bots",
    )


@bot.on_webhook("sms")
async def incoming_sms(ctx, payload):
    """Receive an incoming SMS JSON payload and route it back to MeshCore."""
    if not isinstance(payload, dict):
        ctx.log("SMS webhook: payload must be a JSON object", level="WARNING")
        return

    # VoIP.ms can arrive either as a flat JSON object or through its
    # 3CX-compatible webhook envelope. Support both.
    event_type = ""
    source = payload

    data = payload.get("data")
    if isinstance(data, dict):
        event_type = str(data.get("event_type") or "").strip().casefold()
        nested = data.get("payload")
        if isinstance(nested, dict):
            source = nested

    if event_type and event_type not in {"message.received", "message_received", "received"}:
        return

    raw_from = (source.get("from") or source.get("From")) if isinstance(source, dict) else None
    if isinstance(raw_from, dict):
        raw_from = raw_from.get("phone_number") or raw_from.get("number")

    raw_to = (source.get("to") or source.get("To")) if isinstance(source, dict) else None
    if isinstance(raw_to, list) and raw_to:
        first_to = raw_to[0]
        if isinstance(first_to, dict):
            raw_to = first_to.get("phone_number") or first_to.get("number")
        else:
            raw_to = first_to
    elif isinstance(raw_to, dict):
        raw_to = raw_to.get("phone_number") or raw_to.get("number")

    phone = _normalize_nanpa(
        raw_from
        or _payload_value(source, "FROM", "From", "from_number", "sender")
        or _payload_value(payload, "from", "FROM", "From", "from_number", "sender")
    )
    to = str(
        raw_to
        or _payload_value(source, "TO", "To", "to_number", "did")
        or _payload_value(payload, "to", "TO", "To", "to_number", "did")
        or ""
    ).strip()
    message = _payload_value(
        source, "message", "MESSAGE", "text", "body", "Body"
    ) or _payload_value(payload, "message", "MESSAGE", "text", "body", "Body")
    timestamp = _payload_value(
        source, "received_at", "date", "timestamp", "time"
    ) or _payload_value(payload, "occurred_at", "date", "timestamp", "time")
    provider_id = (
        _payload_value(source, "id", "ID", "message_id", "sms_id", "MessageSid", "SmsSid")
        or _payload_value(payload, "id", "ID", "message_id", "sms_id", "MessageSid", "SmsSid")
        or (str(data.get("id") or "").strip() if isinstance(data, dict) else "")
    )

    if not phone:
        ctx.log("SMS webhook: invalid sender number", level="WARNING")
        return
    if not message:
        ctx.log("SMS webhook: empty message", level="WARNING")
        return

    unique_id = _unique_id(payload, phone, to, message, timestamp)
    is_new = await asyncio.to_thread(
        _save_incoming,
        ctx.settings,
        unique_id,
        provider_id,
        phone,
        to,
        message,
        timestamp,
    )
    if not is_new:
        return

    conversation = await asyncio.to_thread(
        _get_conversation,
        ctx.settings,
        phone,
    )

    if conversation:
        await asyncio.to_thread(
            _update_incoming_conversation,
            ctx.settings,
            phone,
            message,
        )

        mode = str(conversation.get("delivery_mode") or "channel").casefold()

        if mode == "private":
            public_key = str(conversation.get("private_contact_key") or "").strip().lower()

            if public_key:
                try:
                    await ctx.send_dm(
                        public_key,
                        f"📱 SMS {_display_phone(phone)}: {message}",
                    )
                    return
                except Exception as exc:
                    # Never leak the SMS body to a public fallback channel.
                    ctx.log(
                        f"SMS private route failed: {type(exc).__name__}: {exc}",
                        level="WARNING",
                    )

            code = await asyncio.to_thread(
                _queue_unrouted,
                ctx.settings,
                phone,
                message,
            )
            await _notify_unrouted(
                ctx,
                code,
                phone,
                "DM d'origine introuvable",
                None,
            )
            return

        channel = str(conversation.get("channel_name") or "").strip()
        if channel:
            try:
                await ctx.send(
                    channel,
                    f"📱 SMS {_display_phone(phone)} → "
                    f"{conversation.get('mesh_sender') or 'MeshCore'}: {message}",
                )
                return
            except Exception as exc:
                ctx.log(
                    f"SMS channel route failed: {type(exc).__name__}: {exc}",
                    level="WARNING",
                )

        code = await asyncio.to_thread(
            _queue_unrouted,
            ctx.settings,
            phone,
            message,
        )
        await _notify_unrouted(
            ctx,
            code,
            phone,
            "canal d'origine absent",
            None,
        )
        return

    # No previous MeshCore-originated SMS exists for this phone: never guess.
    code = await asyncio.to_thread(
        _queue_unrouted,
        ctx.settings,
        phone,
        message,
    )
    await _notify_unrouted(
        ctx,
        code,
        phone,
        "aucune route connue",
        message,
    )
