"""Kleinere Fun-Befehle.

Fruehere Bewohner dieser Datei und ihr Verbleib (03.09.2026, Freigabe Patrick):

- >gumo  entfernt. Ersetzt durch die taegliche, automatische
         Guten-Morgen-Nachricht (cogs/personality.py,
         _maybe_send_morning_greeting). Der Befehl war ausserdem defekt: Die
         Datei bot/data/gumo.txt enthielt Python-Quelltext statt reiner
         Textzeilen, der Bot postete deshalb Dinge wie
         '"Guten Morgen, {name}!",' oder 'gumo_responses = ['.
- >hallo  entfernt. Reine Altlast des ersten Bots; neue Mitglieder werden ueber
         die Willkommensnachricht begruesst (Textpool "welcome").
- >bothelp entfernt zugunsten von /help, das im Gegensatz dazu nur die Befehle
         zeigt, die der Aufrufer auch benutzen darf.
"""
import random

from discord.ext import commands

import config


def _load_lines(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []


class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.zitate = _load_lines(config.ZITATE_FILE)

    @commands.hybrid_command(name="zitat", description="Zeigt ein zufälliges Zitat.")
    async def zitat(self, ctx: commands.Context):
        if not self.zitate:
            await ctx.send("Aktuell sind keine Zitate hinterlegt.")
            return
        await ctx.send(random.choice(self.zitate))


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
