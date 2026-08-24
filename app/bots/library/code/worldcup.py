"""FIFA World Cup scores for today, from ESPN's public scoreboard.

Usage: ``wc`` or ``worldcup``. Up to three matches in one compact reply:
live/finished matches as "ARG 2-1 FRA (FT)", scheduled ones as
"ARG vs FRA (7:00 PM)".
"""

from remoteterm import bot

BOT_META = {
    "key": "worldcup",
    "name": "worldcup",
    "category": "Sports",
    "description": "World Cup scores today (ESPN scoreboard)",
    "version": "1.0.0",
    "cooldown_seconds": 5,
}

_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"


def _clip(text: str, limit: int = 180) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _format_event(event: dict) -> str:
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    status = (event.get("status") or {}).get("type") or {}
    detail = str(status.get("shortDetail") or "").strip()
    if not (home and away):
        name = str(event.get("shortName") or event.get("name") or "match")[:40]
        return f"{name} ({detail})" if detail else name
    home_abbr = (home.get("team") or {}).get("abbreviation") or "?"
    away_abbr = (away.get("team") or {}).get("abbreviation") or "?"
    if status.get("state") == "pre":
        line = f"{home_abbr} vs {away_abbr}"
    else:
        line = f"{home_abbr} {home.get('score', '?')}-{away.get('score', '?')} {away_abbr}"
    return f"{line} ({detail})" if detail else line


@bot.on_keyword("wc", "worldcup")
async def worldcup(ctx, msg):
    try:
        events = (await ctx.http.get_json(_URL)).get("events") or []
    except Exception:  # httpx.HTTPError / payload shape surprises (ctx.http owns the client)
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    if not events:
        await ctx.reply("No World Cup matches today.")
        return
    lines = [_format_event(event) for event in events[:3]]
    await ctx.reply(_clip("\n".join(lines)))
