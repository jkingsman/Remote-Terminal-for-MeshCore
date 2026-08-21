"""Live World Cup match announcer. Seeded from meshcore-bot's worldcup service.

Polls the ESPN scoreboard every 2 minutes and posts score changes and final
results to a channel. Idles for 30 minutes at a time when no matches are on,
so the off-season poll cost is negligible.
"""

import time

from remoteterm import bot

BOT_META = {
    "key": "worldcup_live",
    "name": "worldcup-live",
    "category": "Alerts",
    "description": "Posts live World Cup score changes to a channel",
    "version": "1.0.0",
    "respond_to_dms": False,
    "settings_schema": [
        {
            "key": "channel",
            "label": "Announcement channel",
            "type": "text",
            "default": "",
            "help": "Channel name (e.g. #sports). Empty disables sending.",
        }
    ],
    "settings": {"channel": ""},
}

_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
IDLE_SECONDS = 30 * 60


def _summarize(event: dict) -> tuple[str, str, str] | None:
    """(event_id, 'ARG 2-1 FRA', state) from one ESPN scoreboard event."""
    try:
        competition = event["competitions"][0]
        competitors = competition["competitors"]
        home = next(c for c in competitors if c.get("homeAway") == "home")
        away = next(c for c in competitors if c.get("homeAway") == "away")
        line = (
            f"{home['team']['abbreviation']} {home.get('score', '0')}-"
            f"{away.get('score', '0')} {away['team']['abbreviation']}"
        )
        state = event.get("status", {}).get("type", {}).get("state", "pre")
        return str(event.get("id", line)), line, state
    except (KeyError, IndexError, StopIteration, TypeError):
        return None


@bot.on_cron("*/2 * * * *")
async def poll(ctx):
    channel = str(ctx.settings.get("channel", "") or "").strip()
    if not channel:
        return
    now = int(time.time())
    if now < int(ctx.state.get("idle_until", 0)):
        return

    try:  # upstream failures: log and retry next poll (ctx.http owns the client)
        data = await ctx.http.get_json(_URL)
        events = data.get("events", [])
    except Exception as exc:
        ctx.log(f"ESPN poll failed: {exc}", level="WARNING")
        return

    live = []
    for event in events:
        summary = _summarize(event)
        if summary is not None and summary[2] == "in":
            live.append(summary)

    if not live:
        ctx.state["idle_until"] = now + IDLE_SECONDS
        return

    scores = ctx.state.setdefault("scores", {})
    for event_id, line, _state in live:
        previous = scores.get(event_id)
        if previous is None:
            scores[event_id] = line
            await ctx.send(channel, f"Kickoff: {line}")
        elif previous != line:
            scores[event_id] = line
            await ctx.send(channel, f"GOAL! {line}")

    # Finished matches: announce once, then forget.
    live_ids = {event_id for event_id, _line, _state in live}
    for event in events:
        summary = _summarize(event)
        if summary is None:
            continue
        event_id, line, state = summary
        if state == "post" and event_id in scores and event_id not in live_ids:
            del scores[event_id]
            await ctx.send(channel, f"FT: {line}")
