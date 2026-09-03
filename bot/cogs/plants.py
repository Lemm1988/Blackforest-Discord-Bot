"""Zufällige Pflanzen-Infos (>pflanze)."""
import random

from discord.ext import commands

from plantinfos import plantinfos


class Plants(commands.Cog):
    # Fuer die Gruppierung in /help. Das Label steht am Cog und nicht in
    # einer zentralen Liste, damit ein entferntes Modul auch aus der Hilfe
    # verschwindet, ohne dass man daran denken muss.
    MODUL_LABEL = "🪴 Pflanzeninfos"
    MODUL_REIHENFOLGE = 3
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="pflanze", description="Wissenswertes zu einer zufälligen Pflanze",
        extras={"hilfe": "Ich suche dir eine zufällige Pflanze heraus und erzähle dir etwas darüber."},)
    async def pflanze(self, ctx: commands.Context):
        selected_plant = random.choice(plantinfos)
        await ctx.send(embed=selected_plant)


async def setup(bot: commands.Bot):
    await bot.add_cog(Plants(bot))
