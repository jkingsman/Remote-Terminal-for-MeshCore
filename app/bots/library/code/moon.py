"""Moon phase from the synodic-month cycle (known-epoch method, pure math).

Usage: ``moon``. Reports the phase name, illumination percent, and the dates
of the next full and new moons. No network access needed.
"""

import math
from datetime import UTC, datetime, timedelta

from remoteterm import bot

BOT_META = {
    "key": "moon",
    "name": "moon",
    "category": "Solar",
    "description": "Moon phase, illumination, next full/new moon",
    "version": "1.0.0",
}

_SYNODIC_DAYS = 29.530588853
# Reference new moon: 2000-01-06 18:14 UTC.
_EPOCH = datetime(2000, 1, 6, 18, 14, tzinfo=UTC)
_PHASES = (
    "New Moon",
    "Waxing Crescent",
    "First Quarter",
    "Waxing Gibbous",
    "Full Moon",
    "Waning Gibbous",
    "Last Quarter",
    "Waning Crescent",
)


@bot.on_keyword("moon")
async def moon(ctx, msg):
    now = datetime.now(tz=UTC)
    age = ((now - _EPOCH).total_seconds() / 86400.0) % _SYNODIC_DAYS
    illumination = (1.0 - math.cos(2.0 * math.pi * age / _SYNODIC_DAYS)) / 2.0 * 100.0
    slot = int(((age + _SYNODIC_DAYS / 16.0) % _SYNODIC_DAYS) / (_SYNODIC_DAYS / 8.0))
    phase = _PHASES[slot]
    to_full = (_SYNODIC_DAYS / 2.0 - age) % _SYNODIC_DAYS
    to_new = (_SYNODIC_DAYS - age) % _SYNODIC_DAYS
    full_date = (now + timedelta(days=to_full)).strftime("%b %d")
    new_date = (now + timedelta(days=to_new)).strftime("%b %d")
    await ctx.reply(
        f"Moon: {phase}, {illumination:.0f}% lit — next full {full_date}, next new {new_date}"
    )
