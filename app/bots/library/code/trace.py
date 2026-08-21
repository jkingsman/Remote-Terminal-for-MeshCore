"""Active route trace along a path. Seeded from meshcore-bot's trace command.

``trace a1b2`` sends a real trace packet along those hops and reports each
node's name and SNR from the response. Uses the app's radio trace endpoint —
the radio must be connected, and slow round-trips may hit the 10s bot budget.
"""

from remoteterm import bot

BOT_META = {
    "key": "trace",
    "name": "trace",
    "category": "Mesh",
    "description": "Active route trace along a hex path (real trace packet)",
    "version": "1.0.0",
    "cooldown_seconds": 30,
    "settings_schema": [
        {
            "key": "hop_hash_bytes",
            "label": "Hop hash width (bytes)",
            "type": "select",
            "default": "1",
            "options": [
                {"value": "1", "label": "1 byte"},
                {"value": "2", "label": "2 bytes"},
                {"value": "4", "label": "4 bytes"},
            ],
        }
    ],
    "settings": {"hop_hash_bytes": "1"},
}


@bot.on_keyword("trace", "tracer")
async def run_trace(ctx, msg):
    arg = msg.arg_text.strip().lower().replace(":", "").replace(" ", "").removeprefix("0x")
    if not arg or any(c not in "0123456789abcdef" for c in arg):
        await ctx.reply("usage: trace <hex path> — e.g. trace a1b2 (hop width in bot settings)")
        return

    width = int(ctx.settings.get("hop_hash_bytes", "1") or "1")
    if width not in (1, 2, 4):
        width = 1
    chars = width * 2
    if len(arg) % chars != 0:
        await ctx.reply(f"path length doesn't divide into {width}-byte hops")
        return
    hops_hex = [arg[i : i + chars] for i in range(0, len(arg), chars)]
    if len(hops_hex) > 8:
        await ctx.reply("Max 8 hops per trace.")
        return

    if ctx.is_test:
        await ctx.reply(f"(test run — trace of {len(hops_hex)} hop(s) not transmitted)")
        return

    # Drives the app's own radio trace endpoint (mesh-introspection bot).
    from fastapi import HTTPException

    from app.models import RadioTraceHopRequest, RadioTraceRequest
    from app.routers.radio import trace_path

    try:
        result = await trace_path(
            RadioTraceRequest(
                hop_hash_bytes=width,  # type: ignore[arg-type]  # validated to 1|2|4 above
                hops=[RadioTraceHopRequest(hop_hex=h) for h in hops_hex],
            )
        )
    except HTTPException as exc:
        await ctx.reply(f"Trace failed: {exc.detail}")
        return

    parts = []
    for node in result.nodes:
        label = node.name or node.observed_hash or "?"
        snr = f" {node.snr:.1f}dB" if node.snr is not None else ""
        parts.append(f"{label}{snr}")
    await ctx.reply(f"Trace ({result.path_len} hops): {' > '.join(parts)}"[:180])
