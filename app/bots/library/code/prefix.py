"""Repeater lookup by hex prefix. Seeded from meshcore-bot's prefix command.

``prefix a1`` — who answers to that prefix (collision-aware);
``prefix free`` — 1-byte prefixes no known repeater uses.
"""

from remoteterm import bot

BOT_META = {
    "key": "prefix",
    "name": "prefix",
    "category": "Mesh",
    "description": "Repeater lookup by hex prefix; 'prefix free' lists unused ones",
    "version": "1.0.0",
}


@bot.on_keyword("prefix", "lookup")
async def prefix_lookup(ctx, msg):
    # Read-only lookups against this app's own contact table (allowed for
    # mesh-introspection bots).
    from app.repository import ContactRepository
    from app.repository.contacts import AmbiguousPublicKeyPrefixError

    arg = msg.arg_text.strip().lower()

    if arg == "free":
        contacts = await ContactRepository.get_all(limit=2000)
        used = {c.public_key[:2].lower() for c in contacts if c.type == 2}
        free = [f"{b:02x}" for b in range(256) if f"{b:02x}" not in used]
        await ctx.reply(
            f"{len(free)} of 256 one-byte prefixes unused by known repeaters. "
            f"First free: {', '.join(free[:12])}"
        )
        return

    cleaned = arg.removeprefix("0x")
    if not cleaned or len(cleaned) > 6 or any(c not in "0123456789abcdef" for c in cleaned):
        await ctx.reply("usage: prefix <1-3 byte hex> or 'prefix free' — e.g. prefix a1")
        return

    try:
        contact = await ContactRepository.get_by_key_prefix(cleaned)
    except AmbiguousPublicKeyPrefixError:
        await ctx.reply(f"Prefix {cleaned} COLLIDES — more than one known node starts with it.")
        return
    if contact is None:
        await ctx.reply(f"No known node with prefix {cleaned}.")
        return
    kinds = {0: "unknown", 1: "client", 2: "repeater", 3: "room", 4: "sensor"}
    kind = kinds.get(contact.type, "node")
    name = contact.name or "(unnamed)"
    await ctx.reply(f"{cleaned} = {name} ({kind}, key {contact.public_key[:12]}…)")
