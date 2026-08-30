"""Regel-Reaktion: 👍 auf die Regel-Nachricht vergibt/entzieht die Member-Rolle.

Bewusst getrennt vom Giveaway-Cog (im alten Bot waren beide Aufgaben in einer
on_raw_reaction_add-Funktion vermischt).
"""
import discord
from discord.ext import commands

import config
import database


class Reactions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _handle(self, payload: discord.RawReactionActionEvent, adding: bool):
        if database.get_setting("role_reaction_enabled", config.DEFAULT_SETTINGS["role_reaction_enabled"]) != "1":
            return
        rules_message_id = int(database.get_setting("rules_message_id", config.DEFAULT_SETTINGS["rules_message_id"]))
        if payload.message_id != rules_message_id or str(payload.emoji) != "👍":
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        channel = guild.get_channel(payload.channel_id)
        rules_channel_name = database.get_setting("rules_channel_name", config.DEFAULT_SETTINGS["rules_channel_name"])
        if not channel or channel.name != rules_channel_name:
            return

        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        role_name = database.get_setting("member_role_name", config.DEFAULT_SETTINGS["member_role_name"])
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            return

        try:
            if adding:
                await member.add_roles(role)
                await member.send(f"{member.mention}, du hast die Regeln akzeptiert und die Rolle **{role_name}** erhalten!")
            else:
                await member.remove_roles(role)
                await member.send(f"{member.mention}, du hast die Regeln widerrufen und die Rolle **{role_name}** wurde entfernt!")
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle(payload, adding=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle(payload, adding=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reactions(bot))
