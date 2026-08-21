"""Sunrise/sunset times computed locally with the NOAA solar equations.

Usage: ``sun`` (default location) or ``sun <place>``. Pure math — the only
network use is the optional place lookup (Nominatim via ``ctx.geocode``).
Times are shown in the node's local timezone.
"""

import math
from datetime import UTC, datetime

from remoteterm import bot

BOT_META = {
    "key": "sun",
    "name": "sun",
    "category": "Solar",
    "description": "Sunrise/sunset times for a location (NOAA solar math)",
    "version": "1.0.0",
    "settings_schema": [
        {
            "key": "default_location",
            "label": "Default location",
            "type": "text",
            "default": "Seattle, WA",
            "help": "Used when the message gives no location.",
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
    "settings": {"default_location": "Seattle, WA", "default_lat": "", "default_lon": ""},
}

_J2000 = 2451545.0
_UNIX_EPOCH_JD = 2440587.5


def _opt_float(value):
    """Float value of a setting, or None when unset/blank/invalid."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sun_times(lat: float, lon: float) -> tuple[datetime, datetime] | None:
    """(sunrise, sunset) as aware UTC datetimes, or None for polar day/night.

    The standard NOAA sunrise equation (Meeus-based); accurate to a minute
    or two, which is plenty for a mesh reply.
    """
    jd_now = datetime.now(tz=UTC).timestamp() / 86400.0 + _UNIX_EPOCH_JD
    cycle = round(jd_now - _J2000 + lon / 360.0)
    jstar = cycle - lon / 360.0  # mean solar noon, days since J2000
    mean_anom = math.radians((357.5291 + 0.98560028 * jstar) % 360.0)
    center = (
        1.9148 * math.sin(mean_anom)
        + 0.0200 * math.sin(2.0 * mean_anom)
        + 0.0003 * math.sin(3.0 * mean_anom)
    )
    ecliptic = math.radians((math.degrees(mean_anom) + center + 180.0 + 102.9372) % 360.0)
    transit = _J2000 + jstar + 0.0053 * math.sin(mean_anom) - 0.0069 * math.sin(2.0 * ecliptic)
    sin_dec = math.sin(ecliptic) * math.sin(math.radians(23.4397))
    cos_dec = math.cos(math.asin(sin_dec))
    phi = math.radians(lat)
    cos_hour = (math.sin(math.radians(-0.833)) - math.sin(phi) * sin_dec) / (
        math.cos(phi) * cos_dec
    )
    if cos_hour < -1.0 or cos_hour > 1.0:
        return None
    half_day = math.degrees(math.acos(cos_hour)) / 360.0
    rise_ts = (transit - half_day - _UNIX_EPOCH_JD) * 86400.0
    set_ts = (transit + half_day - _UNIX_EPOCH_JD) * 86400.0
    rise = datetime.fromtimestamp(rise_ts, tz=UTC)
    sets = datetime.fromtimestamp(set_ts, tz=UTC)
    return rise, sets


@bot.on_keyword("sun")
async def sun(ctx, msg):
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
    place = place[:60]
    times = _sun_times(lat, lon)
    if times is None:
        await ctx.reply(f"Sun ({place}): no rise/set today (polar day or night)")
        return
    rise, sets = times
    rise_local = rise.astimezone().strftime("%H:%M")
    set_local = sets.astimezone().strftime("%H:%M")
    await ctx.reply(f"Sun ({place}): rise {rise_local}, set {set_local} (local)")
