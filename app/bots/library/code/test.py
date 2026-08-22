"""Signal report: hop count, hop ids, region scope, and clock offset."""

import time

from remoteterm import bot

BOT_META = {
    "key": "test",
    "name": "test",
    "category": "Basic",
    "description": "Signal report: hops, path, region, clock offset",
    "version": "1.1.0",
}


@bot.on_keyword("test", "t")
async def signal_report(ctx, msg):
    parts = []
    path = (msg.path or "").strip()
    if path:
        width = max(1, int(msg.path_bytes_per_hop or 1)) * 2
        hops = [path[i : i + width] for i in range(0, len(path), width)]
        noun = "hop" if len(hops) == 1 else "hops"
        parts.append(f"{len(hops)} {noun}: {'>'.join(hops)}")
    else:
        parts.append("direct (no path)")
    if msg.scoped and msg.region:
        parts.append(f"region {msg.region}")
    if msg.sender_timestamp:
        offset = int(time.time()) - int(msg.sender_timestamp)
        parts.append(f"clock offset {offset:+d}s")
    # @[name] is the mention syntax mesh clients recognize and highlight.
    prefix = f"@[{msg.sender_name}]: " if msg.sender_name else ""
    await ctx.reply(prefix + " | ".join(parts))
