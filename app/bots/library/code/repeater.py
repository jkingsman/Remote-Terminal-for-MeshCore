"""Repeater fleet overview. Seeded from meshcore-bot's repeater command.

Ported subcommands: ``repeater list`` and ``repeater stats``. The destructive
fleet operations from meshcore-bot (purge, restore, auto-manage) intentionally
stay in the app's Settings › Radio-App Management UI, where they have
confirmation flows.
"""

import time

from remoteterm import bot

BOT_META = {
    "key": "repeater",
    "name": "repeater",
    "category": "Admin",
    "description": "Repeater fleet overview: list, stats (DM, admins)",
    "version": "1.0.0",
    "admin_only": True,
    "respond_to_dms": True,
}

SECONDS_24H = 86400


def _ago(now: int, then: int | None) -> str:
    if not then:
        return "?"
    delta = max(0, now - then)
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < SECONDS_24H:
        return f"{delta // 3600}h"
    return f"{delta // SECONDS_24H}d"


@bot.on_keyword("repeater", "rp")
async def repeater_cmd(ctx, msg):
    if not msg.is_dm:
        await ctx.reply(ctx.t("rt.dm_only"))
        return

    # Read-only lookup against this app's own contact table (allowed for
    # mesh-introspection bots).
    from app.repository import ContactRepository

    contacts = await ContactRepository.get_all(limit=2000)
    repeaters = [c for c in contacts if c.type == 2]
    sub = (msg.args[0].lower() if msg.args else "stats").strip()
    now = int(time.time())

    if sub == "list":
        if not repeaters:
            await ctx.reply("No repeaters known.")
            return
        repeaters.sort(key=lambda c: c.last_seen or 0, reverse=True)
        listed = ", ".join(
            f"{c.name or c.public_key[:8]} ({_ago(now, c.last_seen)})" for c in repeaters[:6]
        )
        more = f" +{len(repeaters) - 6} more" if len(repeaters) > 6 else ""
        await ctx.reply(f"{len(repeaters)} repeaters: {listed}{more}"[:180])
        return

    if sub == "stats":
        heard_24h = sum(1 for c in repeaters if (c.last_seen or 0) >= now - SECONDS_24H)
        zero_hop = sum(1 for c in repeaters if c.direct_path_len == 0)
        multibyte = sum(
            1 for c in repeaters if c.direct_path_hash_mode and c.direct_path_hash_mode >= 2
        )
        on_radio = sum(1 for c in repeaters if c.on_radio)
        await ctx.reply(
            f"Repeaters: {len(repeaters)} known · {heard_24h} heard 24h · "
            f"{zero_hop} zero-hop · {multibyte} multibyte · {on_radio} on radio"
        )
        return

    await ctx.reply(
        "usage: repeater list|stats — purge/restore/auto-manage live in "
        "Settings > Radio-App Management"
    )
