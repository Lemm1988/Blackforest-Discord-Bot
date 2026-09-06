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
import rollenabgleich


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

        # ZUERST die Richtung Recht -> Rolle. Die Reihenfolge ist der Punkt:
        # Wer ein Recht hat, dessen Rolle ihm fehlt, soll die Rolle bekommen -
        # nicht das Recht verlieren. Lief nur die andere Richtung, loeste der
        # Bot jede Unstimmigkeit zerstoerend auf. Am 05.09.2026 kostete das
        # jemanden seine Berechtigung: Sie wurde ausserhalb des Backends
        # eingetragen, die zugehoerige Rolle fehlte, und der Startabgleich
        # entfernte daraufhin das Recht.
        await self.rollen_nachziehen()

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

    async def rollen_nachziehen(self) -> int:
        """Vergibt fehlende Rollen fuer bereits vorhandene Rechte.

        Bewusst nur vergeben, nicht entziehen. Das Entziehen laeuft im Backend
        bei jeder Rechteaenderung und ueber den Knopf "Bestand angleichen" -
        es hier zusaetzlich zu tun hiesse, beim Start eine Rolle wegzunehmen,
        die vielleicht gerade erst von Hand vergeben wurde. Fehlende Rollen
        nachzutragen ist dagegen in keinem Fall schaedlich.
        """
        zuordnung = database.get_permission_role_map()
        if not zuordnung:
            return 0

        nachgetragen = 0
        for guild in self.bot.guilds:
            eigene = guild.me.top_role.position
            verwaltbar = {
                str(r.id) for r in guild.roles
                if r.position < eigene and not r.managed
            }
            for member in guild.members:
                if member.bot or database.is_protected_owner(str(member.id)):
                    continue
                massnahme = rollenabgleich.massnahmen(
                    rechte=set(database.get_user_permissions(str(member.id))),
                    rollen={str(r.id) for r in member.roles if r.name != "@everyone"},
                    zuordnung=zuordnung,
                    verwaltbare_rollen=verwaltbar,
                    vom_bot_vergeben=database.bot_vergebene_rollen(str(member.id)),
                )
                for role_id in massnahme.vergeben:
                    rolle = guild.get_role(int(role_id))
                    if rolle is None:
                        continue
                    # Der Merker MUSS vor dem Vergeben stehen. Discord meldet
                    # die Rollenaenderung sofort zurueck, und on_member_update
                    # unten laeuft dann los, bevor die Zeile geschrieben waere -
                    # der Listener haelt die Rolle fuer von Hand vergeben und
                    # leitet die ganze Grundausstattung daraus ab. Beobachtet am
                    # 05.09.2026: erst "+manage_categories, +moderate_members,
                    # +verify_questions", eine Sekunde spaeter alles wieder weg.
                    database.merke_rollenvergabe(str(member.id), role_id)
                    try:
                        await member.add_roles(rolle, reason="Backend-Berechtigung (Start)")
                    except discord.HTTPException as exc:
                        database.vergiss_rollenvergabe(str(member.id), role_id)
                        print(f"[rechtesync] {member}: Rolle {rolle.name} nicht vergebbar: {exc}")
                        continue
                    database.log_audit(None, "rollen_angeglichen", target_id=str(member.id),
                                       detail=f"+{rolle.name} (Start)")
                    print(f"[rechtesync] {member}: +{rolle.name} (Recht vorhanden, Rolle fehlte)")
                    nachgetragen += 1
        if nachgetragen:
            print(f"[rechtesync] {nachgetragen} fehlende Rolle(n) nachgetragen.")
        return nachgetragen

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
        # Welche Rollen der Bot ueberhaupt vergeben kann. Rechte, die an einer
        # hoeher stehenden Rolle haengen (Manager), duerfen nicht entzogen
        # werden, nur weil ihr Traeger diese Rolle nicht hat - der Bot koennte
        # sie ihm gar nicht geben.
        eigene_position = member.guild.me.top_role.position
        verwaltbare = {
            str(r.id) for r in member.guild.roles
            if r.position < eigene_position and not r.managed
        }
        massnahme = rechteabgleich.massnahmen(
            rollen={str(r.id) for r in member.roles if r.name != "@everyone"},
            rollenrechte=rollenrechte,
            eigene_rechte=eigene_rechte,
            geschuetzt=database.is_protected_owner(uid),
            spiegelrollen=spiegelrollen,
            zuordnung=database.get_permission_role_map(),
            handverliehen=eigene_rechte - database.bot_gesetzte_rechte(uid),
            ausnahmen=database.ausnahmen(uid),
            verwaltbare_rollen=verwaltbare,
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
