"""Fortune cookies for the mesh. All fortunes written fresh for RemoteTerm."""

import random

from remoteterm import bot

BOT_META = {
    "key": "fortunes",
    "name": "fortunes",
    "category": "Fun",
    "description": "Cracks open a fortune cookie",
    "version": "1.0.0",
}

FORTUNES = (
    "A message you almost didn't send will matter most.",
    "Your patience is a signal; someone distant is receiving it.",
    "Good news travels slowly but arrives intact.",
    "The quiet channel holds the loudest opportunity.",
    "An unexpected hop brings an unexpected friend.",
    "Fortune favors the well-placed antenna.",
    "You will find what you seek two nodes away.",
    "A small kindness today echoes for many hops.",
    "The answer you await is already in transit.",
    "Listen twice, transmit once, prosper always.",
    "A closed door hides an open relay.",
    "Your next idea deserves more power than you plan to give it.",
    "Someone remembers your help long after you forgot giving it.",
    "Clear skies ahead; keep your line of sight.",
    "The detour you dread becomes the story you tell.",
    "Persistence beats signal strength.",
    "An old contact returns with new coordinates.",
    "What you practice quietly will be praised publicly.",
    "The best time to raise an antenna was yesterday; the second best is today.",
    "A short message can carry a long friendship.",
    "Luck is loudest where preparation meets propagation.",
    "You are closer to the summit than the map suggests.",
    "Share what you know; it multiplies in the sharing.",
    "A stranger's question leads you to your next project.",
    "Redundancy today prevents regret tomorrow.",
    "Your curiosity is a compass; follow it uphill.",
    "Slow progress is still propagation.",
    "The network grows because you showed up.",
    "Something lost returns by an unlikely route.",
    "Trust the process, verify the checksum.",
    "A good night's sleep improves your signal-to-noise ratio.",
    "Tomorrow brings a clear frequency and a clearer mind.",
    "Help offered freely returns amplified.",
    "The mountain does not move, but your repeater might.",
    "Your smallest habit is quietly compounding.",
    "An overlooked detail proves valuable this week.",
    "Speak less, mean more, reach farther.",
    "New paths open when old assumptions retire.",
    "You will be the good news in someone's feed.",
    "Adventure begins at the edge of coverage.",
    "The favor you forgot is remembered fondly.",
    "Keep your promises short and your memory long.",
    "A change in weather brings a change in luck.",
    "What seems like noise today decodes tomorrow.",
    "Generosity is the strongest signal you can send.",
    "Your backup plan becomes the main event.",
    "A friendly ping opens a lasting link.",
    "Doubt is fog; movement is wind.",
    "The best conversations start with hello.",
    "Every expert was once lost without a map.",
)


@bot.on_keyword("fortune")
async def fortune(ctx, msg):
    await ctx.reply(random.choice(FORTUNES))
