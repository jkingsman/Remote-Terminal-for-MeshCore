"""A pocketful of short, true fun facts."""

import random

from remoteterm import bot

BOT_META = {
    "key": "funfact",
    "name": "funfact",
    "category": "Fun",
    "description": "Random fun facts",
    "version": "1.0.0",
}

FACTS = (
    "Honey never spoils — sealed honey found in ancient tombs was still edible.",
    "Octopuses have three hearts and blue blood.",
    "A day on Venus is longer than its year.",
    "Bananas are berries; strawberries are not.",
    "Sunlight takes about 8 minutes to reach Earth.",
    "Sharks existed before trees appeared on Earth.",
    "The Eiffel Tower grows several centimeters taller in summer heat.",
    "At its triple point, water can boil and freeze at the same time.",
    "A group of crows is called a murder.",
    "Sloths can hold their breath longer than dolphins can.",
    "The human body contains enough iron to make a small nail.",
    "Wombats produce cube-shaped droppings.",
    "Scotland's national animal is the unicorn.",
    "There are more possible chess games than atoms in the observable universe.",
    "Venus is the hottest planet, even though Mercury is closer to the Sun.",
    "An octopus can taste with its arms.",
    "Sound travels roughly four times faster in water than in air.",
    "The Moon drifts about 3.8 cm farther from Earth every year.",
    "Some turtles can absorb oxygen through their rear ends while hibernating.",
    "A lightning bolt is about five times hotter than the Sun's surface.",
    "Butterflies taste with their feet.",
    "A blue whale's heart can weigh over 180 kg.",
    "Antarctica is the largest desert on Earth.",
    "Cows form close friendships and get stressed when separated.",
    "Polar bear skin is black under all that white fur.",
    "The Great Wall of China is not visible from the Moon with the naked eye.",
    "Olympus Mons on Mars is the tallest volcano in the solar system.",
    "Sea otters hold hands while sleeping so they don't drift apart.",
    "Honeybees can recognize human faces.",
    "Oxford University is older than the Aztec Empire.",
    "There are more trees on Earth than stars in the Milky Way.",
    "The first computer bug was an actual moth found in a relay in 1947.",
    "Human radio broadcasts have been traveling into space for about a century.",
    "GPS satellites must correct for Einstein's relativity to stay accurate.",
    "The International Space Station orbits Earth about every 90 minutes.",
    "Voyager 1, launched in 1977, is the most distant human-made object.",
    "Sound cannot travel through the vacuum of space.",
    "Hawaii drifts several centimeters closer to Japan every year.",
    "A teaspoon of neutron star material would weigh billions of tons.",
    "A hummingbird's heart can beat more than 1,200 times per minute.",
)


@bot.on_keyword("funfact")
async def funfact(ctx, msg):
    await ctx.reply(random.choice(FACTS))
