"""Two-hour rain nowcast from Open-Meteo's 15-minute precipitation forecast.

Usage: ``rain [place]`` or ``nowcast [place]``. With no place, the default
location setting is used. Says when rain starts and eases over the next 2h.
"""

from remoteterm import bot

BOT_META = {
    "key": "rain",
    "name": "rain",
    "category": "Weather",
    "description": "Next-2h rain nowcast (Open-Meteo 15-min data)",
    "version": "1.0.0",
    "cooldown_seconds": 3,
    "settings_schema": [
        {
            "key": "default_location",
            "label": "Default location",
            "type": "text",
            "default": "Seattle, WA",
            "help": "Used when the message gives no location.",
        },
    ],
    "settings": {"default_location": "Seattle, WA"},
}


@bot.on_keyword("rain", "nowcast")
async def rain(ctx, msg):
    query = msg.arg_text.strip() or str(ctx.settings.get("default_location") or "Seattle, WA")
    found = await ctx.geocode(query)
    if found is None:
        await ctx.reply("Couldn't find that location")
        return
    place = found["name"].split(",")[0].strip()[:40]
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={found['lat']:.4f}"
        f"&longitude={found['lon']:.4f}&minutely_15=precipitation"
        "&forecast_minutely_15=8&timezone=auto"
    )
    try:
        block = (await ctx.http.get_json(url))["minutely_15"]
        times = block["time"]
        values = [float(v or 0.0) for v in block["precipitation"]]
        if not times or len(times) != len(values):
            raise ValueError("mismatched minutely_15 arrays")
    except Exception:  # httpx.HTTPError / payload shape surprises (ctx.http owns the client)
        await ctx.reply(ctx.t("rt.error_upstream"))
        return

    start = next((i for i, v in enumerate(values) if v > 0.0), None)
    if start is None:
        await ctx.reply(f"No rain expected in the next 2h ({place})")
        return
    ease = next((i for i in range(start + 1, len(values)) if values[i] == 0.0), None)
    rate = values[start]
    # times are local ISO strings like 2026-08-21T14:15 — keep just HH:MM.
    lead = (
        f"Rain now ({rate:.1f}mm/15min)"
        if start == 0
        else f"Rain starts ~{times[start][-5:]} ({rate:.1f}mm/15min)"
    )
    if ease is not None:
        await ctx.reply(f"{lead}, eases ~{times[ease][-5:]} ({place})")
    else:
        await ctx.reply(f"{lead}, continuing past the next 2h ({place})")
