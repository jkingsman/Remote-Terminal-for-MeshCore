"""German MoWaS civil-protection warnings via inbound webhook.

Seeded from meshcore-bot's DARC MoWaS service. A MoWaS relay POSTs warnings to
``/api/hooks/mowas`` (token-gated by this bot's ``webhook_token`` setting);
each is forwarded to the configured channel(s).

Accepted payload shapes:
- CAP-JSON: {"info": [{"headline": ..., "description": ..., "language": "de-DE"}], ...}
- Simple:   {"headline": ..., "description": ...}
- CAP XML:  {"cap_xml": "<alert ...>"} (urn:oasis CAP 1.2)
"""

import xml.etree.ElementTree as ElementTree

from remoteterm import bot

BOT_META = {
    "key": "mowas",
    "name": "mowas",
    "category": "Alerts",
    "description": "Forwards MoWaS civil-protection warnings from a webhook to channels",
    "version": "1.0.0",
    "respond_to_dms": False,
    "settings_schema": [
        {
            "key": "webhook_token",
            "label": "Webhook token",
            "type": "password",
            "default": "",
            "help": "Required — POST /api/hooks/mowas must send X-Hook-Token.",
        },
        {
            "key": "channel_de",
            "label": "German warnings channel",
            "type": "text",
            "default": "",
        },
        {
            "key": "channel_en",
            "label": "English warnings channel",
            "type": "text",
            "default": "",
        },
    ],
    "settings": {"webhook_token": "", "channel_de": "", "channel_en": ""},
}

_CAP_NS = "{urn:oasis:names:tc:emergency:cap:1.2}"


def _extract(payload: dict) -> list[tuple[str, str]]:
    """[(language, message)] from any accepted payload shape."""
    out: list[tuple[str, str]] = []

    cap_xml = payload.get("cap_xml")
    if isinstance(cap_xml, str) and cap_xml.strip():
        try:
            root = ElementTree.fromstring(cap_xml)
        except ElementTree.ParseError:
            return []
        for info in root.iter(f"{_CAP_NS}info"):
            language = (info.findtext(f"{_CAP_NS}language") or "de").strip()
            headline = (info.findtext(f"{_CAP_NS}headline") or "").strip()
            description = (info.findtext(f"{_CAP_NS}description") or "").strip()
            text = headline or description
            if text:
                out.append((language.lower(), text[:170]))
        return out

    infos = payload.get("info")
    if isinstance(infos, list):
        for info in infos:
            if not isinstance(info, dict):
                continue
            language = str(info.get("language", "de")).strip()
            text = str(info.get("headline") or info.get("description") or "").strip()
            if text:
                out.append((language.lower(), text[:170]))
        return out

    text = str(payload.get("headline") or payload.get("description") or "").strip()
    if text:
        out.append(("de", text[:170]))
    return out


@bot.on_webhook("mowas")
async def receive(ctx, payload):
    channel_de = str(ctx.settings.get("channel_de", "") or "").strip()
    channel_en = str(ctx.settings.get("channel_en", "") or "").strip()
    if not channel_de and not channel_en:
        ctx.log("MoWaS warning received but no channel configured", level="WARNING")
        return

    warnings = _extract(payload if isinstance(payload, dict) else {})
    if not warnings:
        ctx.log("MoWaS payload had no extractable warning", level="WARNING")
        return

    sent = 0
    for language, text in warnings[:2]:
        target = channel_en if language.startswith("en") and channel_en else channel_de
        if not target:
            continue
        await ctx.send(target, f"MoWaS: {text}")
        sent += 1
    ctx.log(f"forwarded {sent} MoWaS warning(s)")
