"""Active emergency incidents via PulsePoint. Seeded from meshcore-bot's alert command.

PulsePoint's web API returns an encrypted envelope; the key-derivation and
AES-CBC decrypt below are ported from meshcore-bot (community
reverse-engineering of their web app). Requires the agency id(s) covering your
area — find them at web.pulsepoint.org.
"""

import base64
import hashlib
import json
from datetime import UTC, datetime

from remoteterm import bot

BOT_META = {
    "key": "alert",
    "name": "alert",
    "category": "Alerts",
    "description": "Active emergency incidents from PulsePoint (needs agency ids)",
    "version": "1.0.0",
    "cooldown_seconds": 30,
    "settings_schema": [
        {
            "key": "agency_ids",
            "label": "PulsePoint agency ids",
            "type": "text",
            "default": "",
            "help": "Comma-separated, e.g. EMS1384 — find yours at web.pulsepoint.org.",
        },
        {
            "key": "max_age_hours",
            "label": "Ignore incidents older than (hours)",
            "type": "int",
            "default": 24,
            "min": 1,
            "max": 168,
        },
    ],
    "settings": {"agency_ids": "", "max_age_hours": 24},
}

_URL = "https://api.pulsepoint.org/v1/webapp"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin": "https://web.pulsepoint.org",
    "Referer": "https://web.pulsepoint.org/",
}

# Common PulsePoint incident call types (subset).
CALL_TYPES = {
    "ME": "Medical", "TC": "Traffic Collision", "SF": "Structure Fire",
    "VF": "Vehicle Fire", "WF": "Wildfire", "OF": "Outside Fire", "GAS": "Gas Leak",
    "HMR": "Hazmat", "RES": "Rescue", "WR": "Water Rescue", "AC": "Aircraft Crash",
    "PA": "Public Assist", "AL": "Alarm", "EX": "Explosion", "FA": "Fire Alarm",
}  # fmt: skip


def _derive_key(salt: bytes) -> bytes:
    """OpenSSL EVP_BytesToKey-style MD5 KDF with PulsePoint's obfuscated password."""
    e = "CommonIncidents"
    password = e[13] + e[1] + e[2] + "brady" + "5" + "r" + e.lower()[6] + e[5] + "gs"
    key = b""
    block = b""
    while len(key) < 32:
        hasher = hashlib.md5()
        if block:
            hasher.update(block)
        hasher.update(password.encode())
        hasher.update(salt)
        block = hasher.digest()
        key += block
    return key[:32]


def _decrypt(envelope: dict) -> dict:
    # Uses the app's pycryptodome dependency (allowed for library bots).
    from Crypto.Cipher import AES

    ciphertext = base64.b64decode(envelope["ct"])
    iv = bytes.fromhex(envelope["iv"])
    salt = bytes.fromhex(envelope["s"])
    cipher = AES.new(_derive_key(salt), AES.MODE_CBC, iv)
    out = cipher.decrypt(ciphertext)
    text = out[1 : out.rindex(b'"')].decode()
    return json.loads(text.replace(r"\"", '"'))


def _incident_age_ok(incident: dict, max_age_hours: int) -> bool:
    raw = incident.get("CallReceivedDateTime")
    if not raw:
        return True
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (datetime.now(UTC) - when).total_seconds() <= max_age_hours * 3600


@bot.on_keyword("alert", "alerts", "incidents")
async def active_incidents(ctx, msg):
    agencies = str(ctx.settings.get("agency_ids", "") or "").strip()
    if not agencies:
        await ctx.reply("Set the PulsePoint agency ids in this bot's settings.")
        return

    try:  # upstream/envelope failures (ctx.http owns the client)
        envelope = await ctx.http.get_json(
            f"{_URL}?resource=incidents&agencyid={agencies}", headers=_HEADERS
        )
        data = _decrypt(envelope)
    except Exception as exc:
        ctx.log(f"PulsePoint fetch failed: {exc}", level="WARNING")
        await ctx.reply(ctx.t("rt.error_upstream"))
        return

    max_age = int(ctx.settings.get("max_age_hours", 24))
    active = [
        incident
        for incident in data.get("incidents", {}).get("active", []) or []
        if _incident_age_ok(incident, max_age)
    ]
    if not active:
        await ctx.reply("No active incidents.")
        return

    lines = []
    for incident in active[:3]:
        call_type = CALL_TYPES.get(str(incident.get("PulsePointIncidentCallType", "")), "Incident")
        address = str(incident.get("FullDisplayAddress") or incident.get("AgencyID") or "")
        lines.append(f"{call_type}: {address}"[:80])
    more = f" (+{len(active) - 3} more)" if len(active) > 3 else ""
    await ctx.reply(f"{len(active)} active: " + " | ".join(lines) + more)
