"""USGS earthquake alerts for a bounding box, plus a ``quake`` keyword.

The cron trigger polls the USGS FDSN event feed every 10 minutes and pushes
new quakes at or above the minimum magnitude to the configured channel
(deduplicated across restarts via bot state). ``quake`` replies with the
latest event in the box from the last 24h.
"""

from datetime import UTC, datetime, timedelta

from remoteterm import bot

BOT_META = {
    "key": "earthquake",
    "name": "earthquake",
    "category": "Alerts",
    "description": "USGS quake alerts for an area; 'quake' shows the latest",
    "version": "1.0.0",
    "settings_schema": [
        {
            "key": "min_magnitude",
            "label": "Minimum magnitude",
            "type": "float",
            "default": 3.0,
            "min": 0,
            "max": 10,
        },
        {"key": "min_lat", "label": "South latitude", "type": "float", "default": 32.0},
        {"key": "max_lat", "label": "North latitude", "type": "float", "default": 42.0},
        {"key": "min_lon", "label": "West longitude", "type": "float", "default": -125.0},
        {"key": "max_lon", "label": "East longitude", "type": "float", "default": -114.0},
        {
            "key": "channel",
            "label": "Alert channel",
            "type": "text",
            "default": "",
            "help": "Channel name for cron-pushed quake alerts. Empty disables pushes.",
        },
    ],
    "settings": {
        "min_magnitude": 3.0,
        "min_lat": 32.0,
        "max_lat": 42.0,
        "min_lon": -125.0,
        "max_lon": -114.0,
        "channel": "",
    },
}

_BASE = "https://earthquake.usgs.gov/fdsnws/event/1/query"


def _setting(ctx, key: str, default: float) -> float:
    try:
        return float(ctx.settings.get(key, default))
    except (TypeError, ValueError):
        return default


def _query_url(ctx, since: datetime) -> str:
    return (
        f"{_BASE}?format=geojson&starttime={since:%Y-%m-%dT%H:%M:%S}"
        f"&minmagnitude={_setting(ctx, 'min_magnitude', 3.0):g}"
        f"&minlatitude={_setting(ctx, 'min_lat', 32.0):g}"
        f"&maxlatitude={_setting(ctx, 'max_lat', 42.0):g}"
        f"&minlongitude={_setting(ctx, 'min_lon', -125.0):g}"
        f"&maxlongitude={_setting(ctx, 'max_lon', -114.0):g}"
        "&orderby=time"
    )


def _format_quake(feature: dict) -> str:
    props = feature.get("properties") or {}
    mag = props.get("mag")
    mag_text = f"M{mag:.1f}" if isinstance(mag, int | float) else "M?"
    place = props.get("place") or "unknown location"
    stamp = props.get("time")
    when = ""
    if isinstance(stamp, int | float):
        local = datetime.fromtimestamp(stamp / 1000.0, tz=UTC).astimezone()
        when = f", {local:%H:%M}"
    return f"{mag_text} quake {place}{when}"[:170]


@bot.on_keyword("quake")
async def quake(ctx, msg):
    since = datetime.now(tz=UTC) - timedelta(hours=24)
    try:
        features = (await ctx.http.get_json(_query_url(ctx, since))).get("features") or []
    except Exception:  # httpx.HTTPError / payload shape surprises (ctx.http owns the client)
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    if not features:
        magnitude = _setting(ctx, "min_magnitude", 3.0)
        await ctx.reply(f"No M{magnitude:g}+ quakes in the configured area in the last 24h")
        return
    await ctx.reply(_format_quake(features[0]))


@bot.on_cron("*/10 * * * *")
async def poll_quakes(ctx):
    channel = str(ctx.settings.get("channel") or "").strip()
    if not channel:
        ctx.log("channel setting is empty; skipping the earthquake poll")
        return
    since = datetime.now(tz=UTC) - timedelta(hours=1)
    try:
        features = (await ctx.http.get_json(_query_url(ctx, since))).get("features") or []
    except Exception:  # httpx.HTTPError / payload shape surprises (ctx.http owns the client)
        ctx.log("USGS earthquake fetch failed")
        return
    sent = ctx.state.setdefault("sent_ids", [])
    fresh = [f for f in features if f.get("id") and f["id"] not in sent]
    # features arrive newest-first; announce the newest three, oldest first.
    for feature in reversed(fresh[:3]):
        try:
            await ctx.send(channel, _format_quake(feature))
        except ValueError:
            ctx.log(f"unknown channel {channel!r} for earthquake alerts")
            return
        sent.append(feature["id"])
    del sent[:-50]
