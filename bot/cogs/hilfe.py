"""Der /help-Befehl.

Kernbestandteil, kein verkaufbares Modul: Ohne Hilfe findet niemand die anderen
Module. Er sammelt seine Inhalte deshalb ein, statt sie zu kennen — jeder Cog
bringt sein eigenes Label (MODUL_LABEL) mit, jeder Befehl seine eigenen Texte
(extras). Fällt ein Modul weg, verschwindet sein Block von selbst aus der Hilfe,
ohne dass hier etwas angepasst werden muss.

Ersetzt zwei Vorgänger, die beide **jedem alles** zeigten: den >bothelp-Befehl
und die eingebaute Hilfe von discord.py. `grant_access` und `revoke_access`
standen damit in der Nachricht, die jedes Mitglied bekam.

Die Textbausteine liegen in bot/hilfe.py, ohne discord.py — damit sie ohne
laufenden Bot prüfbar sind.
"""
import discord
from discord.ext import commands

import config
import database
import hilfe


class Hilfe(commands.Cog):
    MODUL_LABEL = "❓ Hilfe"
    # Bewusst ganz hinten: Wer das hier liest, hat /help schon gefunden.
    MODUL_REIHENFOLGE = 10

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Daten einsammeln
    # ------------------------------------------------------------------

    def _befehl_als_eintrag(self, befehl: commands.Command) -> dict:
        extras = befehl.extras or {}
        return {
            "name": befehl.qualified_name,
            "kurz": befehl.description or "Keine Beschreibung hinterlegt.",
            "lang": extras.get("hilfe"),
            "recht": extras.get("recht"),
            "verwendung": f"/{befehl.qualified_name} {befehl.signature}".strip(),
        }

    def _module(self) -> list[dict]:
        """Alle Cogs mit Label und ihren Befehlen.

        Cogs ohne MODUL_LABEL tauchen nicht auf - das betrifft die
        Hintergrunddienste (Moderation, Reaktionen, Giveaway, Personality), die
        gar keine Befehle haben und in einer Befehlsuebersicht nichts verloren
        haetten.
        """
        module = []
        for cog in self.bot.cogs.values():
            label = getattr(cog, "MODUL_LABEL", None)
            if not label:
                continue
            befehle = [self._befehl_als_eintrag(b) for b in cog.get_commands() if not b.hidden]
            if not befehle:
                continue
            module.append({
                "label": label,
                "reihenfolge": getattr(cog, "MODUL_REIHENFOLGE", 99),
                "befehle": sorted(befehle, key=lambda b: b["name"]),
            })
        return module

    def _pruefer(self, user_id):
        return lambda recht: database.has_permission(user_id, recht)

    # ------------------------------------------------------------------
    # Der Befehl
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="help",
        description="Zeigt, was ich kann — und was du davon benutzen darfst",
        extras={"hilfe": "Ohne Angabe zeige ich dir alle Befehle, die du benutzen "
                         "kannst, nach Bereichen sortiert.\nMit einem Befehlsnamen "
                         "erkläre ich genau diesen einen — zum Beispiel `/help frage`.\n"
                         "Die Antwort sieht nur du."},
    )
    @discord.app_commands.describe(befehl="Optional: der Befehl, den du genauer erklärt haben möchtest")
    async def help(self, ctx: commands.Context, befehl: str = ""):
        pruefer = self._pruefer(ctx.author.id)
        module = self._module()

        # Nur was der Aufrufer sehen darf - auch fuer die Suche unten, damit die
        # Fehlermeldung keine Adminbefehle verraet.
        erlaubt = {
            eintrag["name"]: eintrag
            for modul in module
            for eintrag in hilfe.sichtbare_befehle(modul["befehle"], pruefer)
        }

        if befehl:
            gesucht = befehl.strip().lstrip("/").lower()
            eintrag = erlaubt.get(gesucht)
            if not eintrag:
                await ctx.send(hilfe.nicht_gefunden(gesucht, list(erlaubt)), ephemeral=True)
                return
            embed = discord.Embed(
                description=hilfe.einzelhilfe(eintrag),
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title="Was ich kann",
            description=hilfe.uebersicht(module, pruefer),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed, ephemeral=True)


    # ------------------------------------------------------------------
    # Uebergangshilfe fuer alte Gewohnheiten
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Weist freundlich darauf hin, wenn jemand noch die alte Schreibweise
        mit ">" benutzt.

        Seit dem 03.09.2026 loesen Textbefehle nichts mehr aus. Ohne diesen
        Hinweis waere ">xp" einfach eine Nachricht, auf die nichts passiert -
        und der Bot wirkte kaputt. Das ist dieselbe Fehlerklasse, die uns bei
        Kick/Ban und bei den fehlenden Befehlsfehlern schon beschaeftigt hat:
        Es passiert nichts, und nichts sagt warum.

        BEWUSST eng gefasst - der Hinweis kommt nur, wenn direkt hinter dem ">"
        ein Befehl steht, den es wirklich gibt. Sonst wuerde der Bot auf jedes
        Zitat und jeden Pfeil im Chat antworten.

        Der Hinweis loescht sich nach 30 Sekunden selbst, damit der Kanal nicht
        mit Uebergangsmeldungen volllaeuft.

        Diese Methode darf wieder verschwinden, sobald sich niemand mehr
        vertippt.
        """
        if message.author.bot:
            return

        text = hilfe.altes_praefix_hinweis(
            message.content,
            config.ALTES_PRAEFIX,
            {b.qualified_name for b in self.bot.commands},
        )
        if text is None:
            return
        try:
            await message.channel.send(f"{message.author.mention} {text}", delete_after=30)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Hilfe(bot))
