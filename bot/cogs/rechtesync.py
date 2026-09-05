"""Hält Discord-Rollen und Backend-Rechte in beide Richtungen zusammen.

Vorgabe Patrick, 05.09.2026:

> „dies muss automatisch auf dem server angepasst werden und auch im backend
> wenn ein user berechtigungen erhält in beide richtungen setzen."

Ausgangslage waren zwei sichtbare Widersprüche auf dem Server: Zwei Mitglieder
trugen die Moderator-Rolle, hatten im Backend aber keinerlei Rechte, während
umgekehrt jemand sämtliche Moderationsrechte besaß, ohne die Rolle zu tragen.
Beides stand in der Benutzerverwaltung als Hinweis — es wurde nur nie behoben,
weil die Übertragung ausschließlich auf Knopfdruck lief und die Gegenrichtung
gar nicht existierte.

Dieses Cog schließt die Lücke `Rolle -> Recht`. Die Gegenrichtung
`Recht -> Rolle` gibt es im Backend bereits (rollenabgleich.py); sie wird von
dort jetzt bei jeder Rechteänderung selbst angestoßen statt erst beim Klick.

Die eigentliche Entscheidung, WAS zu tun ist, steht in rechteabgleich.py und ist
ohne laufenden Bot prüfbar. Hier steht nur das Ausführen.
"""
from __future__ import annotations

import discord
from discord.ext import commands

import database
import rechteabgleich


class Rechtesync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._startabgleich_erledigt = False

    @commands.Cog.listener()
    async def on_ready(self):
        """Einmal nach dem Start alles nachziehen.

        Der Listener unten deckt nur ab, was geschieht, WÄHREND der Bot läuft.
        Wer eine Rolle bekommt, während der Pi neu startet, bliebe sonst
        dauerhaft ohne die zugehörigen Rechte — und genau solche Altfälle waren
        der Anlass für dieses Cog.

        on_ready feuert auch nach jedem Verbindungsabbruch erneut, deshalb der
        Merker: Ein voller Durchlauf über alle Mitglieder bei jedem Wackler wäre
        auf einem Pi unnötige Last.
        """
        if self._startabgleich_erledigt:
            return
        self._startabgleich_erledigt = True

        if not database.get_role_permission_map():
            return  # Keine Zuordnung eingerichtet - dann gibt es nichts abzuleiten.

        geaendert = 0
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                if await self.abgleichen(member, grund="Abgleich nach Neustart"):
                    geaendert += 1
        if geaendert:
            print(f"[rechtesync] Startabgleich: bei {geaendert} Mitgliedern nachgezogen.")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Reagiert auf Rollenänderungen in Discord.

        on_member_update feuert auch für Spitznamen, Server-Avatare und den
        Zeitpunkt eines Timeouts. Ohne den Vergleich unten liefe der komplette
        Abgleich bei jeder dieser Kleinigkeiten - auf einem Pi spürbar, und
        völlig ohne Anlass.
        """
        vorher = {r.id for r in before.roles}
        nachher = {r.id for r in after.roles}
        if vorher == nachher:
            return
        await self.abgleichen(after, grund="Rollenänderung in Discord")

    async def abgleichen(self, member: discord.Member, *, grund: str) -> list[str]:
        """Setzt die Backend-Rechte dieser Person passend zu ihren Rollen.

        Liefert eine Liste der Änderungen für das Log. Eine leere Liste heißt:
        Es passte bereits.
        """
        uid = str(member.id)
        rollenrechte = database.get_role_permission_map()
        eigene_rechte = set(database.get_user_permissions(uid))

        # Rollen, die der Bot selbst wegen einer Berechtigung vergeben hat.
        # Sie sind eine Anzeige des Rechts und duerfen nicht zurueckwirken -
        # sonst wird aus einem gezielt vergebenen Recht das ganze Buendel der
        # Rolle. `handverliehen` schuetzt dabei das ausloesende Recht davor,
        # gleich wieder entzogen zu werden.
        spiegelrollen = database.bot_vergebene_rollen(uid)
        massnahme = rechteabgleich.massnahmen(
            rollen={str(r.id) for r in member.roles if r.name != "@everyone"},
            rollenrechte=rollenrechte,
            eigene_rechte=eigene_rechte,
            geschuetzt=database.is_protected_owner(uid),
            spiegelrollen=spiegelrollen,
            zuordnung=database.get_permission_role_map(),
            handverliehen=eigene_rechte - database.bot_gesetzte_rechte(uid),
            ausnahmen=database.ausnahmen(uid),
        )
        if massnahme.leer:
            return []

        # Welche Rolle das Recht mitbringt, wird mitgeschrieben - sonst liesse
        # sich spaeter nicht beantworten, warum jemand es bekommen hat.
        # Spiegelrollen bleiben aussen vor, damit kein Recht auf eine Rolle
        # zurueckgefuehrt wird, die es selbst ausgeloest hat.
        herkunft = {}
        for rolle in member.roles:
            if str(rolle.id) in spiegelrollen:
                continue
            for schluessel in rollenrechte.get(str(rolle.id), ()):
                herkunft.setdefault(schluessel, str(rolle.id))

        geschehen = []
        for schluessel in massnahme.vergeben:
            database.grant_permission(uid, schluessel,
                                      database.rollen_quelle(herkunft.get(schluessel, "?")))
            geschehen.append(f"+{schluessel}")
        for schluessel in massnahme.entziehen:
            # bewusst=False: Der Abgleich raeumt nur auf, was keine Grundlage
            # mehr hat. Daraus eine dauerhafte Ausnahme zu machen waere falsch -
            # bekommt die Person die Rolle zurueck, soll sie wieder die volle
            # Grundausstattung haben.
            database.revoke_user_permission(uid, schluessel, actor_id=None, bewusst=False)
            geschehen.append(f"-{schluessel}")
        for schluessel in massnahme.ausnahmen_aufheben:
            database.revoke_user_permission(uid, schluessel, actor_id=None, bewusst=False)
            geschehen.append(f"~{schluessel}")

        # Wer Rechte verliert, darf nicht mit einer offenen Backend-Sitzung
        # weiterarbeiten - sonst wirkt der Entzug erst beim naechsten Login.
        if massnahme.entziehen:
            database.invalidate_user_session(uid)

        database.log_audit(
            None, "rechtesync", target_id=uid,
            detail=f"{grund}: {rechteabgleich.begruendung(massnahme)}",
        )
        print(f"[rechtesync] {member} ({grund}): {', '.join(geschehen)}")
        return geschehen


async def setup(bot: commands.Bot):
    await bot.add_cog(Rechtesync(bot))
