"""Welcomes first-time posters in a channel. Seeded from meshcore-bot's greeter.

Tracks who it has greeted in persistent ``ctx.state`` (per sender+channel), so
restarts don't re-greet. Note: on first enable every sender counts as new —
including long-time regulars — so enable it on quiet channels first, or
pre-mark regulars by letting it run in a busy hour before announcing it.
meshcore-bot's dead-air delay and defer-to-human behaviors are not ported; add
them here in code if your mesh wants them.
"""

from remoteterm import bot

BOT_META = {
    "key": "greeter",
    "name": "greeter",
    "category": "Basic",
    "description": "Welcomes first-time posters per channel",
    "version": "1.0.0",
    "respond_to_dms": False,
    "settings_schema": [
        {
            "key": "greeting",
            "label": "Greeting template",
            "type": "text",
            "default": "",
            "help": "Blank uses the translated default. Placeholders: {name}, {channel}",
        },
        {
            "key": "max_greeted_remembered",
            "label": "Greeted senders remembered",
            "type": "int",
            "default": 2000,
            "min": 100,
            "max": 20000,
        },
    ],
    "settings": {"greeting": "", "max_greeted_remembered": 2000},
}


@bot.on_message()
async def maybe_greet(ctx, msg):
    if msg.is_dm or msg.is_outgoing or not msg.sender_name or not msg.channel_key:
        return

    seen = ctx.state.setdefault("greeted", {})
    mark = f"{msg.sender_name}|{msg.channel_key}"
    if mark in seen:
        return
    seen[mark] = msg.sender_timestamp or 0

    # Bound the remembered set so state doesn't grow forever.
    limit = int(ctx.settings.get("max_greeted_remembered", 2000))
    if len(seen) > limit:
        for stale in sorted(seen, key=seen.get)[: len(seen) - limit]:
            del seen[stale]

    template = (ctx.settings.get("greeting") or "").strip()
    channel = msg.channel_name or "this channel"
    if template:
        text = template.replace("{name}", msg.sender_name).replace("{channel}", channel)
    else:
        text = ctx.t("rt.welcome", name=msg.sender_name, channel=channel)
    ctx.log(f"greeting {msg.sender_name} in {channel}")
    await ctx.reply(text)
