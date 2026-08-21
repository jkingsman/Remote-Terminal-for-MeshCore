"""Compact live/final scores from ESPN's public scoreboard API."""

from remoteterm import bot

BOT_META = {
    "key": "sports",
    "name": "sports",
    "category": "Sports",
    "description": "Live and final scores from ESPN (NFL, NBA, MLB, NHL, MLS)",
    "version": "1.0.0",
    "settings_schema": [
        {
            "key": "league",
            "label": "League",
            "type": "select",
            "default": "nfl",
            "options": [
                {"value": "nfl", "label": "NFL"},
                {"value": "nba", "label": "NBA"},
                {"value": "mlb", "label": "MLB"},
                {"value": "nhl", "label": "NHL"},
                {"value": "mls", "label": "MLS"},
            ],
        },
        {
            "key": "teams",
            "label": "Teams filter",
            "type": "text",
            "default": "",
            "help": "Optional comma-separated team abbreviations (e.g. SEA,SF)",
        },
    ],
    "settings": {"league": "nfl", "teams": ""},
}

_LEAGUE_PATHS = {
    "nfl": "football/nfl",
    "nba": "basketball/nba",
    "mlb": "baseball/mlb",
    "nhl": "hockey/nhl",
    "mls": "soccer/usa.1",
}

_MAX_REPLY = 170


def _parse_game(event):
    """Return ('SEA 21 @ SF 17 Q3', state, {'SEA', 'SF'}) for one event, or None."""
    competitions = event.get("competitions") or [{}]
    away = home = None
    for side in competitions[0].get("competitors") or []:
        if side.get("homeAway") == "home":
            home = side
        elif side.get("homeAway") == "away":
            away = side
    if not away or not home:
        return None
    status = (event.get("status") or {}).get("type") or {}
    state = str(status.get("state") or "pre")
    detail = str(status.get("shortDetail") or "").replace(" - ", " ").strip()

    def _abbr(side):
        return str((side.get("team") or {}).get("abbreviation") or "?")

    away_abbr, home_abbr = _abbr(away), _abbr(home)
    line = f"{away_abbr} {away.get('score', '?')} @ {home_abbr} {home.get('score', '?')} {detail}"
    return line.strip(), state, {away_abbr.upper(), home_abbr.upper()}


@bot.on_keyword("sports", "score", "scores")
async def sports(ctx, msg):
    league = str(ctx.settings.get("league") or "nfl").lower()
    args = [a.strip() for a in msg.args if a.strip()]
    if args and args[0].lower() in _LEAGUE_PATHS:
        league = args[0].lower()
        args = args[1:]
    path = _LEAGUE_PATHS.get(league, _LEAGUE_PATHS["nfl"])

    try:
        data = await ctx.http.get_json(
            f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
        )
    except Exception as exc:  # httpx errors surface here; ctx.http owns the client
        ctx.log(f"ESPN scoreboard failed: {exc}", level="WARNING")
        await ctx.reply(ctx.t("rt.error_upstream"))
        return

    teams_setting = str(ctx.settings.get("teams") or "")
    wanted = {t.strip().upper() for t in teams_setting.split(",") if t.strip()}
    if args:
        wanted = {args[0].upper()}

    lines = []
    for event in data.get("events") or []:
        parsed = _parse_game(event)
        if parsed is None:
            continue
        line, state, team_abbrs = parsed
        if state not in ("in", "post"):
            continue
        if wanted and not (wanted & team_abbrs):
            continue
        lines.append(line)
        if len(lines) == 3:
            break

    if not lines:
        await ctx.reply(ctx.t("rt.no_results"))
        return

    replies = []
    current = ""
    for line in lines:
        candidate = f"{current} | {line}" if current else line
        if not current or len(candidate) <= _MAX_REPLY:
            current = candidate
        else:
            replies.append(current)
            current = line
    if current:
        replies.append(current)
    for text in replies[:2]:
        await ctx.reply(text)
