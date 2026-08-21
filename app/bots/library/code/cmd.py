"""Lists the keyword commands this node currently answers to."""

from remoteterm import bot

BOT_META = {
    "key": "cmd",
    "name": "cmd",
    "category": "Basic",
    "description": "Lists enabled keyword commands",
    "version": "1.0.0",
}

_MAX_LINE = 150


@bot.on_keyword("cmd", "commands")
async def list_commands(ctx, msg):
    names = sorted({b["name"] for b in ctx.get_enabled_bots() if b["keywords"]}, key=str.lower)
    if not names:
        await ctx.reply("No keyword commands enabled — see the Bots tab")
        return

    lines: list[str] = []
    current = ""
    shown = 0
    for name in names:
        candidate = f"{current}, {name}" if current else name
        if not current or len(candidate) <= _MAX_LINE:
            current = candidate
            shown += 1
            continue
        lines.append(current)
        if len(lines) == 2:
            break
        current = name
        shown += 1
    if len(lines) < 2 and current:
        lines.append(current)

    extra = len(names) - shown
    if extra > 0:
        lines[-1] += f" +{extra} more — see the Bots tab"
    for line in lines:
        await ctx.reply(line)
