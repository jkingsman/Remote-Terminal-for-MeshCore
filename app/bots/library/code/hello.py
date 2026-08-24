"""Answers salutations in ~30 languages with a robot-flavored greeting."""

import random

from remoteterm import bot

BOT_META = {
    "key": "hello",
    "name": "hello",
    "category": "Basic",
    "description": "Greets back anyone who says hello, hi, hola, bonjour, ...",
    "version": "1.1.0",
}

KEYWORDS = (
    "hello",
    "hi",
    "hey",
    "howdy",
    "greetings",
    "salutations",
    "good morning",
    "good afternoon",
    "good evening",
    "good night",
    "yo",
    "sup",
    "whats up",
    "what's up",
    "morning",
    "afternoon",
    "evening",
    "night",
    "gday",
    "g'day",
    "hola",
    "bonjour",
    "ciao",
    "namaste",
    "aloha",
    "shalom",
    "konnichiwa",
    "guten tag",
    "buenos dias",
    "buenas tardes",
    "buenas noches",
)

SUFFIXES = (
    "Beep boop.",
    "All greeting circuits nominal.",
    "This unit is pleased to detect you on the mesh.",
)


@bot.on_keyword(*KEYWORDS)
async def hello(ctx, msg):
    # @[name] is the mention syntax mesh clients recognize and highlight.
    name = f"@[{msg.sender_name}]" if msg.sender_name else "human"
    await ctx.reply(f"{ctx.t('rt.greeting', name=name)} {random.choice(SUFFIXES)}")
