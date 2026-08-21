"""The classic Magic 8-Ball: twenty time-honored answers."""

import random

from remoteterm import bot

BOT_META = {
    "key": "magic8",
    "name": "magic8",
    "category": "Fun",
    "description": "Ask the Magic 8-Ball a yes/no question",
    "version": "1.0.0",
}

ANSWERS = (
    "It is certain.",
    "It is decidedly so.",
    "Without a doubt.",
    "Yes definitely.",
    "You may rely on it.",
    "As I see it, yes.",
    "Most likely.",
    "Outlook good.",
    "Yes.",
    "Signs point to yes.",
    "Reply hazy, try again.",
    "Ask again later.",
    "Better not tell you now.",
    "Cannot predict now.",
    "Concentrate and ask again.",
    "Don't count on it.",
    "My reply is no.",
    "My sources say no.",
    "Outlook not so good.",
    "Very doubtful.",
)


@bot.on_keyword("magic8")
async def magic8(ctx, msg):
    await ctx.reply(f"Magic 8-Ball: {random.choice(ANSWERS)}")
