"""Alerts when a newly heard repeater collides on a routing prefix.

Seeded from meshcore-bot's RepeaterPrefixCollision service. Fires on the
new-contact mesh event; when the new node is a repeater whose first path byte
matches an existing repeater's, posts a warning to the configured channel.
"""

import time

from remoteterm import bot

BOT_META = {
    "key": "prefix_collision",
    "name": "prefix-collision",
    "category": "Alerts",
    "description": "Warns a channel when a new repeater collides on a 1-byte prefix",
    "version": "1.0.0",
    "respond_to_dms": False,
    "settings_schema": [
        {
            "key": "channel",
            "label": "Alert channel",
            "type": "text",
            "default": "",
            "help": "Channel name (e.g. #repeaters). Empty disables sending.",
        },
        {
            "key": "cooldown_minutes",
            "label": "Cooldown per prefix (minutes)",
            "type": "int",
            "default": 60,
            "min": 1,
            "max": 1440,
        },
    ],
    "settings": {"channel": "", "cooldown_minutes": 60},
}


@bot.on_event("new_contact")
async def on_new_contact(ctx, event):
    if event.get("type") != 2:  # repeaters only
        return
    channel = str(ctx.settings.get("channel", "") or "").strip()
    if not channel:
        ctx.log("new repeater heard but no alert channel configured")
        return

    new_key = str(event.get("public_key", "")).lower()
    if len(new_key) < 2:
        return
    prefix = new_key[:2]

    # Read-only lookup against this app's own contact table (allowed for
    # mesh-introspection bots).
    from app.repository import ContactRepository

    contacts = await ContactRepository.get_all(limit=2000)
    others = [
        c
        for c in contacts
        if c.type == 2
        and c.public_key.lower() != new_key
        and c.public_key.lower().startswith(prefix)
    ]
    if not others:
        return

    cooldowns = ctx.state.setdefault("prefix_cooldowns", {})
    now = int(time.time())
    cooldown_seconds = int(ctx.settings.get("cooldown_minutes", 60)) * 60
    if now - int(cooldowns.get(prefix, 0)) < cooldown_seconds:
        return
    cooldowns[prefix] = now

    new_name = event.get("name") or new_key[:8]
    existing = ", ".join((c.name or c.public_key[:8]) for c in others[:3])
    await ctx.send(
        channel,
        f"Prefix collision: new repeater {new_name} shares prefix {prefix} with {existing}",
    )
