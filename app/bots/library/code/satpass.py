"""Next visible satellite pass via the N2YO API (key required).

Usage: ``satpass`` (default satellite), ``satpass iss``, ``satpass 25544``.
Shortcuts: iss, hst/hubble, tiangong. Get a free API key at n2yo.com and set
it in this bot's settings along with the observer coordinates.
"""

from datetime import UTC, datetime

from remoteterm import bot

BOT_META = {
    "key": "satpass",
    "name": "satpass",
    "category": "Solar",
    "description": "Next visible satellite pass (N2YO, key required)",
    "version": "1.0.0",
    "cooldown_seconds": 5,
    "settings_schema": [
        {
            "key": "n2yo_api_key",
            "label": "N2YO API key",
            "type": "password",
            "default": "",
            "help": "Free key from n2yo.com — required.",
        },
        {
            "key": "default_norad",
            "label": "Default NORAD id",
            "type": "int",
            "default": 25544,
            "help": "Satellite used when the message names none (25544 = ISS).",
        },
        {
            "key": "observer_lat",
            "label": "Observer latitude",
            "type": "float",
            "default": 47.6062,
            "min": -90,
            "max": 90,
        },
        {
            "key": "observer_lon",
            "label": "Observer longitude",
            "type": "float",
            "default": -122.3321,
            "min": -180,
            "max": 180,
        },
    ],
    "settings": {
        "n2yo_api_key": "",
        "default_norad": 25544,
        "observer_lat": 47.6062,
        "observer_lon": -122.3321,
    },
}

_SHORTCUTS = {"iss": 25544, "hst": 20580, "hubble": 20580, "tiangong": 48274}


def _setting(ctx, key: str, default: float) -> float:
    try:
        return float(ctx.settings.get(key, default))
    except (TypeError, ValueError):
        return default


@bot.on_keyword("satpass")
async def satpass(ctx, msg):
    api_key = str(ctx.settings.get("n2yo_api_key") or "").strip()
    if not api_key:
        await ctx.reply("Set the N2YO API key in this bot's settings")
        return
    arg = msg.arg_text.strip().lower()
    if not arg:
        norad = int(_setting(ctx, "default_norad", 25544))
    elif arg in _SHORTCUTS:
        norad = _SHORTCUTS[arg]
    elif arg.isdigit():
        norad = int(arg)
    else:
        await ctx.reply("Usage: satpass [iss|hst|tiangong|NORAD#]")
        return
    lat = _setting(ctx, "observer_lat", 47.6062)
    lon = _setting(ctx, "observer_lon", -122.3321)
    url = (
        f"https://api.n2yo.com/rest/v1/satellite/visualpasses/{norad}"
        f"/{lat:.4f}/{lon:.4f}/0/2/60/&apiKey={api_key}"
    )
    try:
        data = await ctx.http.get_json(url)
    except Exception:  # httpx.HTTPError / bad JSON (ctx.http owns the client)
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    if not isinstance(data, dict) or "error" in data:
        await ctx.reply("N2YO error — check the API key in this bot's settings")
        return
    name = str((data.get("info") or {}).get("satname") or norad)
    passes = data.get("passes") or []
    if not passes:
        await ctx.reply(f"No visible passes for {name} in the next 2 days")
        return
    nxt = passes[0]
    start_utc = nxt.get("startUTC") if isinstance(nxt, dict) else None
    if not isinstance(start_utc, int | float):
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    start = datetime.fromtimestamp(int(start_utc), tz=UTC).astimezone()
    duration = int(nxt.get("duration") or 0)
    max_el = float(nxt.get("maxEl") or 0.0)
    compass = str(nxt.get("startAzCompass") or "?")
    await ctx.reply(
        f"{name}: next pass {start:%a %H:%M} local, {duration // 60}m{duration % 60:02d}s, "
        f"max el {max_el:.0f} deg, from {compass}"
    )
