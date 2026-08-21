"""Admin megaphone: relay an announcement to any channel (or back here)."""

from remoteterm import bot

BOT_META = {
    "key": "announcements",
    "name": "announcements",
    "category": "Admin",
    "description": "Admin-only: announce <#channel|here> <text>",
    "version": "1.0.0",
    "admin_only": True,
    "settings_schema": [
        {
            "key": "max_length",
            "label": "Maximum announcement length",
            "type": "int",
            "default": 170,
            "min": 1,
            "max": 400,
        }
    ],
    "settings": {"max_length": 170},
}

USAGE = "usage: announce <#channel> <text> — or: announce here <text>"


@bot.on_keyword("announce")
async def announce(ctx, msg):
    if len(msg.args) < 2:
        await ctx.reply(USAGE)
        return
    target = msg.args[0]
    text = " ".join(msg.args[1:]).strip()
    max_length = int(ctx.settings.get("max_length", 170))
    if len(text) > max_length:
        await ctx.reply(f"too long: {len(text)} chars (max {max_length})")
        return
    if target.lower() == "here":
        await ctx.reply(text)
        return
    try:
        await ctx.send(target, text)
    except ValueError:
        await ctx.reply(f"unknown channel {target}")
        return
    await ctx.reply(f"announced to {target}")
