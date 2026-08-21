"""Lists the hashtag channels this node knows about."""

from remoteterm import bot

BOT_META = {
    "key": "channels",
    "name": "channels",
    "category": "Basic",
    "description": "Lists known hashtag channels",
    "version": "1.0.0",
}

_MAX_LINE = 160


@bot.on_keyword("channels", "channel")
async def list_channels(ctx, msg):
    # Mesh introspection: read-only import of RemoteTerm's own channel
    # repository — an allowed exception to the stdlib-only import rule.
    from app.repository import ChannelRepository

    all_channels = await ChannelRepository.get_all()
    names = sorted(
        {"#" + ch.name.lstrip("#") for ch in all_channels if ch.is_hashtag and ch.name.strip()},
        key=str.lower,
    )
    if not names:
        await ctx.reply(ctx.t("rt.no_results"))
        return

    lines: list[str] = []
    current = ""
    shown = 0
    for name in names:
        candidate = f"{current} {name}" if current else name
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
        lines[-1] += f" +{extra} more"
    for line in lines:
        await ctx.reply(line)
