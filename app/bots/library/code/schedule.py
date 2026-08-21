"""Lists upcoming scheduled messages. Seeded from meshcore-bot's schedule command."""

from datetime import datetime

from remoteterm import bot

BOT_META = {
    "key": "schedule",
    "name": "schedule",
    "category": "Admin",
    "description": "Lists the next scheduled messages",
    "version": "1.0.0",
    "respond_to_dms": True,
}


@bot.on_keyword("schedule")
async def list_schedule(ctx, msg):
    # Read-only lookup against this app's own schedules table (allowed for
    # mesh-introspection bots).
    from app.bots.cron import parse_cron
    from app.repository.bots import BotScheduleRepository

    schedules = await BotScheduleRepository.get_all()
    upcoming: list[tuple[datetime, str]] = []
    now = datetime.now()
    for schedule in schedules:
        if not schedule.enabled:
            continue
        try:
            nxt = parse_cron(schedule.cron).next_fire(now)
        except Exception:
            continue
        if nxt is not None:
            upcoming.append((nxt, schedule.label))

    if not upcoming:
        await ctx.reply("No scheduled messages are enabled.")
        return
    upcoming.sort()
    lines = [f"{when.strftime('%a %H:%M')} {label}" for when, label in upcoming[:3]]
    more = f" (+{len(upcoming) - 3} more)" if len(upcoming) > 3 else ""
    await ctx.reply(f"Next: {' | '.join(lines)}{more}")
