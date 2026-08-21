"""Dice roller. Seeded from meshcore-bot's dice command.

Usage: ``dice`` (d20), ``dice d6``, ``dice 3d6``, ``dice decade`` (d10+d10).
"""

import random

from remoteterm import bot

BOT_META = {
    "key": "dice",
    "name": "dice",
    "category": "Fun",
    "description": "Dice roller: d20, 2d6, decade",
    "version": "1.0.0",
    "settings_schema": [
        {
            "key": "max_dice",
            "label": "Maximum dice per roll",
            "type": "int",
            "default": 10,
            "min": 1,
            "max": 50,
        }
    ],
    "settings": {"max_dice": 10},
}


def _parse_spec(spec):
    """'d20' -> (1, 20); '3d6' -> (3, 6). Returns None when unparseable."""
    spec = spec.lower().strip()
    if "d" not in spec:
        return None
    count_str, _, sides_str = spec.partition("d")
    try:
        count = int(count_str) if count_str else 1
        sides = int(sides_str)
    except ValueError:
        return None
    if count < 1 or sides < 2:
        return None
    return count, sides


@bot.on_keyword("dice")
async def roll_dice(ctx, msg):
    max_dice = int(ctx.settings.get("max_dice", 10))
    spec = msg.arg_text.strip() or "d20"

    if spec.lower() == "decade":
        tens = random.randint(0, 9) * 10
        ones = random.randint(0, 9)
        total = tens + ones if (tens or ones) else 100
        await ctx.reply(f"decade roll: {tens:02d} + {ones} = {total}")
        return

    parsed = _parse_spec(spec)
    if parsed is None:
        await ctx.reply("usage: dice [NdS|decade] — e.g. dice d20, dice 3d6")
        return
    count, sides = parsed
    if count > max_dice:
        await ctx.reply(f"max {max_dice} dice per roll")
        return
    rolls = [random.randint(1, sides) for _ in range(count)]
    if count == 1:
        await ctx.reply(f"d{sides}: {rolls[0]}")
    else:
        await ctx.reply(f"{count}d{sides}: {' + '.join(map(str, rolls))} = {sum(rolls)}")
