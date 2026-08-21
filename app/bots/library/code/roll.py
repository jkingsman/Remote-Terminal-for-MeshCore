"""Rolls a random number between 1 and N (default 100)."""

import random

from remoteterm import bot

BOT_META = {
    "key": "roll",
    "name": "roll",
    "category": "Fun",
    "description": "Rolls a random number 1..N (default 100)",
    "version": "1.0.0",
}


@bot.on_keyword("roll")
async def roll(ctx, msg):
    arg = msg.arg_text.strip()
    max_num = 100
    if arg:
        if not arg.isdigit() or not 1 <= int(arg) <= 10000:
            await ctx.reply("usage: roll [max] — e.g. roll, roll 20 (max 10000)")
            return
        max_num = int(arg)
    result = random.randint(1, max_num)
    who = msg.sender_name or "Someone"
    await ctx.reply(f"{who} rolled {result} (1-{max_num})")
