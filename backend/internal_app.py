"""Winziger, KOMPLETT SEPARATER interner HTTP-Endpunkt NUR fuer die
Discord-Mitgliederliste - ausschliesslich fuer das Bot Connect Dashboard gedacht
(bot-connect-dashboard/app.py, ebenfalls nur 127.0.0.1), damit das Dashboard nicht
selbst einen Discord-Bot-Token/eine eigene API-Anbindung braucht.

WICHTIG, SICHERHEITSRELEVANT - bitte vor Aenderungen lesen:
Dieser Port hat KEINEN eigenen nginx-location-Eintrag und wird NIRGENDS getunnelt.
Das ist zwingend notwendig, nicht nur eine Bequemlichkeits-Entscheidung: Der
bestehende Haupt-Backend-Port (127.0.0.1:5001, siehe backend/app.py) sieht NUR wie
"rein lokal" aus, ist es aber tatsaechlich NICHT - der dumbpipe-Tunnel-Dienst
(dumbpipe-discordbot*.service) leitet Internet-Traffic ueber "listen-tcp --host
localhost:5000" ebenfalls an nginx weiter, welches wiederum per proxy_pass an
127.0.0.1:5001 durchreicht. Fuer nginx (und damit fuer Flask/ProxyFix) sind echte
Internet-Nutzer ueber den Tunnel und rein lokale Aufrufe auf Port 5001 NICHT
unterscheidbar (beide kommen bei nginx als 127.0.0.1 an, IP-basierte Pruefungen
helfen hier also NICHT). Eine ungeschuetzte Route auf Port 5001 waere deshalb
GENAUSO gut aus dem Internet erreichbar wie lokal.

Nur ein komplett SEPARATER Port, fuer den nginx buchstaeblich KEINEN
proxy_pass-Eintrag besitzt, ist strukturell unerreichbar fuer Tunnel-Traffic -
das ist der einzige Grund, warum dieser Endpunkt OHNE Login/Permission-Check
auskommt.

NIEMALS fuer diesen Port (Default 5052, siehe config.INTERNAL_MEMBERS_PORT):
- einen dumbpipe/Iroh-Tunnel-Dienst dafuer einrichten
- einen nginx-location-Eintrag dafuer ergaenzen
- ihn an 0.0.0.0 statt 127.0.0.1 binden
Jede dieser Aenderungen wuerde die Mitgliederliste ungeschuetzt aus dem Internet
erreichbar machen.
"""
import asyncio

from flask import Flask, jsonify


def create_internal_app(bot, bot_loop: asyncio.AbstractEventLoop) -> Flask:
    app = Flask(__name__)

    def run_on_bot(coro):
        future = asyncio.run_coroutine_threadsafe(coro, bot_loop)
        return future.result(timeout=15)

    @app.route("/members")
    def members():
        async def _get_members():
            if not bot.guilds:
                return []
            guild = bot.guilds[0]
            return [
                {"id": str(m.id), "username": str(m), "display_name": m.display_name}
                for m in guild.members
                if not m.bot
            ]
        return jsonify({"members": run_on_bot(_get_members())})

    return app
