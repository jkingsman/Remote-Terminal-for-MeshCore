"""Weather forecasts and alerts: NWS first, Open-Meteo fallback.

Usage: ``wx [place]`` / ``weather [place]`` for the forecast; ``wxa [place]``,
``wxalert [place]`` or ``wx alerts [place]`` for active NWS alerts (US only).
With no place, the default location setting is used.

US locations use api.weather.gov (point metadata, then the forecast).
Anywhere else — or when NWS is down — Open-Meteo answers instead. A generic
cron trigger is included: add a schedule on the Triggers tab and set the
morning channel setting to push the default location's forecast there.
"""

from remoteterm import bot

BOT_META = {
    "key": "wx",
    "name": "wx",
    "category": "Weather",
    "description": "Forecast + alerts: NWS with Open-Meteo fallback",
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
            "default": "imperial",
            "options": [
                {"value": "imperial", "label": "Imperial (F, mph)"},
                {"value": "metric", "label": "Metric (C, km/h)"},
            ],
        },
        {
            "key": "morning_channel",
            "label": "Morning forecast channel",
            "type": "text",
            "default": "",
            "help": (
                "Channel for cron-triggered morning forecasts — add a cron "
                "schedule on the Triggers tab."
            ),
        },
    ],
    "settings": {"default_location": "Seattle, WA", "units": "imperial", "morning_channel": ""},
}

_NWS = "https://api.weather.gov"
_HEADERS = {"User-Agent": "RemoteTerm-MeshCore-bots/1.0", "Accept": "application/geo+json"}


def _clip(text: str, limit: int = 180) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _metric(ctx) -> bool:
    return str(ctx.settings.get("units") or "imperial").strip().lower() == "metric"


async def _locate(ctx, arg_text: str):
    """Resolve message args (or the default location) to (lat, lon, short name)."""
    query = arg_text.strip() or str(ctx.settings.get("default_location") or "Seattle, WA")
    found = await ctx.geocode(query)
    if found is None:
        return None
    return found["lat"], found["lon"], found["name"].split(",")[0].strip()[:40]


async def _nws_forecast(ctx, lat: float, lon: float, metric: bool) -> str:
    points = await ctx.http.get_json(f"{_NWS}/points/{lat:.4f},{lon:.4f}", headers=_HEADERS)
    url = points["properties"]["forecast"]
    if metric:
        url += "?units=si"
    forecast = await ctx.http.get_json(url, headers=_HEADERS)
    periods = forecast["properties"]["periods"]
    now = periods[0]
    wind_dir = now.get("windDirection") or "?"
    wind_speed = now.get("windSpeed") or "?"
    text = (
        f"{now['name']}: {now['shortForecast'][:60]} {now['temperature']}"
        f"{now['temperatureUnit']}, wind {wind_dir} {wind_speed}"
    )
    if len(periods) > 1:
        nxt = periods[1]
        text += (
            f"; {nxt['name']}: {nxt['shortForecast'][:40]} "
            f"{nxt['temperature']}{nxt['temperatureUnit']}"
        )
    return text


async def _open_meteo(ctx, lat: float, lon: float, metric: bool) -> str:
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat:.4f}&longitude={lon:.4f}"
        "&current_weather=true&daily=temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max&timezone=auto"
    )
    if not metric:
        url += "&temperature_unit=fahrenheit&wind_speed_unit=mph"
    data = await ctx.http.get_json(url)
    current = data["current_weather"]
    daily = data["daily"]
    t_unit = "C" if metric else "F"
    w_unit = "km/h" if metric else "mph"
    low = daily["temperature_2m_min"][0]
    high = daily["temperature_2m_max"][0]
    text = (
        f"now {current['temperature']:.0f}{t_unit}, wind {current['windspeed']:.0f}{w_unit}; "
        f"today {low:.0f}-{high:.0f}{t_unit}"
    )
    rain_prob = (daily.get("precipitation_probability_max") or [None])[0]
    if rain_prob is not None:
        text += f", precip {rain_prob:.0f}%"
    return text


async def _forecast_text(ctx, lat: float, lon: float, place: str) -> str | None:
    """Compact one-line forecast, or None when every upstream fails."""
    metric = _metric(ctx)
    try:
        body = await _nws_forecast(ctx, lat, lon, metric)
    except Exception:  # non-US point (NWS 404) or NWS outage — try Open-Meteo
        try:
            body = await _open_meteo(ctx, lat, lon, metric)
        except Exception:  # httpx.HTTPError / payload shape surprises
            return None
    return _clip(f"{place}: {body}")


async def _alert_lines(ctx, lat: float, lon: float) -> list[str]:
    data = await ctx.http.get_json(
        f"{_NWS}/alerts/active?point={lat:.4f},{lon:.4f}", headers=_HEADERS
    )
    lines = []
    for feature in (data.get("features") or [])[:2]:
        props = feature.get("properties") or {}
        event = props.get("event") or "Weather alert"
        headline = props.get("headline") or ""
        lines.append(_clip(f"{event}: {headline}" if headline else event, 170))
    return lines


@bot.on_keyword("wx", "weather", "wxa", "wxalert")
async def wx(ctx, msg):
    args = list(msg.args)
    alerts_mode = msg.keyword in ("wxa", "wxalert")
    if args and args[0].lower() == "alerts":
        alerts_mode = True
        args = args[1:]
    located = await _locate(ctx, " ".join(args))
    if located is None:
        await ctx.reply("Couldn't find that location")
        return
    lat, lon, place = located
    if alerts_mode:
        try:
            lines = await _alert_lines(ctx, lat, lon)
        except Exception:  # NWS alerts are US-only; non-US points fail here
            await ctx.reply(ctx.t("rt.error_upstream"))
            return
        if not lines:
            await ctx.reply(f"No active alerts for {place}")
            return
        for line in lines:
            await ctx.reply(line)
        return
    text = await _forecast_text(ctx, lat, lon, place)
    if text is None:
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    await ctx.reply(text)


@bot.on_cron()
async def morning_forecast(ctx):
    """Runs for cron schedules added on the Triggers tab."""
    channel = str(ctx.settings.get("morning_channel") or "").strip()
    if not channel:
        ctx.log("morning_channel setting is empty; skipping the scheduled forecast")
        return
    query = str(ctx.settings.get("default_location") or "Seattle, WA")
    found = await ctx.geocode(query)
    if found is None:
        ctx.log(f"could not geocode {query!r} for the scheduled forecast")
        return
    place = found["name"].split(",")[0].strip()[:40]
    text = await _forecast_text(ctx, found["lat"], found["lon"], place)
    if text is None:
        ctx.log("weather upstreams failed for the scheduled forecast")
        return
    try:
        await ctx.send(channel, text)
    except ValueError:
        ctx.log(f"unknown channel {channel!r} for the scheduled forecast")
