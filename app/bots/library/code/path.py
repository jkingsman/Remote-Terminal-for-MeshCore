"""Decode a routing path into repeater names. Seeded from meshcore-bot's path command.

``path`` with no argument decodes the path the triggering message itself took;
``path a1b2c3`` decodes a pasted hex path. Hop identifiers are resolved against
the contact list (this app's own mesh view), collision-aware.
"""

from remoteterm import bot

BOT_META = {
    "key": "path",
    "name": "path",
    "category": "Mesh",
    "description": "Decode a routing path into repeater names",
    "version": "1.0.0",
    "settings_schema": [
        {
            "key": "hop_width",
            "label": "Hop width for pasted paths (bytes)",
            "type": "select",
            "default": "1",
            "options": [
                {"value": "1", "label": "1 byte"},
                {"value": "2", "label": "2 bytes"},
                {"value": "3", "label": "3 bytes"},
            ],
            "help": "Used only when decoding a pasted hex path; the message's own path carries its width.",
        }
    ],
    "settings": {"hop_width": "1"},
}


def _split_hops(path_hex: str, width_bytes: int) -> list[str] | None:
    chars = width_bytes * 2
    if len(path_hex) % chars != 0:
        return None
    return [path_hex[i : i + chars] for i in range(0, len(path_hex), chars)]


async def _resolve_hops(hops: list[str]) -> str:
    # Read-only lookup against this app's own contact table (allowed for
    # mesh-introspection bots).
    from app.repository import ContactRepository

    resolved = await ContactRepository.resolve_prefixes(hops)
    parts: list[str] = []
    for hop in hops:
        contact = resolved.get(hop.lower()) or resolved.get(hop.upper()) or resolved.get(hop)
        if contact is not None and contact.name:
            parts.append(f"{hop}={contact.name}")
        else:
            parts.append(f"{hop}=?")
    return " > ".join(parts)


@bot.on_keyword("path", "decode", "route")
async def decode_path(ctx, msg):
    arg = msg.arg_text.strip().replace(":", "").replace(" ", "")

    if arg:
        cleaned = arg.lower().removeprefix("0x")
        if not cleaned or any(c not in "0123456789abcdef" for c in cleaned):
            await ctx.reply(
                "usage: path [hex] — e.g. path a1b2c3 (or bare 'path' on a routed message)"
            )
            return
        width = int(ctx.settings.get("hop_width", "1") or "1")
        hops = _split_hops(cleaned, width)
        if hops is None:
            await ctx.reply(
                f"path length doesn't divide into {width}-byte hops — check hop width setting"
            )
            return
    else:
        if not msg.path:
            await ctx.reply("This message arrived direct (no path). Paste one: path a1b2c3")
            return
        width = msg.path_bytes_per_hop or 1
        hops = _split_hops(msg.path.lower(), width)
        if hops is None:
            await ctx.reply("Couldn't split this message's path into hops.")
            return

    if len(hops) > 16:
        await ctx.reply("Path too long to decode (max 16 hops).")
        return
    rendered = await _resolve_hops(hops)
    await ctx.reply(f"{len(hops)} hop{'s' if len(hops) != 1 else ''}: {rendered}"[:180])
