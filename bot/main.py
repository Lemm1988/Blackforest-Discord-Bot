"""Kombinierter Einstiegspunkt: startet Bot und Flask-Backend in einem Prozess.

Das Flask-Backend läuft in einem Hintergrund-Thread und wird erst im on_ready-Event
des Bots gestartet, damit bot.loop garantiert existiert, wenn das Backend versucht,
Coroutinen auf ihr zu planen (asyncio.run_coroutine_threadsafe).
"""
import hashlib
import sys
import threading
import traceback
from pathlib import Path

import discord
from discord.ext import commands

sys.path.insert(0, str(Path(__file__).parent.parent))  # damit "backend" importierbar ist

import config
import database
import fehler
import verdiente_rollen
from backend.app import create_app
from backend.internal_app import create_internal_app

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.messages = True
intents.reactions = True
intents.guilds = True

# command_prefix=when_mentioned schaltet die Textbefehle ab: Es gibt kein
# Zeichen mehr, mit dem sich ein Befehl aus dem Chat heraus aufrufen liesse.
# discord.py verlangt zwingend ein Praefix, deshalb die Erwaehnung des Bots
# selbst - die kann niemand versehentlich tippen.
#
# Vorher war es ">". Ein solcher Befehl ist fuer Discord eine gewoehnliche
# Chatnachricht, die der Bot selbst durchsucht - er taucht in keiner
# Vorschlagsliste auf, hat keine Beschreibung und ist fuer neue Mitglieder
# praktisch unauffindbar. Genau deshalb die Umstellung (Vorgabe Patrick,
# 03.09.2026: ">" komplett raus, "/" rein).
#
# Wer trotzdem noch ">xp" tippt, bekommt einen freundlichen Hinweis auf die neue
# Schreibweise statt Schweigen - siehe cogs/hilfe.py.
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
# help_command=None schaltet die eingebaute Hilfe von discord.py ab. Sie muss
# weichen, weil sie einen Prefix-Befehl namens "help" belegt - unser eigener
# /help (cogs/hilfe.py) koennte sonst gar nicht registriert werden. Inhaltlich
# ist sie ohnehin kein Verlust: Sie ist englisch formatiert, zeigt jedem alles
# und existiert nur als Prefix-Variante, verschwaende also mit ">" ganz.

_flask_started = False


async def load_cogs():
    # Bedingungen fuer verdiente Rollen der installierten Module laden.
    # Gleiche Liste und gleicher Gedanke wie bei achievements.lade_module():
    # Ohne Quizmodul steht "Richtig beantwortete Quizfragen" gar nicht erst zur
    # Auswahl, statt eine Regel zu erlauben, die nie greift.
    verdiente_rollen.lade_module("kern", "quiz", "xp_level")

    for extension in ("hilfe", "quiz", "xp_level", "plants", "fun", "moderation", "reactions", "verdiente_rollen", "giveaway", "admin", "personality"):
        await bot.load_extension(f"cogs.{extension}")


def start_flask_thread():
    # Produktions-WSGI-Server (waitress) statt Flask-Dev-Server. Waitress kann kein TLS,
    # deshalb bindet es nur lokal (INTERNAL_HOST/PORT) und nginx uebernimmt die
    # TLS-Terminierung nach aussen auf FLASK_PORT (siehe /etc/nginx/conf.d/blackforestbot.conf).
    # Waitress laeuft als reiner Thread im selben Prozess (kein Forking wie bei gunicorn),
    # damit bot.loop fuer asyncio.run_coroutine_threadsafe weiterhin geteilt werden kann.
    from waitress import serve

    app = create_app(bot, bot.loop)

    def run():
        serve(app, host=config.INTERNAL_HOST, port=config.INTERNAL_PORT, _quiet=True)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    print(
        f"Waitress gestartet auf http://{config.INTERNAL_HOST}:{config.INTERNAL_PORT} "
        f"(intern) -> nginx TLS-Proxy auf https://{config.FLASK_HOST}:{config.FLASK_PORT}"
    )


def start_internal_members_thread():
    # Bewusst ein ZWEITER, komplett separater waitress-Thread/Port statt einer
    # weiteren Route im normalen Backend (Port 5001) - siehe
    # backend/internal_app.py fuer die ausfuehrliche Begruendung, warum Port 5001
    # trotz "nur 127.0.0.1" tatsaechlich ueber den Iroh-Tunnel aus dem Internet
    # erreichbar ist und deshalb fuer einen ungeschuetzten Endpunkt nicht sicher waere.
    from waitress import serve

    internal_app = create_internal_app(bot, bot.loop)

    def run():
        serve(internal_app, host=config.INTERNAL_HOST, port=config.INTERNAL_MEMBERS_PORT, _quiet=True)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    print(
        f"Interner Mitglieder-Endpunkt gestartet auf http://{config.INTERNAL_HOST}:{config.INTERNAL_MEMBERS_PORT} "
        "(NUR fuer lokale Prozesse wie das Bot Connect Dashboard, KEIN nginx-Eintrag, NICHT getunnelt)"
    )


@bot.event
async def on_ready():
    global _flask_started
    print(f"Logged in as {bot.user}")
    database.bootstrap_admin(config.BOOTSTRAP_ADMIN_DISCORD_ID)

    # Stellt sicher, dass guild.members vollständig gecacht ist (nicht nur die
    # Teilmenge, die der Gateway initial mitschickt) - wichtig für /api/members
    # im Backend, damit die Mitgliederliste dort zuverlässig vollständig ist.
    for guild in bot.guilds:
        if not guild.chunked:
            await guild.chunk()

    await _sync_slash_commands()

    if not _flask_started:
        start_flask_thread()
        start_internal_members_thread()
        _flask_started = True


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    """Zentrale Fehlerbehandlung fuer Befehle - Chat wie Slash.

    Bis zum 03.09.2026 gab es sie nicht. Wer sich vertippte oder ein Argument
    vergass, bekam GAR KEINE Antwort: discord.py schrieb die Meldung auf stderr,
    und die war ihrerseits unsichtbar, weil dem Dienst PYTHONUNBUFFERED fehlte.
    Der Bot wirkte dadurch kaputt oder so, als ignoriere er den Nutzer.

    Alle Meldungen gehen NUR an den Aufrufer (ephemeral bzw. bei Chat-Befehlen
    als Antwort mit Erwaehnung). Eine Absage wie "dir fehlt die Berechtigung"
    muss nicht oeffentlich im Kanal stehen - Vorgabe Patrick, 03.09.2026.
    """
    # HybridCommandError verpackt den eigentlichen Fehler eine Ebene tiefer,
    # wenn ein Hybrid-Befehl als Slash-Befehl aufgerufen wurde.
    error = getattr(error, "original", error)

    befehl = ctx.command.qualified_name if ctx.command else ""
    signatur = ctx.command.signature if ctx.command else ""
    hinweis = fehler.verwendung(befehl, signatur) if befehl else ""

    if isinstance(error, commands.CommandNotFound):
        art, kontext = "unbekannter_befehl", {}
    elif isinstance(error, commands.CheckFailure):
        # str(error) traegt die Begruendung aus is_admin() bzw.
        # has_permission_check() - siehe cogs/admin.py.
        art, kontext = "fehlende_berechtigung", {"grund": str(error) or None}
    elif isinstance(error, commands.MissingRequiredArgument):
        art, kontext = "fehlendes_argument", {"parameter": error.param.name, "verwendung": hinweis}
    elif isinstance(error, (commands.BadArgument, commands.BadLiteralArgument,
                            commands.ConversionError, commands.UserInputError)):
        art, kontext = "falsches_argument", {"verwendung": hinweis}
    elif isinstance(error, commands.CommandOnCooldown):
        art, kontext = "cooldown", {"sekunden": error.retry_after}
    elif isinstance(error, commands.NoPrivateMessage):
        art, kontext = "nur_auf_server", {}
    else:
        art, kontext = "intern", {}
        # Nur bei unerwarteten Fehlern die vollstaendige Ausnahme ins Log - die
        # bekannten Faelle oben sind Bedienfehler und wuerden es nur zumuellen.
        print(f"[befehl] Unerwarteter Fehler in /{befehl or '?'}: {error!r}")
        traceback.print_exception(type(error), error, error.__traceback__)

    text = fehler.meldung(art, **kontext)
    if text is None:
        return
    try:
        await ctx.send(text, ephemeral=True)
    except discord.HTTPException as exc:
        # Antworten kann selbst scheitern (Interaktion abgelaufen, keine
        # Schreibrechte). Dann bleibt es beim Logeintrag - die Fehlerbehandlung
        # darf nicht ihrerseits einen Fehler ausloesen.
        print(f"[befehl] Fehlermeldung konnte nicht zugestellt werden: {exc}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    """Fuer reine Slash-Befehle ohne Chat-Gegenstueck.

    Die Hybrid-Befehle laufen ueber on_command_error oben; dieser Handler faengt
    ab, was dort nicht ankommt, damit kein Fall stumm bleibt.
    """
    error = getattr(error, "original", error)
    print(f"[befehl] Fehler in einem Slash-Befehl: {error!r}")
    text = fehler.meldung("intern")
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass


async def _sync_slash_commands():
    """Meldet die Befehle bei Discord an, damit sie im /-Menue erscheinen.

    Warum das ueberhaupt noetig ist: Ein Prefix-Befehl (>xp) ist fuer Discord
    eine gewoehnliche Chatnachricht - der Bot liest mit und erkennt sie selbst.
    Ein Slash-Befehl ist das Gegenteil: Discord zeichnet das Auswahlmenue, ohne
    den Bot zu fragen, und muss die Befehlsliste dafuer KENNEN. Sie liegt auf
    Discords Servern, nicht bei uns, und wird hier einmal dorthin uebertragen.

    Bis zum 03.09.2026 fehlte dieser Aufruf. Alle Befehle waren als
    commands.hybrid_command gebaut, also fuer beide Wege vorgesehen - die
    Prefix-Haelfte lief, die Slash-Haelfte war bei Discord schlicht unbekannt
    (nachgeprueft ueber die API: 0 registrierte Befehle, global wie serverweit).

    Serverspezifisch statt global, aus einem praktischen Grund: Eine
    guild-Registrierung wirkt SOFORT, eine globale rollt Discord ueber bis zu
    eine Stunde aus. Beim Entwickeln bedeutet das sonst, dass man nach einer
    Aenderung nicht weiss, ob sie nicht funktioniert oder nur noch nicht da ist.
    Der Bot laeuft ohnehin auf genau einem Server (bot.guilds[0] wird an vielen
    Stellen vorausgesetzt).

    Nur bei Aenderungen: Discord begrenzt, wie oft ein Befehlsbaum registriert
    werden darf. Bei Restart=always koennte ein Bot in einer Neustartschleife
    sonst dagegenlaufen. Deshalb ein Fingerabdruck der Befehlsliste in
    bot_state - ist er unveraendert, passiert nichts.
    """
    if not bot.guilds:
        print("[sync] Kein Server gefunden - Slash-Befehle nicht registriert.")
        return
    guild = discord.Object(id=bot.guilds[0].id)

    bot.tree.copy_global_to(guild=guild)
    befehle = sorted(bot.tree.get_commands(guild=guild), key=lambda c: c.qualified_name)
    # Der Fingerabdruck deckt alles ab, was Discord tatsaechlich angezeigt
    # bekommt - Name, Beschreibung, Parameter und Sichtbarkeit. Aendert sich nur
    # der Rumpf einer Funktion, ist keine Neuanmeldung noetig.
    beschreibung = "|".join(
        f"{c.qualified_name}:{getattr(c, 'description', '')}:"
        f"{getattr(c, 'default_permissions', None)}:"
        f"{','.join(p.name for p in getattr(c, 'parameters', []))}"
        for c in befehle
    )
    fingerabdruck = hashlib.sha256(beschreibung.encode()).hexdigest()

    if database.get_bot_state("slash_commands_hash") == fingerabdruck:
        print(f"[sync] {len(befehle)} Slash-Befehle unveraendert - keine Neuanmeldung noetig.")
        return

    try:
        registriert = await bot.tree.sync(guild=guild)
    except discord.Forbidden:
        # Tritt auf, wenn der Bot ohne den Geltungsbereich applications.commands
        # eingeladen wurde. Der Bot laeuft trotzdem weiter, die Prefix-Befehle
        # sind davon nicht betroffen.
        print("[sync] FEHLER: Discord verweigert die Anmeldung der Befehle "
              "(fehlender Geltungsbereich 'applications.commands'). Der Bot muss "
              "einmal ueber eine Einladungs-URL mit diesem Bereich neu autorisiert "
              "werden. Prefix-Befehle sind nicht betroffen.")
        return
    except discord.HTTPException as exc:
        print(f"[sync] FEHLER beim Anmelden der Slash-Befehle: {exc}")
        return

    database.set_bot_state("slash_commands_hash", fingerabdruck)
    print(f"[sync] {len(registriert)} Slash-Befehle bei Discord angemeldet: "
          f"{', '.join('/' + c.name for c in registriert)}")


async def main():
    database.init_db()
    await load_cogs()
    async with bot:
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
