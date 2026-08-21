"""Reports the running app version and the bot library version."""

from remoteterm import bot

BOT_META = {
    "key": "version",
    "name": "version",
    "category": "Basic",
    "description": "Shows the app and bot library versions",
    "version": "1.0.0",
}


@bot.on_keyword("version", "ver")
async def version(ctx, msg):
    # Read-only import of RemoteTerm's own version metadata — this built-in is
    # allowed to introspect the app it ships with (exception to stdlib-only).
    from app.version_info import get_app_build_info

    info = get_app_build_info()
    commit = f" ({info.commit_hash[:7]})" if info.commit_hash else ""
    await ctx.reply(f"RemoteTerm {info.version}{commit} | bot library {BOT_META['version']}")
