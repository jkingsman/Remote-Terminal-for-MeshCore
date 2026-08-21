"""PV yield forecast for a panel setup via forecast.solar (no key needed).

Usage: ``solarforecast`` or ``sf``. Estimates today's and tomorrow's solar
production from the configured location, panel tilt, azimuth, and kWp.
The public forecast.solar API is rate-limited (~12 calls/hour), hence the
generous cooldown.
"""

from remoteterm import bot

BOT_META = {
    "key": "solarforecast",
    "name": "solarforecast",
    "category": "Solar",
    "description": "PV production forecast today/tomorrow (forecast.solar)",
    "version": "1.0.0",
    "cooldown_seconds": 60,
    "settings_schema": [
        {
            "key": "lat",
            "label": "Latitude",
            "type": "float",
            "default": 47.6062,
            "min": -90,
            "max": 90,
        },
        {
            "key": "lon",
            "label": "Longitude",
            "type": "float",
            "default": -122.3321,
            "min": -180,
            "max": 180,
        },
        {
            "key": "declination",
            "label": "Panel tilt (deg)",
            "type": "int",
            "default": 30,
            "min": 0,
            "max": 90,
            "help": "0 = flat, 90 = vertical.",
        },
        {
            "key": "azimuth",
            "label": "Panel azimuth (deg)",
            "type": "int",
            "default": 0,
            "min": -180,
            "max": 180,
            "help": "forecast.solar convention: 0 = south, -90 = east, 90 = west.",
        },
        {
            "key": "kwp",
            "label": "Installed kWp",
            "type": "float",
            "default": 5.0,
            "min": 0.1,
            "max": 1000,
        },
    ],
    "settings": {
        "lat": 47.6062,
        "lon": -122.3321,
        "declination": 30,
        "azimuth": 0,
        "kwp": 5.0,
    },
}


def _setting(ctx, key: str, default: float) -> float:
    try:
        return float(ctx.settings.get(key, default))
    except (TypeError, ValueError):
        return default


@bot.on_keyword("solarforecast", "sf")
async def solarforecast(ctx, msg):
    lat = _setting(ctx, "lat", 47.6062)
    lon = _setting(ctx, "lon", -122.3321)
    declination = int(_setting(ctx, "declination", 30))
    azimuth = int(_setting(ctx, "azimuth", 0))
    kwp = _setting(ctx, "kwp", 5.0)
    url = f"https://api.forecast.solar/estimate/{lat:.4f}/{lon:.4f}/{declination}/{azimuth}/{kwp:g}"
    try:
        per_day = (await ctx.http.get_json(url))["result"]["watt_hours_day"]
        days = sorted(per_day.items())
    except Exception:  # httpx.HTTPError / payload shape surprises (ctx.http owns the client)
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    if not days:
        await ctx.reply(ctx.t("rt.no_results"))
        return
    text = f"PV: today {float(days[0][1]) / 1000.0:.1f} kWh"
    if len(days) > 1:
        text += f", tomorrow {float(days[1][1]) / 1000.0:.1f} kWh"
    await ctx.reply(text)
