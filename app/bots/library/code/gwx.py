"""Global weather via Open-Meteo — works anywhere, no API key.

Usage: ``gwx [place]`` or ``globalweather [place]``. With no place, the
default location setting is used. Metric by default.
"""

from remoteterm import bot

BOT_META = {
    "key": "gwx",
    "name": "gwx",
    "category": "Weather",
    "description": "Global weather anywhere (Open-Meteo)",
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
        {
            "key": "units",
            "label": "Units",
            "type": "select",
            "default": "metric",
            "options": [
                {"value": "metric", "label": "Metric (C, km/h)"},
                {"value": "imperial", "label": "Imperial (F, mph)"},
            ],
        },
    ],
    "settings": {"default_location": "Seattle, WA", "units": "metric"},
}


def _clip(text: str, limit: int = 180) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


@bot.on_keyword("gwx", "globalweather")
async def gwx(ctx, msg):
    query = msg.arg_text.strip() or str(ctx.settings.get("default_location") or "Seattle, WA")
    found = await ctx.geocode(query)
    if found is None:
        await ctx.reply("Couldn't find that location")
        return
    place = found["name"].split(",")[0].strip()[:40]
    metric = str(ctx.settings.get("units") or "metric").strip().lower() != "imperial"
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={found['lat']:.4f}"
        f"&longitude={found['lon']:.4f}&current_weather=true"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&timezone=auto"
    )
    if not metric:
        url += "&temperature_unit=fahrenheit&wind_speed_unit=mph"
    try:
        data = await ctx.http.get_json(url)
        current = data["current_weather"]
        daily = data["daily"]
        low = daily["temperature_2m_min"][0]
        high = daily["temperature_2m_max"][0]
        rain_prob = (daily.get("precipitation_probability_max") or [None])[0]
        t_unit = "C" if metric else "F"
        w_unit = "km/h" if metric else "mph"
        text = (
            f"{place}: now {current['temperature']:.0f}{t_unit}, "
            f"wind {current['windspeed']:.0f}{w_unit}; today {low:.0f}-{high:.0f}{t_unit}"
        )
        if rain_prob is not None:
            text += f", precip {rain_prob:.0f}%"
    except Exception:  # httpx.HTTPError / payload shape surprises (ctx.http owns the client)
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    await ctx.reply(_clip(text))
