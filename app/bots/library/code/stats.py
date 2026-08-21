"""Mesh statistics digest. Seeded from meshcore-bot's stats command."""

from remoteterm import bot

BOT_META = {
    "key": "stats",
    "name": "stats",
    "category": "Mesh",
    "description": "24h mesh statistics digest",
    "version": "1.0.0",
}


@bot.on_keyword("stats")
async def mesh_stats(ctx, msg):
    stats = await ctx.mesh_stats()
    await ctx.reply(
        f"Mesh 24h: {stats['messages_24h']} msgs · {stats['contacts_24h']} nodes heard · "
        f"{stats['repeaters_24h']} repeaters up · {stats['new_contacts_7d']} new this week"
    )
    await ctx.reply(
        f"Known: {stats['total_contacts']} contacts, {stats['total_repeaters']} repeaters"
    )
