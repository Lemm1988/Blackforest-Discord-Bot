"""Kleinere Fun-Befehle: Zitate, Guten-Morgen-Nachrichten, Begrüßung, Hilfe."""
import random

import discord
from discord.ext import commands

import config
import database
import mood


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
        self.gumo_responses = _load_lines(config.GUMO_FILE)

    @commands.hybrid_command(name="zitat", description="Zeigt ein zufälliges Zitat.")
    async def zitat(self, ctx: commands.Context):
        if not self.zitate:
            await ctx.send("Aktuell sind keine Zitate hinterlegt.")
            return
        await ctx.send(random.choice(self.zitate))

    @commands.hybrid_command(name="gumo", description="Wünscht dir einen guten Morgen.")
    async def gumo(self, ctx: commands.Context):
        if not self.gumo_responses:
            await ctx.send(f"Guten Morgen, {ctx.author.name}!")
            return
        response = random.choice(self.gumo_responses)
        await ctx.send(response.format(name=ctx.author.name))

    @commands.hybrid_command(name="hallo", description="Begrüßt dich auf dem Server.")
    async def hallo(self, ctx: commands.Context):
        neutral_text = (
            f"Guten Tag {ctx.author.name}\n\n"
            "Herzlich Willkommen auf dem Discord Server, ich hoffe du hast Spaß hier auf dem Server.\n"
            "Bei Fragen wende dich gerne an unser \n***Moderatoren Team*** oder an ***Blackforest Craft&Grow*** direkt"
        )
        # personality_enabled=0 -> IMMER die bisherige neutrale Nachricht, exakt
        # wie vor dieser Phase (keine Regression). Auch bei personality_enabled=1
        # nur MANCHMAL gefaerbt (Wuerfeln gegen personality_response_frequency),
        # nicht IMMER - Haeufigkeit/Ton sind Admin-Settings, keine Festwerte.
        if database.get_setting("personality_enabled", config.DEFAULT_SETTINGS["personality_enabled"]) == "1":
            frequency = database.get_setting(
                "personality_response_frequency", config.DEFAULT_SETTINGS["personality_response_frequency"]
            )
            if mood.roll_personality_response(frequency):
                humor_level = database.get_setting(
                    "personality_humor_level", config.DEFAULT_SETTINGS["personality_humor_level"]
                )
                current_mood = database.get_bot_state("mood", "neutral")
                pool = database.get_personality_text_pool("hello")
                colored = mood.pick_variant(pool, current_mood, humor_level, name=ctx.author.name)
                if colored:
                    await ctx.send(colored)
                    return
        await ctx.send(neutral_text)

    @commands.hybrid_command(name="bothelp", description="Zeigt alle verfügbaren Befehle per DM.")
    async def bothelp(self, ctx: commands.Context):
        lines = ["Hi! Ich bin der Helferbot hier auf dem Server. Hier sind alle Befehle, die du aktuell nutzen kannst:\n"]
        for command in sorted(self.bot.commands, key=lambda c: c.name):
            if command.hidden:
                continue
            description = command.description or command.short_doc or "Keine Beschreibung."
            lines.append(f"`>{command.name}` – {description}")
        try:
            await ctx.author.send("\n".join(lines))
            if ctx.interaction is None:
                await ctx.send(f"{ctx.author.mention}, ich habe dir die Befehlsübersicht per DM geschickt!")
        except discord.Forbidden:
            await ctx.send(f"{ctx.author.mention}, ich konnte dir keine Direktnachricht schicken. Bitte überprüfe deine DM-Einstellungen!")


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
