"""Zufällige Pflanzen-Infos (>pflanze)."""
import random

from discord.ext import commands

from plantinfos import plantinfos


class Plants(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="pflanze", description="Zeigt zufällige Infos zu einer Pflanze.")
    async def pflanze(self, ctx: commands.Context):
        selected_plant = random.choice(plantinfos)
        await ctx.send(embed=selected_plant)


async def setup(bot: commands.Bot):
    await bot.add_cog(Plants(bot))
