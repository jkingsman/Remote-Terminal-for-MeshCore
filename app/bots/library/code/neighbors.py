"""Zero-hop neighbor report. Seeded from meshcore-bot's neighbors command.

Reports contacts whose known direct route is zero hops — nodes this radio
hears directly, from the app's own contact table (no discovery sweep).
"""

import time

from remoteterm import bot

BOT_META = {
    "key": "neighbors",
    "name": "neighbors",
    "category": "Mesh",
    "description": "Lists zero-hop neighbors (nodes heard directly)",
    "version": "1.0.0",
    "cooldown_seconds": 60,
}


def _ago(now: int, then: int | None) -> str:
    if not then:
        return "?"
    delta = max(0, now - then)
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h"
    return f"{delta // 86400}d"


@bot.on_keyword("neighbors", "neighbours")
async def neighbors(ctx, msg):
    # Read-only lookup against this app's own contact table (allowed for
    # mesh-introspection bots).
    from app.repository import ContactRepository

    contacts = await ContactRepository.get_all(limit=2000)
    zero_hop = [c for c in contacts if c.direct_path_len == 0]
    if not zero_hop:
        await ctx.reply("No zero-hop neighbors known yet.")
        return
    now = int(time.time())
    zero_hop.sort(key=lambda c: c.last_seen or 0, reverse=True)
    listed = ", ".join(
        f"{c.name or c.public_key[:8]} ({_ago(now, c.last_seen)})" for c in zero_hop[:6]
    )
    more = f" +{len(zero_hop) - 6} more" if len(zero_hop) > 6 else ""
    await ctx.reply(f"{len(zero_hop)} zero-hop neighbor(s): {listed}{more}"[:180])
