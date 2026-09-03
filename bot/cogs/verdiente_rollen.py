"""Verdiente Rollen: Bedingung erfüllt → der Bot bietet die Rolle an.

Beispiel aus der Absprache: Wer 100 Quizfragen richtig beantwortet hat, kann
Ideenschmied werden. Der Bot meldet sich, und man holt sich die Rolle per Knopf
ab.

Warum ein Knopf und keine Direktnachricht an den Bot: Unsere Slash-Befehle sind
serverspezifisch registriert und existieren in DMs schlicht nicht. Ein Knopf
umgeht das ganz - ein Klick, nichts zu merken, keine Schreibweise zu treffen.

Die Bedingungslogik steht in bot/verdiente_rollen.py, ohne discord.py.
"""
import discord
from discord.ext import commands

import database
import verdiente_rollen as vr


class VerdienteRollen(commands.Cog):
    MODUL_LABEL = None  # keine eigenen Befehle, taucht in /help nicht auf

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Prüfen und ankündigen
    # ------------------------------------------------------------------

    async def pruefen(self, member: discord.Member) -> None:
        """Prüft alle Regeln für eine Person. Wird nach den Ereignissen
        aufgerufen, die die zugrundeliegenden Zahlen verändern - nicht in einer
        Schleife über alle Mitglieder, das wäre bei jedem Herzschlag unnötige
        Arbeit für nichts."""
        regeln = database.get_earned_roles(nur_aktive=True)
        if not regeln:
            return

        werte = database.werte_fuer_bedingungen(member.id)
        if member.joined_at:
            werte["tage_dabei"] = (discord.utils.utcnow() - member.joined_at).days

        for regel in regeln:
            try:
                await self._regel_pruefen(member, regel, werte)
            except Exception as exc:
                print(f"[verdiente rollen] Regel {regel['id']} bei {member}: {exc}")

    async def _regel_pruefen(self, member, regel, werte):
        if not vr.erfuellt(regel["bedingung_art"], regel["schwelle"], werte):
            return

        status = database.earned_role_status(regel["id"], member.id)
        if status["vergeben_at"] or status["angekuendigt_at"]:
            return  # schon erledigt - nicht erneut behelligen

        rolle = member.guild.get_role(int(regel["role_id"]))
        if not rolle:
            print(f"[verdiente rollen] Regel {regel['id']}: Rolle {regel['role_id']} gibt es nicht mehr.")
            return
        if rolle.position >= member.guild.me.top_role.position:
            print(f"[verdiente rollen] Regel {regel['id']}: {rolle.name} steht über der Bot-Rolle.")
            return
        if rolle in member.roles:
            # Hat die Rolle schon auf anderem Weg - vermerken und schweigen.
            database.mark_earned_role_granted(regel["id"], member.id)
            return

        wert, schwelle = vr.fortschritt(regel["bedingung_art"], regel["schwelle"], werte)
        text = vr.ankuendigung(
            regel["ankuendigung"], mention=member.mention, rolle=rolle.name,
            wert=wert, schwelle=schwelle,
        )

        if regel["automatisch"]:
            await member.add_roles(rolle, reason="Verdiente Rolle")
            database.mark_earned_role_granted(regel["id"], member.id)
            await self._senden(member, regel, text, knopf=None)
            return

        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(
            label=f"{rolle.name} abholen",
            style=discord.ButtonStyle.success,
            custom_id=vr.knopf_kennung(regel["id"], member.id),
        ))
        await self._senden(member, regel, text, knopf=view)
        database.mark_earned_role_announced(regel["id"], member.id)

    async def _senden(self, member, regel, text, knopf):
        """Ankündigung in den eingestellten Kanal, sonst per Direktnachricht.

        Scheitert das Senden, wird NICHT vermerkt - beim nächsten Auslöser wird
        es erneut versucht. Eine Ankündigung, die niemand gesehen hat, als
        erledigt zu buchen, wäre der schlechteste Ausgang.
        """
        ziel = None
        if regel["channel_id"]:
            ziel = self.bot.get_channel(int(regel["channel_id"]))
        if ziel is None:
            ziel = member
        await ziel.send(text, view=knopf) if knopf else await ziel.send(text)

    # ------------------------------------------------------------------
    # Der Abhol-Knopf
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        kennung = (interaction.data or {}).get("custom_id", "")
        gelesen = vr.knopf_kennung_lesen(kennung)
        if not gelesen:
            return
        regel_id, gemeinter_user = gelesen

        # Der Knopf steht öffentlich im Kanal - ohne diese Prüfung könnte sich
        # jeder, der ihn sieht, die Rolle holen.
        if str(interaction.user.id) != gemeinter_user:
            await interaction.response.send_message(
                "Dieser Knopf ist für jemand anderen. Sobald du die Bedingung erfüllst, "
                "melde ich mich bei dir.", ephemeral=True)
            return

        regel = database.get_earned_role(regel_id)
        if not regel or not regel["enabled"]:
            await interaction.response.send_message(
                "Diese Belohnung gibt es nicht mehr.", ephemeral=True)
            return

        member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
        if member is None:
            await interaction.response.send_message(
                "Das funktioniert nur auf dem Server.", ephemeral=True)
            return

        # Beim Klick nochmal prüfen: Zwischen Ankündigung und Klick können
        # Stunden liegen, und die Regel könnte inzwischen geändert worden sein.
        werte = database.werte_fuer_bedingungen(member.id)
        if member.joined_at:
            werte["tage_dabei"] = (discord.utils.utcnow() - member.joined_at).days
        if not vr.erfuellt(regel["bedingung_art"], regel["schwelle"], werte):
            await interaction.response.send_message(
                "Die Bedingung ist gerade nicht erfüllt.", ephemeral=True)
            return

        rolle = member.guild.get_role(int(regel["role_id"]))
        if not rolle:
            await interaction.response.send_message(
                "Die Rolle gibt es nicht mehr — sag bitte einem Admin Bescheid.", ephemeral=True)
            return

        try:
            await member.add_roles(rolle, reason="Verdiente Rolle abgeholt")
        except discord.HTTPException:
            await interaction.response.send_message(
                "Das hat leider nicht geklappt — sag bitte einem Admin Bescheid.", ephemeral=True)
            return

        database.mark_earned_role_granted(regel_id, member.id)
        await interaction.response.send_message(
            f"Du hast jetzt die Rolle **{rolle.name}**. Viel Freude damit! 🎉", ephemeral=True)


async def verdiente_rollen_pruefen(bot, member):
    """Stösst die Prüfung der verdienten Rollen an, ohne dass der Aufrufer den
    Cog kennen muss.

    Bewusst hier und nicht direkt im Aufrufer: Ist das Modul nicht installiert,
    passiert einfach nichts - genau das Verhalten, das die Modultrennung
    verlangt. Und ein Fehler darin darf nie den eigentlichen Ablauf
    (Quizantwort, XP-Vergabe) mitreissen.
    """
    cog = bot.get_cog("VerdienteRollen")
    if cog is None or member is None or getattr(member, "bot", False):
        return
    try:
        await cog.pruefen(member)
    except Exception as exc:
        print(f"[verdiente rollen] Pruefung fuer {member} fehlgeschlagen: {exc}")


async def setup(bot: commands.Bot):
    await bot.add_cog(VerdienteRollen(bot))
