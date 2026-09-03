"""Modul "XP & Level" - serverweiter Fortschritt.

Am 03.09.2026 aus cogs/quiz.py herausgeloest (Vorgabe Patrick: Module sollen
sauber getrennt und einzeln installierbar bleiben, weil einzelne Bereiche
spaeter kostenpflichtig angeboten werden sollen).

Der Grund fuer die Trennung ist inhaltlich, nicht kosmetisch: XP entsteht an
drei Stellen, von denen nur eine das Quiz ist -

    Chatnachrichten     cogs/moderation.py   (mit Cooldown und Tageslimit)
    Quizantworten       cogs/quiz.py
    Zufallsereignisse   cogs/personality.py

Solange /xp im Quiz-Cog lag, haette wer nur das Quiz-Modul installiert einen
Befehl bekommen, der ueberwiegend Chat-Fortschritt anzeigt - und wer nur XP &
Level installiert, haette gar keine Moeglichkeit gehabt, seine XP zu sehen.

Im Backend war diese Trennung laengst vollzogen (/xp-level als eigener
Bereich); nachgezogen sind hier die Bot-Befehle.
"""
import discord
from discord.ext import commands

import database
import modul_xp_level


class XpLevel(commands.Cog):
    # Fuer die Gruppierung in /help. Das Label steht am Cog und nicht in
    # einer zentralen Liste, damit ein entferntes Modul auch aus der Hilfe
    # verschwindet, ohne dass man daran denken muss.
    MODUL_LABEL = "📈 XP & Level"
    MODUL_REIHENFOLGE = 2
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="xp",
        description="Zeigt deinen XP-Stand und dein Level",
        extras={"hilfe": "Zeigt, wie viele XP du gesammelt hast, auf welchem Level du bist und wie weit es noch bis zum nächsten ist.\nXP bekommst du fürs Schreiben im Chat, für richtige Quizantworten und gelegentlich durch Zufallsereignisse. Pro Tag gibt es eine Obergrenze, damit niemand sie durch reines Vielschreiben hochtreibt."},
    )
    async def xp(self, ctx: commands.Context):
        progress = modul_xp_level.get_user_xp(ctx.author.id)
        await ctx.send(
            f"{ctx.author.mention}, du bist **Level {progress['level']}** mit **{progress['xp']} XP**. "
            f"Noch **{int(progress['xp_to_next'])} XP** bis Level {progress['level'] + 1}! 📈"
        )

    @commands.hybrid_command(
        name="serverxp_rangliste",
        description="Die 15 Besten nach Server-XP",
        extras={"hilfe": "Zeigt die 15 Personen mit den meisten **Server-XP** — also aus Chat, Quiz und Zufallsereignissen zusammen.\nDie reine Quiz-Wertung findest du unter `/quiz_rangliste`."},
    )
    async def serverxp_rangliste(self, ctx: commands.Context):
        top = modul_xp_level.get_xp_leaderboard(15)
        if not top:
            await ctx.send("Es hat noch niemand XP gesammelt.")
            return

        embed = discord.Embed(
            title="📈 Server-XP: Top 15",
            description=(
                "XP sammelt man durch Chatnachrichten, richtige Quizantworten und "
                "Zufallsereignisse. Die Quiz-Punkte stehen getrennt davon in "
                "`/quiz_rangliste`."
            ),
            color=discord.Color.blurple(),
        )
        for platz, eintrag in enumerate(top, start=1):
            user_id = int(eintrag["discord_user_id"])
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            embed.add_field(
                name=f"{platz}. {user.name}",
                value=f"Level {eintrag['level']} · {eintrag['xp']} XP",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="achievements", description="Deine Auszeichnungen und was noch fehlt",
        extras={"hilfe": "Zeigt alle Auszeichnungen mit ihrem Stand. Freigeschaltete siehst du mit Datum, offene mit der Bedingung.\nEinige sind **geheim** — die verrate ich erst, wenn du sie hast. Jede bringt zusätzliche XP.\nDie Antwort sieht nur du."},)
    async def achievements(self, ctx: commands.Context):
        overview = database.get_achievements_overview(ctx.author.id)
        embed = discord.Embed(title="🏅 Deine Achievements", color=discord.Color.gold())
        for entry in overview:
            if entry["unlocked"]:
                status = f"✅ Freigeschaltet am {entry['unlocked_at']} (+{entry['xp_reward']} XP)"
            elif entry["secret"]:
                status = "🔒 Geheimes Achievement - noch nicht freigeschaltet"
            else:
                status = f"🔒 Noch nicht freigeschaltet ({entry['description']})"
            embed.add_field(name=entry["name"], value=status, inline=False)
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(XpLevel(bot))
