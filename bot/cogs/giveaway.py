"""Giveaway-Logik: Erstellen, Teilnahme per 🎉-Reaktion, automatische Gewinnerziehung.

Im Gegensatz zum alten Bot werden laufende Giveaways in der DB persistiert
(Tabelle giveaways) statt nur im Prozessspeicher zu leben - ein Bot-Neustart
bricht ein laufendes Giveaway dadurch nicht mehr ab.
"""
import asyncio
import random
from datetime import datetime, timezone

import discord
from discord.ext import commands

import database


class Giveaway(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Beim Start alle noch nicht beendeten Giveaways neu einplanen (überlebt Neustarts).
        for row in database.get_active_giveaways():
            end_time = datetime.fromisoformat(row["end_time"])
            self.bot.loop.create_task(self._schedule_end(row["message_id"], row["channel_id"], end_time))

    async def create_giveaway(self, channel_id: int, title: str, description: str, end_time: datetime, color: int):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} nicht gefunden")

        embed = discord.Embed(title=title, description=description, color=color)
        embed.set_footer(text=f"Endet am: {end_time.strftime('%d.%m.%Y %H:%M:%S UTC')}")
        message = await channel.send(embed=embed)
        await message.add_reaction("🎉")

        database.create_giveaway(message.id, channel_id, title, end_time.isoformat())
        self.bot.loop.create_task(self._schedule_end(message.id, channel_id, end_time))
        return message

    async def _schedule_end(self, message_id, channel_id, end_time: datetime):
        now = datetime.now(timezone.utc)
        delay = (end_time - now).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            database.end_giveaway(message_id, None)
            return

        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            database.end_giveaway(message_id, None)
            return

        reaction = discord.utils.get(message.reactions, emoji="🎉")
        winner = None
        if reaction:
            participants = [user async for user in reaction.users() if not user.bot]
            if participants:
                winner = random.choice(participants)

        if winner:
            await channel.send(f"🎉 Herzlichen Glückwunsch {winner.mention}! Du hast das Gewinnspiel gewonnen!")
            # Server-Statistik-Tracking (Phase 11) - nur bei einem tatsaechlich
            # gezogenen Gewinner, die bestehende Ankuendigungs-Nachricht oben
            # bleibt unveraendert (deckt "User gewinnt Spiel" bereits ab).
            database.increment_server_stat("games_count")
        else:
            await channel.send("Kein gültiger Teilnehmer für das Gewinnspiel gefunden.")

        database.end_giveaway(message_id, winner.id if winner else None)


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaway(bot))
