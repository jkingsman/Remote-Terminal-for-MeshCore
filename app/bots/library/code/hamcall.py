"""HAMCALL lookup bot for RemoteTerm.

Commands:
  hamcall CALLSIGN
  hamcall                -> tries to extract a callsign from sender name
  hamcall add CALLSIGN NOM VILLE

Lookup order:
  1) HamDB public API
  2) local SQLite fallback
  3) API timeout/errors are never reported as "not found"
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from remoteterm import bot


BOT_META = {
    "key": "hamcall",
    "name": "hamcall",
    "category": "Utility",
    "description": "Lookup amateur-radio callsigns with a local fallback database",
    "version": "1.0.0",
    "settings_schema": [
        {
            "key": "db_path",
            "label": "HAMCALL local database path",
            "type": "text",
            "default": "data/hamcall.db",
        },
        {
            "key": "api_app_name",
            "label": "HamDB application name",
            "type": "text",
            "default": "RemoteTerm",
        },
        {
            "key": "timeout_seconds",
            "label": "API timeout (seconds)",
            "type": "number",
            "default": 10,
        },
    ],
    "settings": {
        "db_path": "data/hamcall.db",
        "api_app_name": "RemoteTerm",
        "timeout_seconds": 10,
    },
}


def _compact_spaces(value: Any) -> str:
    return " ".join(str(value or "").split())


def _fix_utf8_mojibake(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""

    if any(marker in text for marker in ("Ã", "Â", "â€™", "â€œ", "â€", "ðŸ")):
        try:
            return text.encode("latin-1").decode("utf-8")
        except Exception:
            pass

    return text


def _extract_callsign(value: Any) -> str | None:
    """Extract a callsign from a callsign or MeshCore display name."""
    raw = str(value or "").upper().strip()
    if not raw:
        return None

    callsign_re = r"[A-Z]{1,2}\d[A-Z]{1,4}\d?"

    direct = re.search(
        rf"(?<![A-Z0-9])({callsign_re})(?![A-Z0-9])",
        raw,
    )
    if direct:
        return direct.group(1)

    tokens = [token for token in re.split(r"[^A-Z0-9]+", raw) if token]

    # Handles display names such as "VE2 ABC".
    for start in range(len(tokens)):
        for width in (2, 3):
            candidate = "".join(tokens[start:start + width])
            if re.fullmatch(callsign_re, candidate):
                return candidate

    compact = re.sub(r"[^A-Z0-9]", "", raw)

    # Prefer common Canadian prefixes when embedded in a longer username.
    canadian = re.search(r"((?:VA|VE|VO|VY)\d[A-Z]{1,4}\d?)", compact)
    if canadian:
        return canadian.group(1)

    embedded = re.search(rf"({callsign_re})", compact)
    return embedded.group(1) if embedded else None


def _db_path(settings: dict[str, Any]) -> Path:
    raw = str(settings.get("db_path", "data/hamcall.db") or "data/hamcall.db")
    path = Path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _db(settings: dict[str, Any]) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(settings))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hamcall_local (
            callsign TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            city TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    return conn


def _get_local(settings: dict[str, Any], callsign: str) -> dict[str, Any] | None:
    conn = _db(settings)
    try:
        row = conn.execute(
            """
            SELECT callsign, username, city, updated_at
            FROM hamcall_local
            WHERE callsign = ?
            LIMIT 1
            """,
            (callsign.upper(),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _save_local(
    settings: dict[str, Any],
    callsign: str,
    username: str,
    city: str,
) -> None:
    callsign = str(callsign or "").strip().upper()
    username = _compact_spaces(username)
    city = _compact_spaces(city)

    if not callsign or not username or not city:
        raise ValueError("Incomplete HAMCALL data")

    conn = _db(settings)
    try:
        conn.execute(
            """
            INSERT INTO hamcall_local (
                callsign, username, city, updated_at
            )
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(callsign) DO UPDATE SET
                username = excluded.username,
                city = excluded.city,
                updated_at = CURRENT_TIMESTAMP
            """,
            (callsign, username, city),
        )
        conn.commit()
    finally:
        conn.close()


def _request_hamdb(url: str, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "RemoteTerm-HAMCALL/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return int(response.status), response.read()


async def _lookup(
    settings: dict[str, Any],
    callsign: str,
) -> tuple[str | None, str]:
    """API first, then local fallback. Timeout != not-found."""
    extracted = _extract_callsign(callsign)
    if not extracted:
        return None, "Aucun indicatif radio trouvé."

    callsign = extracted
    app_name = str(settings.get("api_app_name", "RemoteTerm") or "RemoteTerm").strip()
    app_name = re.sub(r"[^A-Za-z0-9_.-]", "", app_name) or "RemoteTerm"

    try:
        timeout = float(settings.get("timeout_seconds", 10) or 10)
    except (TypeError, ValueError):
        timeout = 10.0
    timeout = max(1.0, min(timeout, 30.0))

    url = f"https://api.hamdb.org/{callsign}/json/{app_name}"
    api_state = "unavailable"

    try:
        status_code, body = await asyncio.to_thread(_request_hamdb, url, timeout)

        if status_code == 200:
            data = json.loads(body.decode("utf-8", errors="replace"))
            hamdb = data.get("hamdb") if isinstance(data, dict) else None

            if isinstance(hamdb, dict):
                messages = hamdb.get("messages") or {}
                status = str(messages.get("status") or "").upper()

                if status == "NOT_FOUND":
                    api_state = "not_found"
                else:
                    callsign_data = hamdb.get("callsign") or {}
                    if isinstance(callsign_data, dict):
                        call = str(
                            callsign_data.get("call") or callsign
                        ).strip().upper()

                        fname = _fix_utf8_mojibake(
                            callsign_data.get("fname")
                            or callsign_data.get("first_name")
                            or ""
                        ).strip()

                        lname = _fix_utf8_mojibake(
                            callsign_data.get("name")
                            or callsign_data.get("last_name")
                            or ""
                        ).strip()

                        city = _fix_utf8_mojibake(
                            callsign_data.get("addr2")
                            or callsign_data.get("city")
                            or ""
                        ).strip()

                        state = _fix_utf8_mojibake(
                            callsign_data.get("state")
                            or callsign_data.get("province")
                            or ""
                        ).strip()

                        person = fname or lname
                        location = ", ".join(
                            part for part in (city, state) if part
                        )

                        parts = [call]
                        if person:
                            parts.append(person)
                        if location:
                            parts.append(location)

                        result = " ".join(parts).strip()

                        if result and result != call:
                            return result[:128], ""

                        api_state = "not_found"

    except urllib.error.HTTPError as exc:
        # A normal HTTP failure is an unavailable API, unless HamDB returned
        # a parsable NOT_FOUND response above.
        if exc.code == 404:
            api_state = "not_found"
    except (TimeoutError, OSError, urllib.error.URLError, json.JSONDecodeError):
        api_state = "unavailable"
    except Exception:
        api_state = "unavailable"

    local = await asyncio.to_thread(_get_local, settings, callsign)

    if local:
        username = _fix_utf8_mojibake(
            local.get("username") or callsign
        ).strip()
        city = _fix_utf8_mojibake(local.get("city") or "").strip()

        result = f"{callsign} {username}"
        if city:
            result += f" {city}"

        return result[:128], ""

    if api_state == "not_found":
        return None, f"{callsign} introuvable."

    return None, "Service HAMCALL temporairement indisponible."


def _command_argument(msg) -> str:
    """Return text after the hamcall keyword."""
    text = str(msg.text or "").strip()
    parts = text.split(None, 1)
    if parts and parts[0].lower() == "hamcall":
        return parts[1].strip() if len(parts) > 1 else ""
    return text


@bot.on_keyword("hamcall")
async def hamcall(ctx, msg):
    arg = _command_argument(msg)

    # hamcall add CALLSIGN NOM VILLE
    if arg.lower().startswith("add "):
        parts = arg.split(None, 3)

        if len(parts) < 4:
            await ctx.reply("📻 Usage: hamcall add CALLSIGN NOM VILLE")
            return

        callsign = _extract_callsign(parts[1])
        name = _compact_spaces(parts[2])
        city = _compact_spaces(parts[3])

        if not callsign:
            await ctx.reply("📻 Indicatif invalide.")
            return

        if not name or not city:
            await ctx.reply("📻 Usage: hamcall add CALLSIGN NOM VILLE")
            return

        try:
            await asyncio.to_thread(
                _save_local,
                ctx.settings,
                callsign,
                name,
                city,
            )
        except Exception as exc:
            ctx.log(
                f"HAMCALL local DB error: {type(exc).__name__}: {exc}",
                level="WARNING",
            )
            await ctx.reply("📻 Impossible d'enregistrer l'entrée.")
            return

        await ctx.reply(f"📻 {callsign} enregistré: {name} | {city}")
        return

    # Without an explicit argument, try extracting the sender's callsign.
    raw_lookup = arg or (msg.sender_name or "")
    callsign = _extract_callsign(raw_lookup)

    if not callsign:
        await ctx.reply("📻 Aucun indicatif trouvé. Usage: hamcall CALLSIGN")
        return

    result, error = await _lookup(ctx.settings, callsign)

    if result:
        await ctx.reply(f"📻 {result}")
        return

    if error == f"{callsign} introuvable.":
        await ctx.reply(
            f"📻 {callsign} introuvable | Ajout: hamcall add CALLSIGN NOM VILLE"
        )
        return

    await ctx.reply(f"📻 {error}")

