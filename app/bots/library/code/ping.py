"""Liveness check: replies Pong! Seeded from meshcore-bot's ping command."""

from remoteterm import bot

BOT_META = {
    "key": "ping",
    "name": "ping",
    "category": "Basic",
    "description": "Replies Pong! — liveness check",
    "version": "1.0.0",
}


@bot.on_keyword("ping")
async def ping(ctx, msg):
    await ctx.reply(ctx.t("rt.pong"))
