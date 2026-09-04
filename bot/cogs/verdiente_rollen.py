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
        """Prüft alle Regeln für eine Person und meldet sich HÖCHSTENS EINMAL.

        Das war anfangs anders und sofort ein Ärgernis: Wer schon lange dabei
        ist, erfüllt beim ersten Auslöser alle Treuestufen auf einmal. Am
        04.09.2026 bekam Patrick nach einer einzigen Quizantwort fünf
        Ankündigungen innerhalb von zwei Sekunden in die Laberecke — und dasselbe
        hätte jedes bestehende Mitglied getroffen, sobald es etwas tut.

        Die Rollen sind alle verdient und werden auch alle vergeben. Aber
        angekündigt wird zusammengefasst: eine Nachricht für alles, was gerade
        automatisch dazukam, und eine für alles, was abgeholt werden kann.

        Aufgerufen nach den Ereignissen, die die zugrundeliegenden Zahlen
        verändern — nicht in einer Schleife über alle Mitglieder.
        """
        regeln = database.get_earned_roles(nur_aktive=True)
        if not regeln:
            return

        werte = vr.REGISTRATUR.werte(member.id)
        # "tage_dabei" kennt nur Discord - die Registratur kann es nicht selbst
        # holen (siehe Bedingungsart.wert=None in modul_kern.py).
        if member.joined_at:
            werte["tage_dabei"] = (discord.utils.utcnow() - member.joined_at).days

        faellig = []
        for regel in regeln:
            try:
                if await self._ist_faellig(member, regel, werte):
                    faellig.append(regel)
            except Exception as exc:
                print(f"[verdiente rollen] Regel {regel['id']} bei {member}: {exc}")
        if not faellig:
            return

        automatisch = [r for r in faellig if r["automatisch"]]
        abzuholen = [r for r in faellig if not r["automatisch"]]

        if automatisch:
            await self._automatisch_vergeben(member, automatisch, werte)
        if abzuholen:
            await self._zum_abholen_anbieten(member, abzuholen, werte)

    async def _ist_faellig(self, member, regel, werte) -> bool:
        """Trifft die Regel gerade zu, und ist sie noch offen?"""
        if not vr.erfuellt(regel["bedingung_art"], regel["schwelle"], werte):
            return False

        status = database.earned_role_status(regel["id"], member.id)
        if status["vergeben_at"] or status["angekuendigt_at"]:
            return False  # schon erledigt - nicht erneut behelligen

        rolle = member.guild.get_role(int(regel["role_id"]))
        if not rolle:
            print(f"[verdiente rollen] Regel {regel['id']}: Rolle {regel['role_id']} gibt es nicht mehr.")
            return False
        if rolle.position >= member.guild.me.top_role.position:
            print(f"[verdiente rollen] Regel {regel['id']}: {rolle.name} steht über der Bot-Rolle.")
            return False
        if rolle in member.roles:
            # Hat die Rolle schon auf anderem Weg - vermerken und schweigen.
            database.mark_earned_role_granted(regel["id"], member.id)
            return False
        return True

    async def _automatisch_vergeben(self, member, regeln, werte) -> None:
        """Rollen vergeben und EINMAL Bescheid sagen."""
        vergeben = []
        for regel in regeln:
            rolle = member.guild.get_role(int(regel["role_id"]))
            try:
                await member.add_roles(rolle, reason="Verdiente Rolle")
            except discord.HTTPException as exc:
                print(f"[verdiente rollen] {rolle.name} für {member}: {exc}")
                continue
            database.mark_earned_role_granted(regel["id"], member.id)
            vergeben.append((regel, rolle))
        if not vergeben:
            return

        await self._niedrigere_entfernen(member, [r for r, _ in vergeben])

        if len(vergeben) == 1:
            regel, rolle = vergeben[0]
            wert, schwelle = vr.fortschritt(regel["bedingung_art"], regel["schwelle"], werte)
            text = vr.ankuendigung(regel["ankuendigung"], mention=member.mention,
                                   rolle=rolle.name, wert=wert, schwelle=schwelle)
        else:
            # Mehrere auf einmal: Der Text der HÖCHSTEN Stufe, danach die
            # übrigen in einer Zeile. Fünf einzelne Glückwünsche wären kein
            # Fest, sondern eine Flut.
            regel, rolle = max(vergeben, key=lambda p: p[0]["schwelle"])
            wert, schwelle = vr.fortschritt(regel["bedingung_art"], regel["schwelle"], werte)
            text = vr.ankuendigung(regel["ankuendigung"], mention=member.mention,
                                   rolle=rolle.name, wert=wert, schwelle=schwelle)
            weitere = [r.name for _, r in vergeben if r.id != rolle.id]
            text += "\n\nAuf dem Weg dorthin gab es ausserdem: " + ", ".join(f"**{n}**" for n in weitere)

        await self._senden(member, regel, text, knopf=None)

    async def _zum_abholen_anbieten(self, member, regeln, werte) -> None:
        """Eine Nachricht mit einem Knopf je Rolle."""
        view = discord.ui.View(timeout=None)
        namen = []
        for regel in regeln[:25]:  # Discord erlaubt hoechstens 25 Knoepfe
            rolle = member.guild.get_role(int(regel["role_id"]))
            view.add_item(discord.ui.Button(
                label=f"{rolle.name} abholen",
                style=discord.ButtonStyle.success,
                custom_id=vr.knopf_kennung(regel["id"], member.id),
            ))
            namen.append(rolle.name)

        hoechste = max(regeln, key=lambda r: r["schwelle"])
        rolle = member.guild.get_role(int(hoechste["role_id"]))
        wert, schwelle = vr.fortschritt(hoechste["bedingung_art"], hoechste["schwelle"], werte)
        text = vr.ankuendigung(hoechste["ankuendigung"], mention=member.mention,
                               rolle=rolle.name, wert=wert, schwelle=schwelle)
        if len(namen) > 1:
            text += "\n\nZum Abholen bereit: " + ", ".join(f"**{n}**" for n in namen)

        await self._senden(member, hoechste, text, knopf=view)
        for regel in regeln:
            database.mark_earned_role_announced(regel["id"], member.id)

    async def _niedrigere_entfernen(self, member, erreichte) -> None:
        """Bei einer Rangleiter die vorherigen Stufen wieder abnehmen.

        Nur für Regeln, die als Rangleiter eingestellt sind, und nur innerhalb
        DERSELBEN Bedingungsart — wer ein Jahr dabei ist, verliert seine
        Quiz-Auszeichnungen nicht.

        Und nur, was der Bot selbst vergeben hat. Wer eine dieser Rollen von
        Hand bekommen hat, behält sie; dieselbe Regel wie beim
        Berechtigungs-Abgleich.

        Der Merker in earned_role_grants wird BEWUSST nicht gelöscht: Sonst
        träfe die Bedingung beim nächsten Auslöser wieder zu, die Rolle käme
        zurück, würde erneut entfernt — eine Schaukel ohne Ende.
        """
        alle = database.get_earned_roles()
        selbst_vergeben = database.vom_bot_verdient_vergeben(member.id)

        zu_entfernen = {}
        for regel in erreichte:
            if not regel.get("ersetzt_niedrigere"):
                continue
            for niedriger in vr.niedrigere_stufen(regel, alle):
                if niedriger["id"] not in selbst_vergeben:
                    continue
                rolle = member.guild.get_role(int(niedriger["role_id"]))
                if rolle and rolle in member.roles:
                    zu_entfernen[rolle.id] = rolle

        for rolle in zu_entfernen.values():
            try:
                await member.remove_roles(rolle, reason="Rangleiter: höhere Stufe erreicht")
            except discord.HTTPException as exc:
                print(f"[verdiente rollen] {rolle.name} für {member} nicht entfernbar: {exc}")

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
        werte = vr.REGISTRATUR.werte(member.id)
        # "tage_dabei" kennt nur Discord - die Registratur kann es nicht selbst
        # holen (siehe Bedingungsart.wert=None in bedingungen_kern.py).
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
        # Auch beim Abholen gilt die Rangleiter - sonst wirkte die Einstellung
        # nur bei automatisch vergebenen Rollen.
        await self._niedrigere_entfernen(member, [regel])
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
