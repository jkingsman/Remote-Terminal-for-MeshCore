"""HF band conditions from HamQSL (hamqsl.com/solarxml.php).

Usage: ``hfcond``. Two compact replies: day and night propagation for the
80m-40m / 30m-20m / 17m-15m / 12m-10m bands.
"""

import xml.etree.ElementTree as ElementTree

from remoteterm import bot

BOT_META = {
    "key": "hfcond",
    "name": "hfcond",
    "category": "Solar",
    "description": "HF band conditions, day and night (HamQSL)",
    "version": "1.0.0",
    "cooldown_seconds": 5,
}

_URL = "https://www.hamqsl.com/solarxml.php"


@bot.on_keyword("hfcond")
async def hfcond(ctx, msg):
    try:
        raw = await ctx.http.get_text(_URL)
        root = ElementTree.fromstring(raw)
        rows = []
        for band in root.findall("./solardata/calculatedconditions/band"):
            period = (band.get("time") or "").strip().lower()
            name = (band.get("name") or "").strip()
            condition = (band.text or "").strip()
            if period and name and condition:
                rows.append((period, name, condition))
    except Exception:  # httpx.HTTPError / XML shape surprises (ctx.http owns the client)
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    if not rows:
        await ctx.reply(ctx.t("rt.no_results"))
        return
    for period in ("day", "night"):
        parts = [f"{name}={cond}" for row_period, name, cond in rows if row_period == period]
        if parts:
            await ctx.reply(f"HF {period}: " + " ".join(parts))
