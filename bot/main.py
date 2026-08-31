"""Kombinierter Einstiegspunkt: startet Bot und Flask-Backend in einem Prozess.

Das Flask-Backend läuft in einem Hintergrund-Thread und wird erst im on_ready-Event
des Bots gestartet, damit bot.loop garantiert existiert, wenn das Backend versucht,
Coroutinen auf ihr zu planen (asyncio.run_coroutine_threadsafe).
"""
import sys
import threading
from pathlib import Path

import discord
from discord.ext import commands

sys.path.insert(0, str(Path(__file__).parent.parent))  # damit "backend" importierbar ist

import config
import database
from backend.app import create_app
from backend.internal_app import create_internal_app

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.messages = True
intents.reactions = True
intents.guilds = True

bot = commands.Bot(command_prefix=config.COMMAND_PREFIX, intents=intents)

_flask_started = False


async def load_cogs():
    for extension in ("quiz", "plants", "fun", "moderation", "reactions", "giveaway", "admin", "personality"):
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

    if not _flask_started:
        start_flask_thread()
        start_internal_members_thread()
        _flask_started = True


async def main():
    database.init_db()
    await load_cogs()
    async with bot:
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
