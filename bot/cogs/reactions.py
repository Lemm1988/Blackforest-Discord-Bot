"""Reaktionsrollen: Wer auf eine Nachricht mit einem Emoji reagiert, bekommt eine Rolle.

Bis zum 03.09.2026 gab es genau EINE solche Regel, und sie steckte fest im Code:
Nachricht aus `rules_message_id`, Emoji 👍, Rolle aus `member_role_name`, Kanal
über `rules_channel_name`. Vier einzelne Einstellungen - also nicht vermehrbar.

Jetzt eine Liste beliebig vieler Regeln in der Tabelle reaction_roles, im
Backend unter Bot-Setup pflegbar. Die alte Regel zieht beim ersten Start von
selbst um und verhält sich danach unverändert.

Ein Detail, das dabei besser geworden ist: Die alte Fassung suchte den Kanal
über seinen NAMEN (`channel.name != rules_channel_name`) und die Rolle über
`discord.utils.get(guild.roles, name=...)`. Wer den Regelkanal umbenennt oder die
Rolle "Member" in "Mitglied" ändert, hätte die Rollenvergabe stillschweigend
abgeschaltet - ohne Fehler, ohne Hinweis. Die Regeln arbeiten deshalb mit IDs.
"""
import discord
from discord.ext import commands

import database


# Die frühere, fest verdrahtete Regel. Stand bis zum 03.09.2026 als vier
# Einstellungen in config.DEFAULT_SETTINGS; die sind entfernt, weil sie nach dem
# Umzug nichts mehr bewirkt hätten - wer sie geändert hätte, hätte geglaubt
# etwas zu tun, und es wäre nichts passiert.
#
# Die Werte bleiben hier als Ausgangspunkt für den einmaligen Umzug stehen,
# damit eine frisch aufgesetzte Datenbank die Regel weiterhin bekommt. Sobald
# der Umzug überall gelaufen ist, kann auch das hier weg.
ALTE_REGEL = {
    "message_id": "1244268026069651466",
    "kanal": "§-regeln-§",
    "rolle": "Member",
}


class Reactions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Einmaliger Umzug der alten, fest verdrahteten Regel
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await self._alte_regel_uebernehmen()
        except Exception as exc:
            print(f"[reaktionsrollen] Umzug der alten Regel fehlgeschlagen: {exc}")

    async def _alte_regel_uebernehmen(self):
        """Überführt die frühere Regel-Reaktion in die neue Tabelle.

        Läuft in on_ready und nicht in init_db(), weil Kanal- und Rollennamen
        erst zu IDs aufgelöst werden können, wenn der Bot mit Discord verbunden
        ist. Der Merker in bot_state sorgt dafür, dass das genau einmal passiert -
        sonst käme die Regel nach jedem Löschen im Backend wieder zurück.
        """
        if database.get_bot_state("reaction_roles_migriert") == "1":
            return
        if not self.bot.guilds:
            return
        guild = self.bot.guilds[0]

        message_id = database.get_setting("rules_message_id", ALTE_REGEL["message_id"])
        kanalname = database.get_setting("rules_channel_name", ALTE_REGEL["kanal"])
        rollenname = database.get_setting("member_role_name", ALTE_REGEL["rolle"])
        aktiv = database.get_setting("role_reaction_enabled", "1") == "1"

        kanal = discord.utils.get(guild.text_channels, name=kanalname)
        rolle = discord.utils.get(guild.roles, name=rollenname)

        # Nichts erfinden: Findet sich der Kanal oder die Rolle nicht, wird der
        # Umzug NICHT als erledigt vermerkt. Beim naechsten Start wird es erneut
        # versucht - vielleicht wurde nur etwas umbenannt.
        if not kanal or not rolle:
            print(f"[reaktionsrollen] Alte Regel nicht uebernommen: "
                  f"Kanal {kanalname!r} {'gefunden' if kanal else 'NICHT gefunden'}, "
                  f"Rolle {rollenname!r} {'gefunden' if rolle else 'NICHT gefunden'}.")
            return

        if not database.find_reaction_role(message_id, "👍"):
            neu_id = database.add_reaction_role(
                channel_id=kanal.id, message_id=message_id, emoji="👍", role_id=rolle.id,
                entzug_erlaubt=True,
                dm_text=("{mention}, du hast die Regeln akzeptiert und die Rolle "
                         "**{rolle}** erhalten!"),
            )
            if not aktiv:
                database.update_reaction_role(neu_id, enabled=False)
            print(f"[reaktionsrollen] Alte Regel-Reaktion uebernommen: "
                  f"👍 auf {message_id} -> {rolle.name} (aktiv={aktiv}).")

        database.set_bot_state("reaction_roles_migriert", "1")

    # ------------------------------------------------------------------
    # Der eigentliche Ablauf
    # ------------------------------------------------------------------

    async def _handle(self, payload: discord.RawReactionActionEvent, adding: bool):
        regel = database.find_reaction_role(payload.message_id, str(payload.emoji))
        if not regel:
            return

        # Reaktion entfernt, aber die Regel gibt die Rolle nicht wieder her.
        if not adding and not regel["entzug_erlaubt"]:
            return

        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if not guild:
            return

        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        rolle = guild.get_role(int(regel["role_id"]))
        if not rolle:
            print(f"[reaktionsrollen] Regel {regel['id']}: Rolle {regel['role_id']} "
                  f"gibt es nicht mehr.")
            return

        # Vorher pruefen statt an einer nichtssagenden Discord-Ablehnung zu
        # scheitern: Ein Bot kann nur Rollen unterhalb seiner eigenen vergeben.
        if rolle.position >= guild.me.top_role.position:
            print(f"[reaktionsrollen] Regel {regel['id']}: Rolle {rolle.name} steht über "
                  f"der Bot-Rolle und kann nicht vergeben werden.")
            return

        try:
            if adding:
                await member.add_roles(rolle, reason="Reaktionsrolle")
            else:
                await member.remove_roles(rolle, reason="Reaktionsrolle entfernt")
        except discord.Forbidden:
            print(f"[reaktionsrollen] Regel {regel['id']}: Discord hat die Rollenänderung "
                  f"für {member} abgelehnt.")
            return
        except discord.HTTPException as exc:
            print(f"[reaktionsrollen] Regel {regel['id']}: {exc}")
            return

        await self._benachrichtigen(member, rolle, regel, adding)

    async def _benachrichtigen(self, member, rolle, regel, adding: bool):
        """Direktnachricht, falls für die Regel ein Text hinterlegt ist.

        Scheitert bewusst still: Wer seine DMs zu hat, soll die Rolle trotzdem
        bekommen - die Nachricht ist Beiwerk, die Rolle ist der Zweck.
        """
        text = (regel.get("dm_text") or "").strip()
        if not text:
            return
        if adding:
            nachricht = text.replace("{mention}", member.mention).replace("{rolle}", rolle.name)
        else:
            nachricht = f"{member.mention}, die Rolle **{rolle.name}** wurde wieder entfernt."
        try:
            await member.send(nachricht)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle(payload, adding=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle(payload, adding=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reactions(bot))
