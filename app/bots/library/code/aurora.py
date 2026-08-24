"""Aurora outlook from the NOAA SWPC 1-minute planetary K index.

Usage: ``aurora`` or ``kp``. Maps the current Kp to the NOAA G storm scale
and adds a short visibility hint.
"""

from remoteterm import bot

BOT_META = {
    "key": "aurora",
    "name": "aurora",
    "category": "Solar",
    "description": "Kp index and aurora outlook (NOAA SWPC)",
    "version": "1.0.0",
    "cooldown_seconds": 5,
}

_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"


def _describe(kp: float) -> tuple[str, str]:
    """(label, visibility hint) for a Kp value, on the NOAA G scale."""
    if kp >= 9.0:
        return "G5 extreme storm", "aurora possible far south"
    if kp >= 8.0:
        return "G4 severe storm", "aurora possible far south"
    if kp >= 7.0:
        return "G3 strong storm", "aurora possible to mid latitudes"
    if kp >= 6.0:
        return "G2 moderate storm", "aurora visible at high latitudes"
    if kp >= 5.0:
        return "G1 storm", "aurora possible at high latitudes"
    if kp >= 4.0:
        return "active", "aurora possible at very high latitudes"
    return "quiet", "aurora unlikely"


@bot.on_keyword("aurora", "kp")
async def aurora(ctx, msg):
    try:
        rows = await ctx.http.get_json(_URL)
        last = rows[-1]
        kp = last.get("estimated_kp")
        if kp is None:
            kp = last["kp_index"]
        kp = float(kp)
    except Exception:  # httpx.HTTPError / payload shape surprises (ctx.http owns the client)
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    label, hint = _describe(kp)
    await ctx.reply(f"Kp {kp:.1f} ({label}) — {hint}")
