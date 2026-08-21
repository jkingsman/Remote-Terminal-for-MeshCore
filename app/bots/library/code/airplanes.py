"""Nearby aircraft via the airplanes.live ADS-B API (no key needed).

Usage: ``airplanes [place]`` / ``adsb`` / ``aircraft``. Shows the closest
three aircraft within the configured radius: callsign, altitude, distance,
and direction. With no place, the default lat/lon (or default location)
setting is used.
"""

import math

from remoteterm import bot

BOT_META = {
    "key": "airplanes",
    "name": "airplanes",
    "category": "Info",
    "description": "Closest aircraft overhead (airplanes.live ADS-B)",
    "version": "1.0.0",
    "cooldown_seconds": 5,
    "settings_schema": [
        {
            "key": "radius_nm",
            "label": "Search radius (nm)",
            "type": "int",
            "default": 25,
            "min": 1,
            "max": 250,
        },
        {
            "key": "default_location",
            "label": "Default location",
            "type": "text",
            "default": "Seattle, WA",
            "help": "Used when the message gives no location and no lat/lon is set.",
        },
        {
            "key": "default_lat",
            "label": "Default latitude",
            "type": "float",
            "default": "",
            "min": -90,
            "max": 90,
            "help": "Optional. With longitude also set, skips the geocode lookup.",
        },
        {
            "key": "default_lon",
            "label": "Default longitude",
            "type": "float",
            "default": "",
            "min": -180,
            "max": 180,
            "help": "Optional. With latitude also set, skips the geocode lookup.",
        },
    ],
    "settings": {
        "radius_nm": 25,
        "default_location": "Seattle, WA",
        "default_lat": "",
        "default_lon": "",
    },
}

_EARTH_RADIUS_NM = 3440.065


def _opt_float(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = (
        math.sin((rlat2 - rlat1) / 2.0) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin((rlon2 - rlon1) / 2.0) ** 2
    )
    return _EARTH_RADIUS_NM * 2.0 * math.asin(math.sqrt(a))


def _compass(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    d_lon = math.radians(lon2 - lon1)
    x = math.sin(d_lon) * math.cos(rlat2)
    y = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(rlat2) * math.cos(d_lon)
    bearing = (math.degrees(math.atan2(x, y)) + 360.0) % 360.0
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[round(bearing / 45.0) % 8]


def _altitude_text(alt) -> str:
    if alt == "ground":
        return "ground"
    if isinstance(alt, int | float):
        return f"{int(alt):,}ft"
    return "?ft"


@bot.on_keyword("airplanes", "adsb", "aircraft")
async def airplanes(ctx, msg):
    query = msg.arg_text.strip()
    lat = lon = None
    place = ""
    if not query:
        lat = _opt_float(ctx.settings.get("default_lat"))
        lon = _opt_float(ctx.settings.get("default_lon"))
        place = str(ctx.settings.get("default_location") or "Seattle, WA").strip()
    if lat is None or lon is None:
        found = await ctx.geocode(query or place or "Seattle, WA")
        if found is None:
            await ctx.reply("Couldn't find that location")
            return
        lat, lon = found["lat"], found["lon"]
        place = found["name"].split(",")[0].strip()
    elif not place:
        place = f"{lat:.2f},{lon:.2f}"
    place = place[:40]

    try:
        radius = int(float(ctx.settings.get("radius_nm", 25)))
    except (TypeError, ValueError):
        radius = 25
    radius = max(1, min(250, radius))

    url = f"https://api.airplanes.live/v2/point/{lat:.4f}/{lon:.4f}/{radius}"
    try:
        aircraft = (await ctx.http.get_json(url)).get("ac") or []
    except Exception:  # httpx.HTTPError / payload shape surprises (ctx.http owns the client)
        await ctx.reply(ctx.t("rt.error_upstream"))
        return

    sightings = []
    for entry in aircraft:
        a_lat, a_lon = entry.get("lat"), entry.get("lon")
        if a_lat is None or a_lon is None:
            continue
        callsign = str(entry.get("flight") or entry.get("r") or entry.get("hex") or "?").strip()
        distance = _distance_nm(lat, lon, a_lat, a_lon)
        direction = _compass(lat, lon, a_lat, a_lon)
        sightings.append(
            (distance, f"{callsign} {_altitude_text(entry.get('alt_baro'))}", direction)
        )
    if not sightings:
        await ctx.reply(f"No aircraft within {radius}nm of {place}")
        return
    sightings.sort(key=lambda s: s[0])
    parts = [f"{label} {dist:.0f}nm {direction}" for dist, label, direction in sightings[:3]]
    await ctx.reply(f"{place}: " + "; ".join(parts))
