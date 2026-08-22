"""Collects unique message paths for 6 seconds. Seeded from meshcore-bot's multitest.

Say ``multitest``, then have stations transmit; after 6 seconds it reports the
distinct routing paths of everything heard in the window.
"""

import asyncio
import time

from remoteterm import bot

BOT_META = {
    "key": "multitest",
    "name": "multitest",
    "category": "Mesh",
    "description": "Collects unique message paths heard during a 6s window",
    "version": "1.1.0",
    "cooldown_seconds": 30,
}

WINDOW_SECONDS = 6


@bot.on_keyword("multitest", "mt")
async def multitest(ctx, msg):
    start = int(time.time())
    await asyncio.sleep(WINDOW_SECONDS)

    # Read-only lookup against this app's own message store (allowed for
    # mesh-introspection bots).
    from app.repository import MessageRepository

    messages = await MessageRepository.get_all(limit=100, after=start - 1)
    paths: dict[str, int] = {}
    direct = 0
    for message in messages:
        if message.outgoing:
            continue
        if not message.paths:
            direct += 1
            continue
        for message_path in message.paths:
            key = message_path.path or "direct"
            paths[key] = paths.get(key, 0) + 1

    if not paths and not direct:
        await ctx.reply(f"Heard nothing in {WINDOW_SECONDS}s.")
        return
    parts = [f"{p}×{n}" if n > 1 else p for p, n in sorted(paths.items())]
    if direct:
        parts.append(f"direct×{direct}" if direct > 1 else "direct")
    summary = " | ".join(parts)
    # Busy meshes overflow one RF frame — split instead of truncating.
    await ctx.reply_split(f"{len(parts)} unique path(s) in {WINDOW_SECONDS}s: {summary}")
