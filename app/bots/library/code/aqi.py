"""Air quality (US AQI + PM2.5) via the Open-Meteo air quality API.

Usage: ``aqi [place]`` or ``airquality [place]``. With no place, the default
location setting is used.
"""

from remoteterm import bot

BOT_META = {
    "key": "aqi",
    "name": "aqi",
    "category": "Weather",
    "description": "Air quality: US AQI and PM2.5 (Open-Meteo)",
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


def _category(aqi: float) -> str:
    if aqi <= 50:
        return "Good"
    if aqi <= 100:
        return "Moderate"
    if aqi <= 150:
        return "Unhealthy for Sensitive Groups"
    if aqi <= 200:
        return "Unhealthy"
    if aqi <= 300:
        return "Very Unhealthy"
    return "Hazardous"


@bot.on_keyword("aqi", "airquality")
async def aqi(ctx, msg):
    query = msg.arg_text.strip() or str(ctx.settings.get("default_location") or "Seattle, WA")
    found = await ctx.geocode(query)
    if found is None:
        await ctx.reply("Couldn't find that location")
        return
    place = found["name"].split(",")[0].strip()[:40]
    url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={found['lat']:.4f}&longitude={found['lon']:.4f}&current=us_aqi,pm2_5"
    )
    try:
        current = (await ctx.http.get_json(url))["current"]
        aqi_value = current.get("us_aqi")
        pm25 = current.get("pm2_5")
    except Exception:  # httpx.HTTPError / payload shape surprises (ctx.http owns the client)
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    if aqi_value is None:
        await ctx.reply(f"No air quality data for {place}")
        return
    text = f"AQI {aqi_value:.0f} ({_category(float(aqi_value))})"
    if pm25 is not None:
        text += f", PM2.5 {pm25:.1f} ug/m3"
    await ctx.reply(f"{text} — {place}")
