"""Pushes new NWS weather alerts for a fixed point to a channel.

Polls api.weather.gov every 5 minutes for active alerts at the configured
latitude/longitude (US only) and sends each new alert's headline to the
configured channel once, deduplicated across restarts via bot state.
"""

from remoteterm import bot

BOT_META = {
    "key": "weather_alerts",
    "name": "weather_alerts",
    "category": "Alerts",
    "description": "Pushes new NWS alerts for a point to a channel",
    "version": "1.0.0",
    "settings_schema": [
        {
            "key": "default_lat",
            "label": "Latitude",
            "type": "float",
            "default": 47.6062,
            "min": -90,
            "max": 90,
        },
        {
            "key": "default_lon",
            "label": "Longitude",
            "type": "float",
            "default": -122.3321,
            "min": -180,
            "max": 180,
        },
        {
            "key": "channel",
            "label": "Alert channel",
            "type": "text",
            "default": "",
            "help": "Channel name for pushed alerts. Empty disables the poll.",
        },
    ],
    "settings": {"default_lat": 47.6062, "default_lon": -122.3321, "channel": ""},
}

_HEADERS = {"User-Agent": "RemoteTerm-MeshCore-bots/1.0", "Accept": "application/geo+json"}


def _setting(ctx, key: str, default: float) -> float:
    try:
        return float(ctx.settings.get(key, default))
    except (TypeError, ValueError):
        return default


@bot.on_cron("*/5 * * * *")
async def poll_alerts(ctx):
    channel = str(ctx.settings.get("channel") or "").strip()
    if not channel:
        ctx.log("channel setting is empty; skipping the weather alerts poll")
        return
    lat = _setting(ctx, "default_lat", 47.6062)
    lon = _setting(ctx, "default_lon", -122.3321)
    url = f"https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}"
    try:
        features = (await ctx.http.get_json(url, headers=_HEADERS)).get("features") or []
    except Exception:  # httpx.HTTPError / payload shape surprises (ctx.http owns the client)
        ctx.log("NWS alerts fetch failed")
        return
    sent = ctx.state.setdefault("sent_ids", [])
    fresh = [f for f in features if f.get("id") and f["id"] not in sent]
    for feature in fresh[:3]:
        props = feature.get("properties") or {}
        line = str(props.get("headline") or props.get("event") or "Weather alert")[:170]
        try:
            await ctx.send(channel, line)
        except ValueError:
            ctx.log(f"unknown channel {channel!r} for weather alerts")
            return
        sent.append(feature["id"])
    del sent[:-50]
