"""Liveness check: replies Pong! Seeded from meshcore-bot's ping command."""

from remoteterm import bot

BOT_META = {
    "key": "ping",
    "name": "ping",
    "category": "Basic",
    "description": "Replies Pong! — liveness check",
    "version": "1.1.0",
}


@bot.on_keyword("ping")
async def ping(ctx, msg):
    pong = ctx.t("rt.pong")
    # @[name] is the mention syntax mesh clients recognize and highlight.
    if msg.sender_name:
        await ctx.reply(f"@[{msg.sender_name}] {pong}")
    else:
        await ctx.reply(pong)
