"""Sends a flood advert on request. Seeded from meshcore-bot's advert command.

DM-only and admin-gated by default, with the original's 1-hour cooldown —
adverts cost the whole mesh airtime.
"""

from remoteterm import bot

BOT_META = {
    "key": "advert",
    "name": "advert",
    "category": "Admin",
    "description": "Sends a flood advert on request (DM, admins, 1h cooldown)",
    "version": "1.0.0",
    "admin_only": True,
    "cooldown_seconds": 3600,
    "settings_schema": [
        {
            "key": "mode",
            "label": "Advert mode",
            "type": "select",
            "default": "flood",
            "options": [
                {"value": "flood", "label": "Flood"},
                {"value": "zero_hop", "label": "Zero-hop"},
            ],
        }
    ],
    "settings": {"mode": "flood"},
}


@bot.on_keyword("advert")
async def send_advert(ctx, msg):
    if not msg.is_dm:
        await ctx.reply(ctx.t("rt.dm_only"))
        return
    if ctx.is_test:
        await ctx.reply("(test run — advert not transmitted)")
        return

    # Drives this app's own radio advertise endpoint (allowed for admin bots).
    from fastapi import HTTPException

    from app.routers.radio import RadioAdvertiseRequest, send_advertisement

    setting = str(ctx.settings.get("mode", "flood") or "flood")
    mode = "zero_hop" if setting == "zero_hop" else "flood"
    try:
        await send_advertisement(RadioAdvertiseRequest(mode=mode))
    except HTTPException as exc:
        await ctx.reply(f"Advert failed: {exc.detail}")
        return
    await ctx.reply(f"Advert sent ({mode.replace('_', '-')}).")
