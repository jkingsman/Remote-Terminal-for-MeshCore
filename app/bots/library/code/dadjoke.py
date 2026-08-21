"""Dad jokes from icanhazdadjoke.com."""

from remoteterm import bot

BOT_META = {
    "key": "dadjoke",
    "name": "dadjoke",
    "category": "Fun",
    "description": "Random dad jokes",
    "version": "1.0.0",
}


@bot.on_keyword("dadjoke", "dad joke", "dadjokes", "dad jokes")
async def dadjoke(ctx, msg):
    try:
        data = await ctx.http.get_json(
            "https://icanhazdadjoke.com/", headers={"Accept": "application/json"}
        )
    except Exception as exc:  # httpx errors surface here; ctx.http owns the client
        ctx.log(f"icanhazdadjoke failed: {exc}", level="WARNING")
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    joke = str(data.get("joke") or "").strip()
    if not joke:
        await ctx.reply(ctx.t("rt.no_results"))
        return
    if len(joke) > 178:
        joke = joke[:175] + "..."
    await ctx.reply(joke)
