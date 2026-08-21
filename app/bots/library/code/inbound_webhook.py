"""Inbound HTTP-to-mesh bridge: POST /api/hooks/send relays a message.

Payload: ``{"channel": "#name-or-32-hex-key", "message": "..."}`` for a
channel send, or ``{"dm_to": "<64-hex public key>", "message": "..."}`` for
a direct message.
"""

from remoteterm import bot

BOT_META = {
    "key": "inbound_webhook",
    "name": "inbound webhook",
    "category": "Custom",
    "description": "POST /api/hooks/send relays a message to a channel or DM",
    "version": "1.0.0",
    "settings_schema": [
        {
            "key": "webhook_token",
            "label": "Webhook token",
            "type": "password",
            "default": "",
            "help": "Required — requests must send X-Hook-Token",
        }
    ],
    "settings": {"webhook_token": ""},
}

_MAX_MESSAGE_CHARS = 400


def _is_hex(value, length):
    return len(value) == length and all(c in "0123456789abcdefABCDEF" for c in value)


@bot.on_webhook("send")
async def deliver(ctx, payload):
    # Authentication happens upstream: the HTTP router compares X-Hook-Token
    # against the webhook_token setting and rejects the request before this
    # handler ever runs — no token handling is needed here.
    if not isinstance(payload, dict):
        ctx.log("hook /send: payload must be a JSON object", level="WARNING")
        return
    message = str(payload.get("message") or "").strip()
    if not message:
        ctx.log("hook /send: missing 'message'", level="WARNING")
        return
    if len(message) > _MAX_MESSAGE_CHARS:
        ctx.log(
            f"hook /send: message too long ({len(message)} > {_MAX_MESSAGE_CHARS})",
            level="WARNING",
        )
        return

    dm_to = str(payload.get("dm_to") or "").strip()
    channel = str(payload.get("channel") or "").strip()
    if dm_to:
        if not _is_hex(dm_to, 64):
            ctx.log("hook /send: 'dm_to' must be a 64-hex public key", level="WARNING")
            return
        await ctx.send_dm(dm_to, message)
        ctx.log(f"hook /send: delivered {len(message)} chars via DM to {dm_to[:12]}")
        return
    if not channel:
        ctx.log("hook /send: need 'channel' or 'dm_to'", level="WARNING")
        return
    try:
        await ctx.send(channel, message)
    except ValueError:
        ctx.log(f"hook /send: unknown channel {channel!r}", level="WARNING")
        return
    ctx.log(f"hook /send: delivered {len(message)} chars to {channel}")
