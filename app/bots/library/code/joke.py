"""Jokes from JokeAPI (v2.jokeapi.dev), safe-mode only."""

from remoteterm import bot

BOT_META = {
    "key": "joke",
    "name": "joke",
    "category": "Fun",
    "description": "Random jokes (safe mode)",
    "version": "1.0.0",
}


def _trim(text):
    text = text.strip()
    return text if len(text) <= 178 else text[:175] + "..."


@bot.on_keyword("joke", "jokes")
async def joke(ctx, msg):
    try:
        data = await ctx.http.get_json("https://v2.jokeapi.dev/joke/Any?safe-mode")
    except Exception as exc:  # httpx errors surface here; ctx.http owns the client
        ctx.log(f"jokeapi failed: {exc}", level="WARNING")
        await ctx.reply(ctx.t("rt.error_upstream"))
        return

    kind = data.get("type")
    if kind == "single":
        text = str(data.get("joke") or "").strip()
        if text:
            await ctx.reply(_trim(text))
            return
    elif kind == "twopart":
        setup = str(data.get("setup") or "").strip()
        delivery = str(data.get("delivery") or "").strip()
        if setup and delivery:
            await ctx.reply(_trim(setup))
            await ctx.reply(_trim(delivery))
            return
    await ctx.reply(ctx.t("rt.no_results"))
