"""Random cat facts from catfact.ninja."""

from remoteterm import bot

BOT_META = {
    "key": "catfact",
    "name": "catfact",
    "category": "Fun",
    "description": "Random cat facts",
    "version": "1.0.0",
}


@bot.on_keyword("catfact", "meow", "purr")
async def catfact(ctx, msg):
    try:
        data = await ctx.http.get_json("https://catfact.ninja/fact")
    except Exception as exc:  # httpx errors surface here; ctx.http owns the client
        ctx.log(f"catfact.ninja failed: {exc}", level="WARNING")
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    fact = str(data.get("fact") or "").strip()
    if not fact:
        await ctx.reply(ctx.t("rt.no_results"))
        return
    if len(fact) > 170:
        fact = fact[:167] + "..."
    await ctx.reply(fact)
