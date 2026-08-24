"""Solar indices from HamQSL (hamqsl.com/solarxml.php).

Usage: ``solar``. Reports solar flux (SFI), sunspot number, A-index and
K-index, plus the HamQSL update hour.
"""

import re
import xml.etree.ElementTree as ElementTree

from remoteterm import bot

BOT_META = {
    "key": "solar",
    "name": "solar",
    "category": "Solar",
    "description": "Solar indices: SFI, sunspots, A/K index (HamQSL)",
    "version": "1.0.0",
    "cooldown_seconds": 5,
}

_URL = "https://www.hamqsl.com/solarxml.php"


@bot.on_keyword("solar")
async def solar(ctx, msg):
    try:
        raw = await ctx.http.get_text(_URL)
        data = ElementTree.fromstring(raw).find("solardata")
        if data is None:
            raise ValueError("no solardata element")
        sfi = (data.findtext("solarflux") or "?").strip()
        ssn = (data.findtext("sunspots") or "?").strip()
        a_index = (data.findtext("aindex") or "?").strip()
        k_index = (data.findtext("kindex") or "?").strip()
        updated = (data.findtext("updated") or "").strip()
    except Exception:  # httpx.HTTPError / XML shape surprises (ctx.http owns the client)
        await ctx.reply(ctx.t("rt.error_upstream"))
        return
    stamp = re.search(r"(\d{2})\d{2} GMT", updated)
    suffix = f" — updated {stamp.group(1)}Z" if stamp else ""
    await ctx.reply(f"SFI {sfi} SSN {ssn} A {a_index} K {k_index}{suffix}")
