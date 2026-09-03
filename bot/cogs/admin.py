"""Admin-Befehle zur Verwaltung des granularen Backend-Berechtigungssystems.

Früher gab es feste Rollen (admin/moderator/bot_user). Jetzt gibt es einzelne,
pro Nutzer frei kombinierbare Berechtigungs-Flags (siehe database.ALL_PERMISSIONS),
identisch zu dem, was auch die Backend-Seite "/users" verwaltet - beide
Oberflächen (Bot-Befehl und Web-UI) schreiben in dieselbe user_permissions-Tabelle.

grant_access/revoke_access bleiben bewusst NUR für is_admin nutzbar (nicht für
irgendein anderes Flag) - sonst könnte sich ein Nutzer mit z.B. nur "send_dm"
selbst weitere Rechte oder sogar is_admin verschaffen.
"""
import discord
from discord.ext import commands
from discord import app_commands

import config
import database


# --- Sichtbarkeit im Discord-/-Menue (03.09.2026) ---
#
# @app_commands.default_permissions(...) ist NUR ein Anzeigefilter: Discord
# zeichnet das Befehlsmenue, ohne den Bot zu fragen, und kann dabei
# ausschliesslich seine EIGENEN Serverrechte beruecksichtigen - von den
# Berechtigungen in unserer Datenbank weiss es nichts. Die echte Schranke
# bleiben deshalb is_admin() und has_permission_check() weiter unten.
#
# Die Zuordnung wurde am 03.09.2026 gegen den echten Server geprueft:
#   grant_access/revoke_access -> is_admin  -> Discord "Administrator".
#       Deckt sich exakt: Die einzige Person mit is_admin ist der
#       Server-Eigentuemer, der Administrator ohnehin hat.
#   dm -> send_dm -> Discord "Mitglieder timeouten" (moderate_members).
#       BEWUSST NICHT Administrator: send_dm ist eine Berechtigung, die an
#       Moderatoren vergeben wird, und die Rollen Moderator (Position 5) und
#       Manager (6) haben auf diesem Server kein Adminrecht. Hinter
#       "Administrator" versteckt waere der Befehl fuer genau die Leute
#       unsichtbar, fuer die er gedacht ist. moderate_members haben auf diesem
#       Server genau Moderator und Manager.
#
# Ueberschreibbar bleibt das ohnehin: Servereinstellungen -> Integrationen,
# pro Befehl und Rolle.


# Die Pruefungen ANTWORTEN NICHT SELBST, sondern loesen eine CheckFailure mit
# der Begruendung aus. Die zentrale Fehlerbehandlung in main.py zeigt sie dann
# an - und zwar nur fuer den Aufrufer sichtbar (Vorgabe Patrick, 03.09.2026).
#
# Vorher schickten sie die Meldung selbst per ctx.send(). Als reiner
# Chat-Befehl ging das gut, bei Slash-Befehlen waere es ein Fehler geworden:
# Discord erlaubt auf eine Anfrage GENAU EINE Antwort. Erst haette die Pruefung
# geantwortet, dann die Fehlerbehandlung - das ergibt "Interaction has already
# been responded to", und der Nutzer sieht im Zweifel "Die Anwendung reagiert
# nicht". Ausserdem stand die Absage vorher oeffentlich im Kanal.


def _rollen(ctx) -> set:
    """Discord-Rollen des Aufrufers, fuer Rechte die an einer Rolle haengen.

    In einer Direktnachricht gibt es keine Rollen - dann zaehlen nur die
    Backend-Flags.
    """
    member = getattr(ctx, "author", None)
    return {str(r.id) for r in getattr(member, "roles", [])}


def is_admin():
    async def predicate(ctx: commands.Context) -> bool:
        if not database.has_permission(ctx.author.id, "is_admin", rollen=_rollen(ctx)):
            raise commands.CheckFailure("⛔ Dieser Befehl ist nur für Admins.")
        return True
    return commands.check(predicate)


def has_permission_check(key: str):
    async def predicate(ctx: commands.Context) -> bool:
        if not database.has_permission(ctx.author.id, key, rollen=_rollen(ctx)):
            raise commands.CheckFailure(
                f"⛔ Dir fehlt die Berechtigung **{key}** für diesen Befehl."
            )
        return True
    return commands.check(predicate)


class Admin(commands.Cog):
    # Fuer die Gruppierung in /help. Das Label steht am Cog und nicht in
    # einer zentralen Liste, damit ein entferntes Modul auch aus der Hilfe
    # verschwindet, ohne dass man daran denken muss.
    MODUL_LABEL = "🔧 Administration"
    MODUL_REIHENFOLGE = 9
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="grant_access", description="Vergibt einem Mitglied eine Backend-Berechtigung",
        extras={"hilfe": "Gibt einer Person ein Recht für die Weboberfläche.\nDer Bot-Owner ist geschützt und kann hierüber nicht verändert werden. Jede Vergabe landet im Audit-Log.", "recht": "is_admin"},)
    @app_commands.describe(user="Der Discord-Nutzer", permission="Die zu vergebende Berechtigung")
    @app_commands.choices(permission=[app_commands.Choice(name=p, value=p) for p in database.ALL_PERMISSIONS])
    @app_commands.default_permissions(administrator=True)
    @is_admin()
    async def grant_access(self, ctx: commands.Context, user: discord.User, permission: str):
        if config.BOT_OWNER_ID and str(user.id) == config.BOT_OWNER_ID:
            database.log_audit(
                ctx.author.id, "owner_protection_triggered",
                target_id=config.BOT_OWNER_ID, detail=f"Versuch, Owner-Recht '{permission}' über /grant_access zu vergeben",
            )
            await ctx.send("⛔ Der Bot-Owner kann nicht über dieses Kommando verändert werden.")
            return
        if permission not in database.ALL_PERMISSIONS:
            await ctx.send(f"Ungültige Berechtigung. Erlaubt: {', '.join(database.ALL_PERMISSIONS)}")
            return
        database.grant_permission(user.id, permission, granted_by=ctx.author.id)
        await ctx.send(f"✅ {user.mention} hat jetzt die Berechtigung **{permission}**.")

    @commands.hybrid_command(name="revoke_access", description="Entzieht einem Mitglied eine Backend-Berechtigung",
        extras={"hilfe": "Nimmt einer Person ein Recht für die Weboberfläche wieder weg.\nDer Bot-Owner ist geschützt. Jeder Entzug landet im Audit-Log.", "recht": "is_admin"},)
    @app_commands.describe(user="Der Discord-Nutzer", permission="Die zu entziehende Berechtigung")
    @app_commands.choices(permission=[app_commands.Choice(name=p, value=p) for p in database.ALL_PERMISSIONS])
    @app_commands.default_permissions(administrator=True)
    @is_admin()
    async def revoke_access(self, ctx: commands.Context, user: discord.User, permission: str):
        if config.BOT_OWNER_ID and str(user.id) == config.BOT_OWNER_ID:
            database.log_audit(
                ctx.author.id, "owner_protection_triggered",
                target_id=config.BOT_OWNER_ID, detail=f"Versuch, Owner-Recht '{permission}' über /revoke_access zu entziehen",
            )
            await ctx.send("⛔ Der Bot-Owner kann nicht über dieses Kommando verändert werden.")
            return
        database.revoke_user_permission(user.id, permission, actor_id=ctx.author.id)
        await ctx.send(f"🚫 {user.mention} hat die Berechtigung **{permission}** nicht mehr.")

    @commands.hybrid_command(name="dm", description="Schickt einem Mitglied eine Nachricht in meinem Namen",
        extras={"hilfe": "Ich schicke der angegebenen Person eine Direktnachricht. Für den Empfänger sieht es so aus, als käme sie von mir — dein Name steht nicht dabei, der Vorgang wird aber im Audit-Log festgehalten.\nHat die Person ihre DMs zu, sage ich dir das.", "recht": "send_dm"},)
    @app_commands.describe(user="Der Discord-Nutzer", nachricht="Der Nachrichtentext")
    @app_commands.default_permissions(moderate_members=True)
    @has_permission_check("send_dm")
    async def dm(self, ctx: commands.Context, user: discord.User, *, nachricht: str):
        try:
            await user.send(nachricht)
        except discord.Forbidden:
            await ctx.send(
                f"⚠️ Konnte {user.mention} keine DM senden (DMs deaktiviert oder Bot blockiert).",
                ephemeral=True,
            )
            return
        await ctx.send(f"✅ Nachricht an {user.mention} gesendet.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
