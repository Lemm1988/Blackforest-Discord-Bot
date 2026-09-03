"""Flask-Backend mit Discord-OAuth2-Login (statt eigenem Passwortsystem).

Läuft im selben Prozess wie der Bot (siehe main.py), in einem eigenen Thread.
Aktionen, die den Bot selbst betreffen (Embed posten, Giveaway starten), werden
per asyncio.run_coroutine_threadsafe auf die Event-Loop des Bots geschickt,
statt wie im alten Design per internem HTTP-Call auf einen separaten Port.
"""
import asyncio
import functools
import logging
import math
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import discord
from flask import Flask, flash, redirect, render_template, request, session, url_for, jsonify
from markupsafe import escape
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import database
import modul_xp_level
import modul_quiz
import mood
import rollenabgleich
import scheduling
import verdiente_rollen
from . import oauth
from .moderation_rules import moderation_block_reason

log = logging.getLogger(__name__)

# Grenzwerte fuer das Discord-Embed-Objekt, exakt nach offizieller Doku:
# https://discord.com/developers/docs/resources/message#embed-object-embed-limits
EMBED_LIMITS = {
    "title": 256,
    "description": 4096,
    "field_name": 256,
    "field_value": 1024,
    "footer_text": 2048,
    "author_name": 256,
    "fields_max": 25,
    "total": 6000,
}


# Discord schreibt den Grund einer Moderationsaktion ueber den HTTP-Header
# X-Audit-Log-Reason ins Audit-Log des Servers. Die Dokumentation dazu ist
# eindeutig (nachgeschlagen am 03.09.2026 unter
# https://docs.discord.com/developers/resources/audit-log):
#
#     "The X-Audit-Log-Reason header supports 1-512 URL-encoded UTF-8 characters."
#
# Entscheidend ist "URL-encoded": Gezaehlt wird NACH der Kodierung. Ein "a"
# bleibt ein Zeichen, ein "ü" wird zu %C3%BC und belegt sechs. Ein deutscher
# Text reisst die Grenze also frueher, als das Zaehlen der Buchstaben vermuten
# laesst. Ohne Begrenzung antwortet Discord mit einer 400, die im Backend als
# "Kick fehlgeschlagen: 400 Bad Request" landet - und man sucht den Fehler bei
# den Rechten oder der Rollenhierarchie statt beim Begruendungstext.
AUDIT_REASON_MAX = 512

# Discord kann beim Bann die Nachrichten der letzten bis zu 7 Tage mitloeschen
# (delete_message_seconds, 0 bis 604800). Bewusst eine feste Auswahl statt eines
# freien Zahlenfelds: Der Wert wird gegen genau diese Liste geprueft, sonst
# koennte ein manipulierter Aufruf etwas Ungueltiges schicken und Discord
# antwortete wieder mit einer nichtssagenden Ablehnung.
#
# Die Voreinstellung ist und bleibt 0 - "nichts loeschen". Das ist die EINZIGE
# Aenderung in diesem Projekt mit nicht umkehrbarer Wirkung: Geloeschte
# Nachrichten sind weg, auch die von anderen Leuten in der Unterhaltung.
BAN_LOESCHZEITRAEUME = {
    "0": ("Keine löschen", 0),
    "3600": ("Letzte Stunde", 3600),
    "86400": ("Letzte 24 Stunden", 86400),
    "604800": ("Letzte 7 Tage", 604800),
}


def _kuerze_audit_grund(text: str) -> tuple[str, bool]:
    """Kuerzt den Grund so, dass er URL-kodiert unter Discords Grenze bleibt.

    Rueckgabe: (gekuerzter Text, wurde_gekuerzt).

    Bewusst kuerzen statt ablehnen: Die Alternative waere, dass ein Bann an
    einem zu langen Begruendungstext scheitert - in dem Moment, in dem man
    jemanden bannen will, ist das die falsche Prioritaet. Der VOLLSTAENDIGE Text
    wird ohnehin im eigenen Audit-Log (database.log_audit) abgelegt, das diese
    Beschraenkung nicht hat.

    Zeichenweise statt text[:512], weil sonst mitten in einem Mehrbyte-Zeichen
    geschnitten wuerde.
    """
    if len(quote(text, safe="")) <= AUDIT_REASON_MAX:
        return text, False

    # Die Reserve fuer das Auslassungszeichen muss ebenfalls KODIERT gerechnet
    # werden: "…" ist ein Zeichen im Text, aber %E2%80%A6 und damit neun im
    # Header. Mit einer Reserve von 1 lief das Ergebnis um acht Zeichen ueber
    # die Grenze - gefunden vom Test test_kein_schnitt_mitten_im_zeichen.
    ELLIPSE = "…"
    reserve = len(quote(ELLIPSE, safe=""))

    ergebnis = []
    laenge = 0
    for zeichen in text:
        kodiert = len(quote(zeichen, safe=""))
        if laenge + kodiert > AUDIT_REASON_MAX - reserve:
            break
        ergebnis.append(zeichen)
        laenge += kodiert
    return "".join(ergebnis).rstrip() + ELLIPSE, True


def _parse_embed_color(value):
    """Wandelt einen Hex-Farbstring (z.B. '#2f7a4d' oder '2f7a4d') in den von
    discord.Embed erwarteten Integer um. Leer/None -> kein Farbbalken (Discord
    zeigt dann seinen Standard-Grauton)."""
    text = str(value or "").strip().lstrip("#")
    if not text:
        return None
    try:
        color = int(text, 16)
    except ValueError:
        raise ValueError("Ungültiger Farbwert (erwartet Hex, z.B. #2f7a4d).")
    if not 0 <= color <= 0xFFFFFF:
        raise ValueError("Farbwert außerhalb des gültigen Bereichs.")
    return color


# Discord akzeptiert in Embed-Feldern nur diese Schemata. Alles andere wird mit
# "Invalid Form Body" abgelehnt - und zwar ERST in der Antwort auf das Senden,
# ohne zu sagen, WELCHES der sechs Adressfelder gemeint war. Bei einem Embed mit
# Bild, Banner, Autorensymbol und Footersymbol darf man dann raten.
ERLAUBTE_URL_SCHEMATA = ("http://", "https://", "attachment://")


def _pruefe_url(wert: str, feldname: str) -> str | None:
    """Prueft eine Adresse und nennt im Fehlerfall das betroffene Feld.

    Leer ist in Ordnung - die Felder sind alle freiwillig.

    BEWUSST nur eine Formatpruefung: Ob die Adresse erreichbar ist oder
    wirklich ein Bild liefert, wird nicht geprueft. Das hiesse, beim Absenden
    eine fremde Adresse abzurufen - das kostet Zeit, kann haengen, und der
    Server wuerde damit auf Zuruf beliebige Adressen kontaktieren.
    """
    wert = (wert or "").strip()
    if not wert:
        return None
    if wert.lower().startswith(ERLAUBTE_URL_SCHEMATA):
        return wert

    # Der mit Abstand haeufigste Fall: Adresse aus der Adresszeile kopiert, ohne
    # das Schema. Sieht fuer Menschen wie eine Adresse aus, fuer Discord nicht.
    if "." in wert.split("/")[0] and "://" not in wert:
        raise ValueError(
            f"{feldname}: „{wert[:60]}\u201c ist keine vollständige Adresse. "
            f"Fehlt vielleicht https:// am Anfang?"
        )
    raise ValueError(
        f"{feldname}: Discord akzeptiert hier nur Adressen, die mit http://, "
        f"https:// oder attachment:// beginnen."
    )


def _build_embed_from_payload(data: dict) -> discord.Embed:
    """Baut ein discord.Embed aus dem JSON-Payload der Embed-Builder-Seiten
    (create_embed.html sowie der Embed-Modus von send_dm.html - gemeinsames
    Formular-Markup in _embed_builder.html / gemeinsame Vorschau-Logik in
    static/js/embed-builder.js). Erzwingt serverseitig dieselben Zeichenlimits,
    die das Frontend live anzeigt: die Anzeige dort ist nur Komfort, /api/embed
    und /api/dm koennen auch direkt (ohne das Formular) aufgerufen werden, die
    eigentliche Grenze muss also hier greifen.
    """
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    fields = data.get("fields") or []
    thumbnail = str(data.get("thumbnail") or "").strip()
    banner = str(data.get("banner") or "").strip()
    author_name = str(data.get("author_name") or "").strip()
    footer_text = str(data.get("footer") or "").strip()

    if not any([title, description, fields, thumbnail, banner, author_name]):
        raise ValueError("Embed braucht mindestens Titel, Beschreibung, ein Feld oder ein Bild.")
    if len(title) > EMBED_LIMITS["title"]:
        raise ValueError(f"Titel darf maximal {EMBED_LIMITS['title']} Zeichen haben.")
    if len(description) > EMBED_LIMITS["description"]:
        raise ValueError(f"Beschreibung darf maximal {EMBED_LIMITS['description']} Zeichen haben.")
    # Erst filtern, dann zaehlen: Das Formular schickt angelegte, aber nicht
    # ausgefuellte Feldzeilen mit. Wer 26 Zeilen anlegt und drei leer laesst, hat
    # 23 echte Felder - und bekam vorher trotzdem die Meldung, es seien zu viele.
    gefuellte_felder = [
        f for f in fields
        if str(f.get("name") or "").strip() and str(f.get("value") or "").strip()
    ]
    if len(gefuellte_felder) > EMBED_LIMITS["fields_max"]:
        raise ValueError(f"Maximal {EMBED_LIMITS['fields_max']} Felder pro Embed erlaubt.")

    embed = discord.Embed(
        title=title or None,
        description=description or None,
        color=_parse_embed_color(data.get("color")),
        url=_pruefe_url(str(data.get("url") or ""), "Titel-Link"),
    )

    if author_name:
        if len(author_name) > EMBED_LIMITS["author_name"]:
            raise ValueError(f"Autor-Name darf maximal {EMBED_LIMITS['author_name']} Zeichen haben.")
        embed.set_author(
            name=author_name,
            url=_pruefe_url(str(data.get("author_url") or ""), "Autoren-Link"),
            icon_url=_pruefe_url(str(data.get("author_icon") or ""), "Autoren-Symbol"),
        )

    for field in fields:
        name = str(field.get("name") or "").strip()
        value = str(field.get("value") or "").strip()
        if not name or not value:
            continue
        if len(name) > EMBED_LIMITS["field_name"]:
            raise ValueError(f"Feldname darf maximal {EMBED_LIMITS['field_name']} Zeichen haben.")
        if len(value) > EMBED_LIMITS["field_value"]:
            raise ValueError(f"Feldwert darf maximal {EMBED_LIMITS['field_value']} Zeichen haben.")
        embed.add_field(name=name, value=value, inline=bool(field.get("inline", False)))

    if thumbnail:
        embed.set_thumbnail(url=_pruefe_url(thumbnail, "Vorschaubild"))
    if banner:
        embed.set_image(url=_pruefe_url(banner, "Banner"))
    if footer_text:
        if len(footer_text) > EMBED_LIMITS["footer_text"]:
            raise ValueError(f"Footer darf maximal {EMBED_LIMITS['footer_text']} Zeichen haben.")
        embed.set_footer(
            text=footer_text,
            icon_url=_pruefe_url(str(data.get("footer_icon") or ""), "Footer-Symbol"),
        )

    if data.get("timestamp"):
        embed.timestamp = datetime.now(timezone.utc)

    if len(embed) > EMBED_LIMITS["total"]:
        raise ValueError(f"Embed ist insgesamt zu groß (max. {EMBED_LIMITS['total']} Zeichen über alle Textfelder).")

    return embed


def create_app(bot, bot_loop: asyncio.AbstractEventLoop) -> Flask:
    app = Flask(__name__)
    app.secret_key = config.FLASK_SECRET_KEY or secrets.token_hex(32)
    # nginx terminiert TLS und reicht X-Forwarded-Proto/-For weiter (siehe
    # /etc/nginx/conf.d/blackforestbot.conf) - ProxyFix sorgt dafuer, dass Flask
    # das korrekt als "https" erkennt statt das interne, unverschluesselte
    # Waitress-Protokoll (http) anzunehmen.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    def run_on_bot(coro):
        """Führt eine Bot-Coroutine thread-sicher aus und wartet auf das Ergebnis."""
        future = asyncio.run_coroutine_threadsafe(coro, bot_loop)
        return future.result(timeout=15)

    def is_guild_member(discord_user_id) -> bool:
        """Rein synchrone Cache-Abfrage (guild.get_member() ist keine Coroutine,
        braucht also keinen run_on_bot-Umweg über die Bot-Event-Loop)."""
        if not bot.guilds:
            return False
        return bot.guilds[0].get_member(int(discord_user_id)) is not None

    def require_login(view):
        """Nur eingeloggt, keine weitere Prüfung - für Routen, die selbst intern
        differenzieren (z.B. /game: Leaderboard vs. Einstellungen)."""
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if "discord_user_id" not in session:
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapped

    def require_guild_member(view):
        """Basis-Berechtigung: jedes AKTUELLE Guild-Mitglied, ganz ohne DB-Eintrag
        in user_permissions. Wer den Server verlassen hat, verliert auch diese
        Basis-Rechte wieder (anders als bei explizit vergebenen Permission-Flags,
        die absichtlich unabhängig von der aktuellen Mitgliedschaft gelten)."""
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if "discord_user_id" not in session:
                return redirect(url_for("login"))
            if not is_guild_member(session["discord_user_id"]):
                return render_template("no_access.html"), 403
            return view(*args, **kwargs)
        return wrapped

    def require_permission(key):
        """Erfordert genau dieses Flag ODER is_admin (is_admin ist in
        database.has_permission() bereits als generischer Bypass eingebaut)."""
        def decorator(view):
            @functools.wraps(view)
            def wrapped(*args, **kwargs):
                if "discord_user_id" not in session:
                    return redirect(url_for("login"))
                if not database.has_permission(session["discord_user_id"], key):
                    return render_template("no_access.html"), 403
                return view(*args, **kwargs)
            return wrapped
        return decorator

    def require_any_permission(*keys):
        """Für geteilte Hilfsrouten (z.B. /api/channels, von Embed- UND
        Giveaway-Formular genutzt): reicht, wenn eines der Flags vorhanden ist."""
        def decorator(view):
            @functools.wraps(view)
            def wrapped(*args, **kwargs):
                if "discord_user_id" not in session:
                    return redirect(url_for("login"))
                uid = session["discord_user_id"]
                if not any(database.has_permission(uid, k) for k in keys):
                    return render_template("no_access.html"), 403
                return view(*args, **kwargs)
            return wrapped
        return decorator

    @app.context_processor
    def inject_permission_helper():
        """Macht has_permission() in JEDEM Template verfügbar (z.B. navbar.html),
        ohne dass jede einzelne Route sie separat mitgeben muss."""
        def _has_permission(key):
            if "discord_user_id" not in session:
                return False
            return database.has_permission(session["discord_user_id"], key)
        return {"has_permission": _has_permission}

    def _get_csrf_token():
        """Lazily erzeugtes, session-gebundenes CSRF-Token. Leichte, selbstgebaute
        Lösung statt einer zusätzlichen Abhängigkeit (Flask-WTF o.ä.) - für die
        Größe dieses Projekts (klassische Server-Formulare + eine Handvoll
        JSON-Fetch-Aufrufe, kein SPA-Framework) reicht ein einzelnes, pro Session
        einmal erzeugtes Token mit Vergleich bei jeder zustandsändernden Anfrage."""
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return token

    @app.context_processor
    def inject_csrf_token():
        # Als Funktion (nicht direkt der Wert), damit Templates es klassisch als
        # {{ csrf_token() }} aufrufen können - das ist die in Flask-Projekten
        # übliche Konvention (z.B. auch bei Flask-WTF), falls hier später doch
        # mal auf eine Bibliothek umgestiegen wird.
        return {"csrf_token": _get_csrf_token}

    @app.before_request
    def _enforce_session_invalidation():
        """Sicherheits-Fix: erzwingt sofortigen Logout, falls die aktuelle Session
        VOR einem serverseitigen Invalidierungs-Zeitpunkt fuer diesen User
        ausgestellt wurde (database.invalidate_user_session(), z.B. beim Widerruf
        eines Tunnel-Zugangs ueber scripts/tunnel_access_core.py). Ohne das bliebe
        ein bereits bestehendes Discord-OAuth-Session-Cookie im Browser trotz
        entzogenem Zugang technisch weiter gueltig - Netzwerk-Tunnel und
        Backend-Session sind sonst zwei unabhaengige Zugriffswege.

        Laeuft VOR der CSRF-Pruefung (Registrierungsreihenfolge), da eine bereits
        ungueltige Session gar nicht erst gegen CSRF geprueft werden muss.
        """
        if "discord_user_id" not in session:
            return
        login_time = session.get("login_time")
        if not login_time:
            # Session von vor diesem Feature (kein login_time gesetzt) - einmalig
            # nachtragen statt sie faelschlich als ungueltig zu behandeln.
            session["login_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return
        invalidated_at = database.get_session_invalidated_at(session["discord_user_id"])
        if invalidated_at and login_time < invalidated_at:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({"error": "Sitzung wurde beendet (Zugang widerrufen). Bitte erneut einloggen."}), 401
            flash("Deine Sitzung wurde beendet (Zugang widerrufen). Bitte erneut einloggen.", "error")
            return redirect(url_for("login"))

    @app.before_request
    def _enforce_csrf():
        """CSRF-Schutz für alle zustandsändernden Anfragen (POST/PUT/PATCH/DELETE)
        eingeloggter Nutzer: Token entweder als verstecktes Formularfeld
        (klassische POST-Formulare) oder als X-CSRF-Token-Header (die drei
        JSON-Fetch-Endpunkte /api/embed, /api/dm, /api/giveaway).

        Nicht eingeloggte Anfragen werden hier durchgelassen - es gibt keine
        POST-Route ohne @require_*-Dekoration, die leitet ohnehin zum Login um,
        ein CSRF-403 davor wäre nur verwirrender als der normale Redirect.
        """
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return
        if "discord_user_id" not in session:
            return
        expected = session.get("csrf_token")
        provided = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if expected and provided and secrets.compare_digest(expected, provided):
            return
        log.warning("CSRF-Check fehlgeschlagen für %s %s (User %s).", request.method, request.path, session.get("discord_user_id"))
        if request.path.startswith("/api/"):
            return jsonify({"error": "Ungültiges oder abgelaufenes Sicherheits-Token. Bitte Seite neu laden."}), 403
        flash("Sicherheitscheck fehlgeschlagen (ungültiges oder abgelaufenes Formular-Token) - bitte erneut versuchen.", "error")
        return redirect(request.referrer or url_for("dashboard"))

    # --- Auth ---

    @app.route("/")
    def login():
        if "discord_user_id" in session:
            return redirect(url_for("dashboard"))
        if not config.OAUTH_CONFIGURED:
            return render_template("oauth_not_configured.html")
        return render_template("login.html")

    @app.route("/login/discord")
    def login_discord():
        if not config.OAUTH_CONFIGURED:
            return render_template("oauth_not_configured.html"), 500
        # Zufaelliger, in der Session gemerkter state-Wert gegen Login-CSRF: ohne
        # ihn koennte ein Angreifer einem Opfer einen eigenen, von ihm selbst
        # erzeugten Discord-Autorisierungscode unterschieben (der Code landet dann
        # im Callback und wuerde das Opfer unbemerkt in den Discord-Account des
        # Angreifers einloggen) - der state-Vergleich im Callback verhindert das.
        state = secrets.token_urlsafe(32)
        session["oauth_state"] = state
        return redirect(oauth.build_authorize_url(config.DISCORD_CLIENT_ID, config.REDIRECT_URI, state))

    @app.route("/callback")
    def callback():
        code = request.args.get("code")
        if not code:
            return "Kein OAuth-Code erhalten.", 400

        expected_state = session.pop("oauth_state", None)
        received_state = request.args.get("state")
        if not expected_state or not received_state or not secrets.compare_digest(expected_state, received_state):
            log.warning("OAuth-Callback mit fehlendem/falschem state-Parameter abgelehnt.")
            return "Ungültiger oder abgelaufener Login-Versuch (state-Mismatch). Bitte erneut einloggen.", 400

        try:
            token_data = oauth.exchange_code_for_token(
                config.DISCORD_CLIENT_ID, config.DISCORD_CLIENT_SECRET, config.REDIRECT_URI, code
            )
            discord_user = oauth.fetch_discord_user(token_data["access_token"])
        except Exception as exc:
            log.exception("OAuth-Login fehlgeschlagen")
            # escape(): exc-Text landet ungeprueft in der HTML-Antwort - ohne
            # Escaping waere das ein Reflected-XSS-Vektor, falls der Fehlertext
            # jemals von aussen beeinflussbaren Inhalt enthaelt.
            return f"OAuth-Login fehlgeschlagen: {escape(exc)}", 500

        discord_user_id = discord_user["id"]

        # Neu: Login-Voraussetzung ist jetzt aktuelle Guild-Mitgliedschaft statt
        # eines expliziten Permission-Eintrags - JEDES Mitglied bekommt die
        # Basis-Rechte (Fragen einreichen, Leaderboard ansehen), einzelne
        # Berechtigungen kommen zusätzlich obendrauf.
        if not is_guild_member(discord_user_id):
            return render_template("no_access.html", username=discord_user.get("username")), 403

        session["discord_user_id"] = discord_user_id
        session["username"] = discord_user.get("username")
        # Sicherheits-Fix: Zeitpunkt der Session-Ausstellung festhalten, damit ein
        # spaeterer Widerruf (z.B. eines Tunnel-Zugangs, siehe
        # scripts/tunnel_access_core.py) diese Session gezielt invalidieren kann,
        # siehe _enforce_session_invalidation() unten.
        session["login_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return redirect(url_for("dashboard"))

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # --- Dashboard ---

    @app.route("/dashboard")
    @require_guild_member
    def dashboard():
        stats = modul_quiz.get_question_stats()
        xp_progress = modul_xp_level.get_user_xp(session["discord_user_id"])
        return render_template("dashboard.html", stats=stats, xp_progress=xp_progress)

    # --- Pflanzenquiz: Leaderboard (Basis-Berechtigung) + Einstellungen (manage_game_settings) ---

    @app.route("/achievements")
    @require_guild_member
    def achievements_page():
        return render_template("achievements.html", overview=database.get_achievements_overview(session["discord_user_id"]))

    # Registry aller Spiele, die der Bot hat (Spec 1, Phase B1). Aktuell nur das
    # Pflanzenquiz - die Uebersichtsseite iteriert ueber diese Liste, ein neues
    # Spiel braucht also nur einen weiteren Eintrag hier plus seine eigene(n)
    # Route(n)/Template(s), keine Aenderung an der Uebersichtsseite selbst.
    GAMES = [
        {
            "slug": "quiz",
            "name": "Pflanzenquiz",
            "emoji": "🌿",
            "description": "Frage-Antwort-Quiz mit Punkten, XP/Level und Achievements.",
            "enabled_setting": "game_quiz_enabled",
            "page_endpoint": "quiz_game_page",
        },
    ]

    @app.route("/games")
    @require_guild_member
    def games_overview():
        games = [
            {
                **g,
                "enabled": database.get_setting(
                    g["enabled_setting"], config.DEFAULT_SETTINGS[g["enabled_setting"]]
                ) == "1",
            }
            for g in GAMES
        ]
        return render_template("games.html", games=games)

    @app.route("/games/quiz")
    @require_guild_member
    def quiz_game_page():
        per_page = 20
        page = request.args.get("page", 1, type=int)
        total_players = modul_quiz.count_players()
        total_pages = max(1, -(-total_players // per_page))
        page = max(1, min(page, total_pages))
        leaderboard_rows = modul_quiz.get_leaderboard_page(per_page, (page - 1) * per_page)

        async def _get_member_names():
            if not bot.guilds:
                return {}
            guild = bot.guilds[0]
            return {str(m.id): m.display_name for m in guild.members}

        member_names = run_on_bot(_get_member_names())

        leaderboard = [
            {
                "rank": (page - 1) * per_page + idx + 1,
                "user_id": row["user_id"],
                # Fallback auf die rohe ID, falls der Spieler den Server verlassen hat
                # und daher nicht (mehr) im Mitglieder-Cache auftaucht.
                "name": member_names.get(row["user_id"], row["user_id"]),
                "score": row["score"],
            }
            for idx, row in enumerate(leaderboard_rows)
        ]

        settings = None
        if database.has_permission(session["discord_user_id"], "manage_game_settings"):
            settings = {
                "enabled": database.get_setting(
                    "game_quiz_enabled", config.DEFAULT_SETTINGS["game_quiz_enabled"]
                ),
                "points": database.get_setting(
                    "quiz_points_correct", config.DEFAULT_SETTINGS["quiz_points_correct"]
                ),
                "timeout": database.get_setting(
                    "quiz_answer_timeout_seconds", config.DEFAULT_SETTINGS["quiz_answer_timeout_seconds"]
                ),
                "cooldown": database.get_setting(
                    "quiz_cooldown_seconds", config.DEFAULT_SETTINGS["quiz_cooldown_seconds"]
                ),
                "announcements_enabled": database.get_setting(
                    "announcements_enabled", config.DEFAULT_SETTINGS["announcements_enabled"]
                ),
                "announcement_channel_id": database.get_setting(
                    "announcement_channel_id", config.DEFAULT_SETTINGS["announcement_channel_id"]
                ),
            }

        return render_template(
            "game_quiz.html",
            leaderboard=leaderboard,
            page=page,
            total_pages=total_pages,
            settings=settings,
        )

    @app.route("/games/quiz/settings", methods=["POST"])
    @require_permission("manage_game_settings")
    def quiz_game_save_settings():
        form = request.form
        old_values = {
            "enabled": database.get_setting("game_quiz_enabled", config.DEFAULT_SETTINGS["game_quiz_enabled"]),
            "points": database.get_setting("quiz_points_correct", config.DEFAULT_SETTINGS["quiz_points_correct"]),
            "timeout": database.get_setting("quiz_answer_timeout_seconds", config.DEFAULT_SETTINGS["quiz_answer_timeout_seconds"]),
            "cooldown": database.get_setting("quiz_cooldown_seconds", config.DEFAULT_SETTINGS["quiz_cooldown_seconds"]),
            "announcements_enabled": database.get_setting("announcements_enabled", config.DEFAULT_SETTINGS["announcements_enabled"]),
            "announcement_channel_id": database.get_setting("announcement_channel_id", config.DEFAULT_SETTINGS["announcement_channel_id"]),
        }
        game_quiz_enabled = "1" if form.get("enabled") == "1" else "0"
        try:
            points = max(1, int(form.get("points", "50")))
        except (TypeError, ValueError):
            points = 50
        try:
            timeout = max(5, int(form.get("timeout", "30")))
        except (TypeError, ValueError):
            timeout = 30
        try:
            cooldown = max(0, int(form.get("cooldown", "5")))
        except (TypeError, ValueError):
            cooldown = 5
        announcements_enabled = "1" if form.get("announcements_enabled") == "1" else "0"
        announcement_channel_id = form.get("announcement_channel_id", "").strip()
        if announcement_channel_id and not announcement_channel_id.isdigit():
            announcement_channel_id = ""

        database.set_setting("game_quiz_enabled", game_quiz_enabled)
        database.set_setting("quiz_points_correct", str(points))
        database.set_setting("quiz_answer_timeout_seconds", str(timeout))
        database.set_setting("quiz_cooldown_seconds", str(cooldown))
        database.set_setting("announcements_enabled", announcements_enabled)
        database.set_setting("announcement_channel_id", announcement_channel_id)
        _log_settings_change("config_change", old_values, {
            "enabled": game_quiz_enabled,
            "points": str(points), "timeout": str(timeout), "cooldown": str(cooldown),
            "announcements_enabled": announcements_enabled, "announcement_channel_id": announcement_channel_id,
        })
        return redirect(url_for("quiz_game_page"))

    # --- XP & Level (eigenes Kern-Modul, Menü-Reorganisation) - gespeist sowohl
    # vom Quiz (xp_per_quiz) als auch von Nachrichten-XP, deshalb bewusst NICHT
    # unter Pflanzenquiz einsortiert, sondern eigener Top-Level-Navbar-Punkt. ---

    XP_LEVEL_SETTING_KEYS = (
        "xp_level_base", "xp_level_growth", "xp_cooldown_seconds", "xp_per_quiz",
        "message_xp_enabled", "xp_per_message", "daily_xp_limit",
    )

    @app.route("/xp-level")
    @require_guild_member
    def xp_level_page():
        settings = None
        if database.has_permission(session["discord_user_id"], "manage_game_settings"):
            settings = {
                key: database.get_setting(key, config.DEFAULT_SETTINGS[key]) for key in XP_LEVEL_SETTING_KEYS
            }
        return render_template("xp_level.html", settings=settings)

    @app.route("/xp-level/settings", methods=["POST"])
    @require_permission("manage_game_settings")
    def xp_level_save_settings():
        form = request.form
        old_values = {
            key: database.get_setting(key, config.DEFAULT_SETTINGS[key]) for key in XP_LEVEL_SETTING_KEYS
        }
        try:
            xp_per_quiz = max(0, int(form.get("xp_per_quiz", "10")))
        except (TypeError, ValueError):
            xp_per_quiz = 10
        try:
            xp_level_base = max(1, int(form.get("xp_level_base", "100")))
        except (TypeError, ValueError):
            xp_level_base = 100
        try:
            xp_level_growth = max(0, int(form.get("xp_level_growth", "50")))
        except (TypeError, ValueError):
            xp_level_growth = 50
        try:
            xp_cooldown_seconds = max(0, int(form.get("xp_cooldown_seconds", "60")))
        except (TypeError, ValueError):
            xp_cooldown_seconds = 60
        message_xp_enabled = "1" if form.get("message_xp_enabled") == "1" else "0"
        try:
            xp_per_message = max(0, int(form.get("xp_per_message", "2")))
        except (TypeError, ValueError):
            xp_per_message = 2
        try:
            daily_xp_limit = max(0, int(form.get("daily_xp_limit", "200")))
        except (TypeError, ValueError):
            daily_xp_limit = 200

        database.set_setting("xp_per_quiz", str(xp_per_quiz))
        database.set_setting("xp_level_base", str(xp_level_base))
        database.set_setting("xp_level_growth", str(xp_level_growth))
        database.set_setting("xp_cooldown_seconds", str(xp_cooldown_seconds))
        database.set_setting("message_xp_enabled", message_xp_enabled)
        database.set_setting("xp_per_message", str(xp_per_message))
        database.set_setting("daily_xp_limit", str(daily_xp_limit))
        _log_settings_change("config_change", old_values, {
            "xp_per_quiz": str(xp_per_quiz), "xp_level_base": str(xp_level_base),
            "xp_level_growth": str(xp_level_growth), "xp_cooldown_seconds": str(xp_cooldown_seconds),
            "message_xp_enabled": message_xp_enabled, "xp_per_message": str(xp_per_message),
            "daily_xp_limit": str(daily_xp_limit),
        })
        return redirect(url_for("xp_level_page"))

    # --- Fragen einreichen (Basis-Berechtigung: jedes aktuelle Guild-Mitglied) ---

    @app.route("/dbot")
    @require_guild_member
    def bot_question_insert():
        user_questions = modul_quiz.get_questions_by_user(session["discord_user_id"])
        return render_template("dbot.html", user_questions=user_questions, categories=modul_quiz.get_categories())

    @app.route("/qinput", methods=["POST"])
    @require_guild_member
    def submit_question():
        form = request.form
        question = (form.get("question") or "").strip()
        option_a = (form.get("option_a") or "").strip()
        option_b = (form.get("option_b") or "").strip()
        option_c = (form.get("option_c") or "").strip()
        option_d = (form.get("option_d") or "").strip()
        correct_option = (form.get("correct_option") or "").strip().upper()
        # Gemeinsame Validierung mit dem Discord-Befehl >frage_einreichen (siehe
        # database.validate_question_submission()) - eine Quelle der Wahrheit fuer
        # die Regeln statt zweier unabhaengig gepflegter Kopien.
        error = database.validate_question_submission(question, option_a, option_b, option_c, option_d, correct_option)
        if error:
            return jsonify({"error": error}), 400

        category_id_raw = form.get("category_id") or None
        category_id = None
        if category_id_raw:
            try:
                category_id = int(category_id_raw)
            except (TypeError, ValueError):
                return jsonify({"error": "Ungültige Kategorie."}), 400

        question_id = modul_quiz.submit_question(
            question, option_a, option_b, option_c, option_d, correct_option,
            session["discord_user_id"],
            category_id=category_id,
        )
        return jsonify({"status": "True", "question_id": question_id, "new_question": question})

    # --- Fragen verifizieren (verify_questions) ---

    @app.route("/verify_questions", methods=["GET", "POST"])
    @require_permission("verify_questions")
    def verify_questions():
        per_page = 10

        if request.method == "POST":
            form = request.form
            for key in form:
                if key.startswith("delete_question_"):
                    modul_quiz.delete_question(int(key.split("_")[-1]))
                elif key.startswith("edit_question_"):
                    qid = int(key.split("_")[-1])
                    category_raw = form.get(f"category_{qid}")
                    modul_quiz.update_question(
                        qid,
                        form.get(f"question_{qid}"),
                        form.get(f"answer_{qid}[A]"),
                        form.get(f"answer_{qid}[B]"),
                        form.get(f"answer_{qid}[C]"),
                        form.get(f"answer_{qid}[D]"),
                        form.get(f"correct_option_{qid}"),
                        verified=form.get(f"verified_{qid}") == "1",
                        category_id=int(category_raw) if category_raw else None,
                    )

        page_unverified = request.args.get("page_unverified", 1, type=int)
        page_verified = request.args.get("page_verified", 1, type=int)

        unverified = modul_quiz.get_questions(False, per_page, (page_unverified - 1) * per_page)
        verified = modul_quiz.get_questions(True, per_page, (page_verified - 1) * per_page)

        total_unverified = modul_quiz.count_questions(False)
        total_verified = modul_quiz.count_questions(True)

        return render_template(
            "verify_questions.html",
            unverified_questions=unverified,
            verified_questions=verified,
            page_unverified=page_unverified,
            page_verified=page_verified,
            total_unverified_pages=max(1, -(-total_unverified // per_page)),
            total_verified_pages=max(1, -(-total_verified // per_page)),
            categories=modul_quiz.get_categories(),
        )

    # --- Kategorien verwalten (manage_categories) ---

    @app.route("/categories")
    @require_permission("manage_categories")
    def categories_page():
        return render_template(
            "categories.html",
            categories=modul_quiz.get_categories(),
            question_counts=modul_quiz.count_questions_by_category(),
        )

    @app.route("/categories/add", methods=["POST"])
    @require_permission("manage_categories")
    def categories_add():
        name = request.form.get("name", "").strip()
        emoji = request.form.get("emoji", "").strip() or None
        if name:
            modul_quiz.add_category(name, emoji)
        return redirect(url_for("categories_page"))

    @app.route("/categories/update", methods=["POST"])
    @require_permission("manage_categories")
    def categories_update():
        form = request.form
        # Eine einzige Tabelle/Form fuer alle Kategorien (wie bei /verify_questions):
        # der geklickte Speichern-Button traegt die id in seinem Namen.
        for key in form:
            if key.startswith("save_category_"):
                category_id = int(key.split("_")[-1])
                name = form.get(f"name_{category_id}", "").strip()
                emoji = form.get(f"emoji_{category_id}", "").strip() or None
                if name:
                    modul_quiz.update_category(category_id, name, emoji)
        return redirect(url_for("categories_page"))

    @app.route("/categories/delete/<int:category_id>", methods=["POST"])
    @require_permission("manage_categories")
    def categories_delete(category_id):
        modul_quiz.delete_category(category_id)
        return redirect(url_for("categories_page"))

    # --- Kanalliste (für Embed-/Giveaway-Formulare + jedes Kanal-Auswahl-Dropdown,
    # z.B. auf /personality und /moderation - Phase B4-Nachtrag) ---

    @app.route("/api/channels")
    @require_any_permission("create_embed", "create_giveaway", "manage_game_settings", "manage_badword_filter")
    def api_channels():
        async def _get_channels():
            return [
                {"id": str(c.id), "name": c.name}
                for c in bot.get_all_channels()
                if isinstance(c, discord.TextChannel)
            ]
        channels = run_on_bot(_get_channels())
        return jsonify({"channels": channels})

    # --- Rollenliste (für den Rollen-Picker, aktuell nur Link-Filter-Rollen-
    # Whitelist - Link-Filter-Ueberarbeitung) ---

    @app.route("/api/roles")
    @require_permission("manage_badword_filter")
    def api_roles():
        async def _get_roles():
            if not bot.guilds:
                return []
            guild = bot.guilds[0]
            return [{"id": str(r.id), "name": r.name} for r in guild.roles if r.name != "@everyone"]
        roles = run_on_bot(_get_roles())
        return jsonify({"roles": roles})

    # --- Mitgliederliste (für den wiederverwendbaren Member-Picker in Formularen) ---
    # Deckt Bots mit nur einer Guild ab (Standardfall hier: bot.guilds[0]). Für
    # Nutzer, die den Server bereits verlassen haben, greift weiterhin die manuelle
    # ID-Eingabe (z.B. /api/user_lookup beim DM-Formular), die davon unberührt bleibt.

    @app.route("/api/members")
    @require_any_permission("send_dm", "manage_badword_filter", "is_admin")
    def api_members():
        async def _get_members():
            if not bot.guilds:
                return []
            guild = bot.guilds[0]
            return [
                {
                    "id": str(m.id),
                    "username": str(m),
                    "display_name": m.display_name,
                    "avatar": str(m.display_avatar.url),
                }
                for m in guild.members
                if not m.bot
            ]
        members = run_on_bot(_get_members())
        return jsonify({"members": members})

    # --- Embed erstellen (create_embed) ---

    @app.route("/sendemb")
    @require_permission("create_embed")
    def create_embed_page():
        return render_template("create_embed.html")

    @app.route("/api/embed", methods=["POST"])
    @require_permission("create_embed")
    def send_embed():
        data = request.json or {}
        if not data.get("channel_id"):
            return jsonify({"error": "Kanal wird benötigt."}), 400

        try:
            embed = _build_embed_from_payload(data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        async def _post():
            channel = bot.get_channel(int(data["channel_id"]))
            if not channel:
                raise ValueError("Kanal nicht gefunden")
            await channel.send(embed=embed)

        try:
            run_on_bot(_post())
        except Exception as exc:
            log.exception("Embed senden fehlgeschlagen (Kanal %s)", data.get("channel_id"))
            return jsonify({"error": str(exc)}), 500
        return jsonify({"message": "Embed gesendet!"})

    # --- Nachricht senden / DM (send_dm) ---

    @app.route("/dm")
    @require_permission("send_dm")
    def send_dm_page():
        return render_template("send_dm.html")

    @app.route("/api/user_lookup")
    @require_any_permission("send_dm", "manage_badword_filter", "is_admin")
    def user_lookup():
        user_id = request.args.get("user_id", "").strip()
        if not user_id.isdigit():
            return jsonify({"error": "Ungültige Nutzer-ID"}), 400

        async def _lookup():
            user = await bot.fetch_user(int(user_id))
            return {"name": str(user), "avatar": str(user.display_avatar.url)}

        try:
            data = run_on_bot(_lookup())
        except discord.NotFound:
            return jsonify({"error": "Kein Nutzer mit dieser ID gefunden."}), 404
        except Exception as exc:
            log.exception("Nutzer-Lookup fehlgeschlagen (user_id=%s)", user_id)
            return jsonify({"error": str(exc)}), 500
        return jsonify(data)

    @app.route("/api/dm", methods=["POST"])
    @require_permission("send_dm")
    def send_dm():
        data = request.json or {}
        user_id = str(data.get("user_id", "")).strip()
        message = str(data.get("message", "")).strip()
        embed_payload = data.get("embed")

        embed = None
        if embed_payload:
            try:
                embed = _build_embed_from_payload(embed_payload)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        if not user_id:
            return jsonify({"error": "Nutzer-ID ist erforderlich."}), 400
        if not message and not embed:
            return jsonify({"error": "Nachricht oder Embed wird benötigt."}), 400
        if len(message) > 2000:
            return jsonify({"error": "Nachricht darf maximal 2000 Zeichen haben."}), 400
        if not user_id.isdigit():
            return jsonify({"error": "Nutzer-ID muss die numerische Discord-User-ID sein."}), 400

        async def _send():
            user = await bot.fetch_user(int(user_id))
            await user.send(content=message or None, embed=embed)
            return str(user)

        try:
            username = run_on_bot(_send())
        except discord.Forbidden:
            return jsonify({"error": "Der Nutzer hat DMs deaktiviert oder blockiert den Bot."}), 400
        except discord.NotFound:
            return jsonify({"error": "Kein Discord-Nutzer mit dieser ID gefunden."}), 404
        except Exception as exc:
            log.exception("DM senden fehlgeschlagen (user_id=%s)", user_id)
            return jsonify({"error": str(exc)}), 500
        return jsonify({"message": f"Nachricht an {username} gesendet."})

    # --- Automod: Schimpfwortfilter, Spam-/Flutschutz, Link-Filter (manage_badword_filter) ---

    def _filter_settings():
        return {
            "enabled": database.get_setting(
                "badword_filter_enabled", config.DEFAULT_SETTINGS["badword_filter_enabled"]
            ) == "1",
            "action": database.get_setting(
                "badword_filter_action", config.DEFAULT_SETTINGS["badword_filter_action"]
            ),
            "threshold": database.get_setting(
                "badword_timeout_threshold", config.DEFAULT_SETTINGS["badword_timeout_threshold"]
            ),
            "duration": database.get_setting(
                "badword_timeout_duration_minutes", config.DEFAULT_SETTINGS["badword_timeout_duration_minutes"]
            ),
            "expiry_mode": database.get_setting(
                "badword_violation_expiry_mode", config.DEFAULT_SETTINGS["badword_violation_expiry_mode"]
            ),
            "aggressive_normalization": database.get_setting(
                "badword_aggressive_normalization_enabled",
                config.DEFAULT_SETTINGS["badword_aggressive_normalization_enabled"],
            ) == "1",
        }

    def _spam_settings():
        return {
            "enabled": database.get_setting(
                "spam_filter_enabled", config.DEFAULT_SETTINGS["spam_filter_enabled"]
            ) == "1",
            "threshold": database.get_setting(
                "spam_message_threshold", config.DEFAULT_SETTINGS["spam_message_threshold"]
            ),
            "window": database.get_setting(
                "spam_time_window_seconds", config.DEFAULT_SETTINGS["spam_time_window_seconds"]
            ),
            "action": database.get_setting(
                "spam_filter_action", config.DEFAULT_SETTINGS["spam_filter_action"]
            ),
            "duration": database.get_setting(
                "spam_timeout_duration_minutes", config.DEFAULT_SETTINGS["spam_timeout_duration_minutes"]
            ),
        }

    def _link_settings():
        return {
            "enabled": database.get_setting(
                "link_filter_enabled", config.DEFAULT_SETTINGS["link_filter_enabled"]
            ) == "1",
            "block_all_enabled": database.get_setting(
                "link_filter_block_all_enabled", config.DEFAULT_SETTINGS["link_filter_block_all_enabled"]
            ) == "1",
            "whitelist_enabled": database.get_setting(
                "link_filter_whitelist_enabled", config.DEFAULT_SETTINGS["link_filter_whitelist_enabled"]
            ) == "1",
            "new_members_enabled": database.get_setting(
                "link_filter_new_members_enabled", config.DEFAULT_SETTINGS["link_filter_new_members_enabled"]
            ) == "1",
            "new_member_days": database.get_setting(
                "link_filter_new_member_days", config.DEFAULT_SETTINGS["link_filter_new_member_days"]
            ),
            "action": database.get_setting(
                "link_filter_action", config.DEFAULT_SETTINGS["link_filter_action"]
            ),
            "threshold": database.get_setting(
                "link_filter_timeout_threshold", config.DEFAULT_SETTINGS["link_filter_timeout_threshold"]
            ),
            "duration": database.get_setting(
                "link_filter_timeout_duration_minutes", config.DEFAULT_SETTINGS["link_filter_timeout_duration_minutes"]
            ),
        }

    def _log_settings_change(action, old: dict, new: dict):
        """Vergleicht altes/neues Settings-Dict und loggt nur, wenn sich
        tatsaechlich etwas geaendert hat (kein Log-Eintrag bei unveraendertem
        Speichern desselben Formulars)."""
        changes = {k: (old.get(k), v) for k, v in new.items() if old.get(k) != v}
        if changes:
            detail = "; ".join(f"{k}: {ov!r} -> {nv!r}" for k, (ov, nv) in changes.items())
            database.log_audit(session["discord_user_id"], action, detail=detail)


    @app.route("/moderation")
    @require_permission("manage_badword_filter")
    def moderation_page():
        role_ids = database.get_link_filter_whitelisted_role_ids()
        channel_ids = database.get_link_filter_exempt_channel_ids()

        async def _resolve_role_and_channel_names():
            if not bot.guilds:
                return {}, {}
            guild = bot.guilds[0]
            return (
                {str(r.id): r.name for r in guild.roles},
                {str(c.id): c.name for c in guild.text_channels},
            )

        role_names, channel_names = run_on_bot(_resolve_role_and_channel_names())
        whitelisted_roles = [{"id": rid, "name": role_names.get(rid, rid)} for rid in role_ids]
        exempt_channels = [{"id": cid, "name": channel_names.get(cid, cid)} for cid in channel_ids]

        return render_template(
            "moderation.html",
            badwords=database.get_badwords_full(),
            violations=database.get_badword_violations(50),
            settings=_filter_settings(),
            spam_settings=_spam_settings(),
            link_settings=_link_settings(),
            link_whitelist=database.get_link_whitelist_full(),
            whitelisted_roles=whitelisted_roles,
            exempt_channels=exempt_channels,
        )

    @app.route("/moderation/spam_settings", methods=["POST"])
    @require_permission("manage_badword_filter")
    def moderation_spam_settings():
        form = request.form
        old_values = _spam_settings()
        database.set_setting("spam_filter_enabled", "1" if form.get("spam_enabled") == "1" else "0")
        try:
            threshold = max(1, int(form.get("spam_threshold", "5")))
        except (TypeError, ValueError):
            threshold = 5
        try:
            window = max(1, int(form.get("spam_window", "5")))
        except (TypeError, ValueError):
            window = 5
        action = form.get("spam_action", "delete_warn_timeout")
        if action not in ("delete_only", "delete_warn", "delete_warn_timeout"):
            action = "delete_warn_timeout"
        try:
            duration = max(1, int(form.get("spam_duration", "10")))
        except (TypeError, ValueError):
            duration = 10
        database.set_setting("spam_message_threshold", str(threshold))
        database.set_setting("spam_time_window_seconds", str(window))
        database.set_setting("spam_filter_action", action)
        database.set_setting("spam_timeout_duration_minutes", str(duration))
        _log_settings_change("config_change", old_values, _spam_settings())
        return redirect(url_for("moderation_page"))

    @app.route("/moderation/link_settings", methods=["POST"])
    @require_permission("manage_badword_filter")
    def moderation_link_settings():
        form = request.form
        old_values = _link_settings()
        database.set_setting("link_filter_enabled", "1" if form.get("link_enabled") == "1" else "0")
        database.set_setting("link_filter_block_all_enabled", "1" if form.get("link_block_all") == "1" else "0")
        database.set_setting("link_filter_whitelist_enabled", "1" if form.get("link_whitelist_rule") == "1" else "0")
        database.set_setting("link_filter_new_members_enabled", "1" if form.get("link_new_members_rule") == "1" else "0")
        try:
            days = max(0, int(form.get("link_new_member_days", "3")))
        except (TypeError, ValueError):
            days = 3
        database.set_setting("link_filter_new_member_days", str(days))
        action = form.get("link_action", "delete_warn")
        if action not in ("delete_only", "delete_warn", "delete_warn_timeout"):
            action = "delete_warn"
        database.set_setting("link_filter_action", action)
        try:
            threshold = max(1, int(form.get("link_threshold", "3")))
        except (TypeError, ValueError):
            threshold = 3
        try:
            duration = max(1, int(form.get("link_duration", "10")))
        except (TypeError, ValueError):
            duration = 10
        database.set_setting("link_filter_timeout_threshold", str(threshold))
        database.set_setting("link_filter_timeout_duration_minutes", str(duration))
        _log_settings_change("config_change", old_values, _link_settings())

        # Rollen-Whitelist + Kanal-Ausnahmeliste: Mehrfachauswahl-Felder, komplett
        # ersetzt statt einzeln diffed (siehe database.set_link_filter_whitelisted_roles()).
        old_role_ids = sorted(database.get_link_filter_whitelisted_role_ids())
        new_role_ids = sorted(set(form.getlist("whitelisted_roles")))
        database.set_link_filter_whitelisted_roles(new_role_ids, added_by=session["discord_user_id"])
        if old_role_ids != new_role_ids:
            database.log_audit(
                session["discord_user_id"], "config_change",
                detail=f"link_filter_whitelisted_roles: {old_role_ids!r} -> {new_role_ids!r}",
            )

        old_channel_ids = sorted(database.get_link_filter_exempt_channel_ids())
        new_channel_ids = sorted(set(form.getlist("exempt_channels")))
        database.set_link_filter_exempt_channels(new_channel_ids, added_by=session["discord_user_id"])
        if old_channel_ids != new_channel_ids:
            database.log_audit(
                session["discord_user_id"], "config_change",
                detail=f"link_filter_exempt_channels: {old_channel_ids!r} -> {new_channel_ids!r}",
            )

        return redirect(url_for("moderation_page"))

    @app.route("/moderation/link_whitelist/add", methods=["POST"])
    @require_permission("manage_badword_filter")
    def moderation_link_whitelist_add():
        domain = request.form.get("domain", "").strip()
        if domain:
            database.add_link_domain(domain, session["discord_user_id"])
        return redirect(url_for("moderation_page"))

    @app.route("/moderation/link_whitelist/remove/<int:domain_id>", methods=["POST"])
    @require_permission("manage_badword_filter")
    def moderation_link_whitelist_remove(domain_id):
        database.remove_link_domain(domain_id)
        return redirect(url_for("moderation_page"))

    @app.route("/moderation/settings", methods=["POST"])
    @require_permission("manage_badword_filter")
    def moderation_save_settings():
        form = request.form
        old_values = _filter_settings()
        database.set_setting("badword_filter_enabled", "1" if form.get("filter_enabled") == "1" else "0")

        action = form.get("filter_action", "delete_warn")
        if action not in ("delete_only", "delete_warn", "delete_warn_timeout"):
            action = "delete_warn"
        database.set_setting("badword_filter_action", action)

        try:
            threshold = max(1, int(form.get("timeout_threshold", "3")))
        except (TypeError, ValueError):
            threshold = 3
        try:
            duration = max(1, int(form.get("timeout_duration", "10")))
        except (TypeError, ValueError):
            duration = 10
        database.set_setting("badword_timeout_threshold", str(threshold))
        database.set_setting("badword_timeout_duration_minutes", str(duration))

        expiry_mode = form.get("violation_expiry_mode", "auto_30d")
        if expiry_mode not in ("auto_30d", "manual_only"):
            expiry_mode = "auto_30d"
        database.set_setting("badword_violation_expiry_mode", expiry_mode)

        aggressive_normalization = "1" if form.get("aggressive_normalization") == "1" else "0"
        database.set_setting("badword_aggressive_normalization_enabled", aggressive_normalization)

        _log_settings_change("config_change", old_values, _filter_settings())
        return redirect(url_for("moderation_page"))

    @app.route("/moderation/reset_violations", methods=["POST"])
    @require_permission("manage_badword_filter")
    def moderation_reset_violations():
        user_id = request.form.get("user_id", "").strip()
        violation_type = request.form.get("violation_type", "badword")
        # "spam" bewusst ausgeschlossen: Flut-Erkennung reagiert sofort pro Ausbruch,
        # es gibt keine über Zeit angesammelte Zählung, die zurückgesetzt werden müsste.
        if violation_type not in ("badword", "link"):
            violation_type = "badword"
        if user_id.isdigit():
            database.reset_user_violations(user_id, violation_type=violation_type)
        return redirect(url_for("moderation_page"))

    @app.route("/moderation/add", methods=["POST"])
    @require_permission("manage_badword_filter")
    def moderation_add_word():
        word = request.form.get("word", "").strip()
        if word:
            database.add_badword(word, session["discord_user_id"])
        return redirect(url_for("moderation_page"))

    @app.route("/moderation/remove/<int:word_id>", methods=["POST"])
    @require_permission("manage_badword_filter")
    def moderation_remove_word(word_id):
        database.remove_badword(word_id)
        return redirect(url_for("moderation_page"))

    # --- Giveaway (create_giveaway) ---

    @app.route("/giveaway")
    @require_permission("create_giveaway")
    def create_giveaway_page():
        return render_template("giveaway.html")

    @app.route("/api/giveaway", methods=["POST"])
    @require_permission("create_giveaway")
    def start_giveaway():
        data = request.json or {}
        required = ["channel_id", "title", "description", "end_time", "color"]
        if not all(k in data for k in required):
            return jsonify({"error": "Fehlende Felder"}), 400

        async def _start():
            giveaway_cog = bot.get_cog("Giveaway")
            end_time = datetime.fromisoformat(data["end_time"]).astimezone()
            await giveaway_cog.create_giveaway(
                int(data["channel_id"]), data["title"], data["description"],
                end_time, int(data["color"].lstrip("#"), 16),
            )

        try:
            run_on_bot(_start())
        except Exception as exc:
            log.exception("Giveaway starten fehlgeschlagen (Kanal %s)", data.get("channel_id"))
            return jsonify({"error": str(exc)}), 500
        return jsonify({"message": "Giveaway gestartet!"})

    # --- Benutzerverwaltung (NUR is_admin - es gibt bewusst kein eigenes
    # "manage_users"-Flag, siehe database.ALL_PERMISSIONS) ---

    def _discord_rollen():
        """Alle Rollen des Servers, plus die Angabe, welche der Bot verwalten kann.

        Discord erlaubt einem Bot nur, Rollen UNTERHALB seiner eigenen hoechsten
        Rolle zu vergeben und zu entziehen. Rollen darueber werden trotzdem
        angezeigt, aber als nicht auswaehlbar markiert - sonst stellt man eine
        Zuordnung ein, die stillschweigend nie greift.
        """
        async def _holen():
            if not bot.guilds:
                return [], set()
            guild = bot.guilds[0]
            eigene_position = guild.me.top_role.position
            rollen = [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "position": r.position,
                    "verwaltbar": r.position < eigene_position and not r.managed,
                }
                for r in sorted(guild.roles, key=lambda r: -r.position)
                if r.name != "@everyone"
            ]
            verwaltbar = {r["id"] for r in rollen if r["verwaltbar"]}
            return rollen, verwaltbar

        return run_on_bot(_holen())

    # --- Verdiente Rollen (Bot-Setup) ---

    @app.route("/earned-roles")
    @require_permission("manage_badword_filter")
    def earned_roles_page():
        rollen, verwaltbare = _discord_rollen()
        rollennamen = {r["id"]: r["name"] for r in rollen}
        regeln = database.get_earned_roles()
        for r in regeln:
            r["rollenname"] = rollennamen.get(r["role_id"], f"unbekannt ({r['role_id']})")
            r["vergebbar"] = r["role_id"] in verwaltbare
            art = verdiente_rollen.BEDINGUNGSARTEN().get(r["bedingung_art"])
            r["bedingung_label"] = art.label if art else r["bedingung_art"]
            r["einheit"] = art.einheit if art else ""
        return render_template(
            "earned_roles.html",
            regeln=regeln,
            rollen=rollen,
            bedingungsarten=verdiente_rollen.BEDINGUNGSARTEN(),
        )

    @app.route("/earned-roles/add", methods=["POST"])
    @require_permission("manage_badword_filter")
    def earned_roles_add():
        form = request.form
        _, verwaltbare = _discord_rollen()

        role_id = (form.get("role_id") or "").strip()
        art = (form.get("bedingung_art") or "").strip()
        try:
            schwelle = int(form.get("schwelle", "0"))
        except (TypeError, ValueError):
            schwelle = 0

        if not verdiente_rollen.ist_gueltige_art(art):
            flash("Unbekannte Bedingung.", "error")
            return redirect(url_for("earned_roles_page"))
        if schwelle < 1:
            flash("Die Schwelle muss mindestens 1 sein.", "error")
            return redirect(url_for("earned_roles_page"))
        if role_id not in verwaltbare:
            flash("Diese Rolle steht über der Bot-Rolle — der Bot könnte sie nicht vergeben.", "error")
            return redirect(url_for("earned_roles_page"))

        channel_id = (form.get("channel_id") or "").strip()
        if channel_id and not channel_id.isdigit():
            channel_id = ""

        neu_id = database.add_earned_role(
            role_id=role_id, bedingung_art=art, schwelle=schwelle,
            ankuendigung=form.get("ankuendigung", ""), channel_id=channel_id,
            automatisch=form.get("automatisch") == "1",
        )
        database.log_audit(
            session["discord_user_id"], "earned_role_add", target_id=neu_id,
            detail=f"{art} >= {schwelle} -> Rolle {role_id}",
        )
        flash("Regel angelegt.", "success")
        return redirect(url_for("earned_roles_page"))

    @app.route("/earned-roles/<int:regel_id>/update", methods=["POST"])
    @require_permission("manage_badword_filter")
    def earned_roles_update(regel_id):
        form = request.form
        try:
            schwelle = max(1, int(form.get("schwelle", "1")))
        except (TypeError, ValueError):
            schwelle = None
        database.update_earned_role(
            regel_id, schwelle=schwelle,
            ankuendigung=form.get("ankuendigung", ""),
            automatisch=form.get("automatisch") == "1",
            enabled=form.get("enabled") == "1",
        )
        database.log_audit(session["discord_user_id"], "earned_role_update", target_id=regel_id)
        flash("Regel gespeichert.", "success")
        return redirect(url_for("earned_roles_page"))

    @app.route("/earned-roles/<int:regel_id>/delete", methods=["POST"])
    @require_permission("manage_badword_filter")
    def earned_roles_delete(regel_id):
        database.delete_earned_role(regel_id)
        database.log_audit(session["discord_user_id"], "earned_role_delete", target_id=regel_id)
        flash("Regel gelöscht. Bereits vergebene Rollen bleiben bestehen.", "success")
        return redirect(url_for("earned_roles_page"))

    # --- Reaktionsrollen (Bot-Setup) ---

    @app.route("/reaction-roles")
    @require_permission("manage_badword_filter")
    def reaction_roles_page():
        rollen, verwaltbare = _discord_rollen()
        rollennamen = {r["id"]: r["name"] for r in rollen}

        async def _kanalnamen():
            if not bot.guilds:
                return {}
            return {str(c.id): c.name for c in bot.guilds[0].text_channels}

        kanalnamen = run_on_bot(_kanalnamen())

        regeln = database.get_reaction_roles()
        for r in regeln:
            r["rollenname"] = rollennamen.get(r["role_id"], f"unbekannt ({r['role_id']})")
            r["kanalname"] = kanalnamen.get(r["channel_id"], f"unbekannt ({r['channel_id']})")
            # Eine Regel kann durch eine Hierarchie-Aenderung unwirksam werden,
            # ohne dass jemand sie anfasst. Das muss man sehen.
            r["vergebbar"] = r["role_id"] in verwaltbare
        return render_template(
            "reaction_roles.html",
            regeln=regeln,
            rollen=rollen,
        )

    @app.route("/reaction-roles/add", methods=["POST"])
    @require_permission("manage_badword_filter")
    def reaction_roles_add():
        form = request.form
        _, verwaltbare = _discord_rollen()

        channel_id = (form.get("channel_id") or "").strip()
        message_id = (form.get("message_id") or "").strip()
        emoji = (form.get("emoji") or "").strip()
        role_id = (form.get("role_id") or "").strip()

        if not (channel_id.isdigit() and message_id.isdigit() and role_id.isdigit() and emoji):
            flash("Bitte Kanal, Nachrichten-ID, Emoji und Rolle vollständig angeben.", "error")
            return redirect(url_for("reaction_roles_page"))
        if role_id not in verwaltbare:
            flash("Diese Rolle steht über der Bot-Rolle — der Bot könnte sie nicht vergeben.", "error")
            return redirect(url_for("reaction_roles_page"))
        if database.find_reaction_role(message_id, emoji):
            flash("Für diese Nachricht und dieses Emoji gibt es bereits eine Regel.", "error")
            return redirect(url_for("reaction_roles_page"))

        # Pruefen, ob es die Nachricht ueberhaupt gibt - und die Reaktion gleich
        # setzen. Sonst muesste jemand als Erster von Hand reagieren, damit
        # andere ueberhaupt draufklicken koennen.
        async def _reaktion_setzen():
            kanal = bot.get_channel(int(channel_id))
            if kanal is None:
                raise ValueError("Kanal nicht gefunden.")
            nachricht = await kanal.fetch_message(int(message_id))
            await nachricht.add_reaction(emoji)

        try:
            run_on_bot(_reaktion_setzen())
        except discord.NotFound:
            flash("In diesem Kanal gibt es keine Nachricht mit dieser ID.", "error")
            return redirect(url_for("reaction_roles_page"))
        except Exception as exc:
            log.exception("Reaktionsrolle anlegen fehlgeschlagen")
            flash(f"Die Reaktion konnte nicht gesetzt werden: {exc}", "error")
            return redirect(url_for("reaction_roles_page"))

        neu_id = database.add_reaction_role(
            channel_id=channel_id, message_id=message_id, emoji=emoji, role_id=role_id,
            entzug_erlaubt=form.get("entzug_erlaubt") == "1",
            dm_text=form.get("dm_text", ""),
        )
        database.log_audit(
            session["discord_user_id"], "reaction_role_add", target_id=neu_id,
            detail=f"{emoji} auf {message_id} -> Rolle {role_id}",
        )
        flash("Regel angelegt — die Reaktion wurde unter die Nachricht gesetzt.", "success")
        return redirect(url_for("reaction_roles_page"))

    @app.route("/reaction-roles/<int:regel_id>/update", methods=["POST"])
    @require_permission("manage_badword_filter")
    def reaction_roles_update(regel_id):
        form = request.form
        database.update_reaction_role(
            regel_id,
            enabled=form.get("enabled") == "1",
            entzug_erlaubt=form.get("entzug_erlaubt") == "1",
            dm_text=form.get("dm_text", ""),
        )
        database.log_audit(session["discord_user_id"], "reaction_role_update", target_id=regel_id)
        flash("Regel gespeichert.", "success")
        return redirect(url_for("reaction_roles_page"))

    @app.route("/reaction-roles/<int:regel_id>/delete", methods=["POST"])
    @require_permission("manage_badword_filter")
    def reaction_roles_delete(regel_id):
        database.delete_reaction_role(regel_id)
        database.log_audit(session["discord_user_id"], "reaction_role_delete", target_id=regel_id)
        flash("Regel gelöscht. Bereits vergebene Rollen bleiben bestehen.", "success")
        return redirect(url_for("reaction_roles_page"))

    def _rollen_anwenden(user_id, massnahme, rollennamen, grund: str) -> list[str]:
        """Fuehrt die Massnahme fuer EINE Person aus. Gibt aus, was passiert ist.

        Der Bot-Owner ist ausgenommen - wie bei den Moderationsaktionen. Und
        jede Vergabe wird vermerkt, damit der Bot spaeter nur wegnimmt, was er
        selbst gegeben hat.
        """
        if config.BOT_OWNER_ID and str(user_id) == config.BOT_OWNER_ID:
            return []

        async def _tun():
            guild = bot.guilds[0]
            member = guild.get_member(int(user_id))
            if member is None:
                return ["nicht mehr auf dem Server"]
            geschehen = []
            for role_id in massnahme.vergeben:
                rolle = guild.get_role(int(role_id))
                if rolle is None:
                    continue
                await member.add_roles(rolle, reason=f"Backend-Berechtigung ({grund})")
                database.merke_rollenvergabe(user_id, role_id)
                geschehen.append(f"+{rolle.name}")
            for role_id in massnahme.entziehen:
                rolle = guild.get_role(int(role_id))
                if rolle is None:
                    continue
                await member.remove_roles(rolle, reason=f"Backend-Berechtigung entzogen ({grund})")
                database.vergiss_rollenvergabe(user_id, role_id)
                geschehen.append(f"-{rolle.name}")
            return geschehen

        try:
            return run_on_bot(_tun())
        except Exception:
            log.exception("Rollenabgleich fuer %s fehlgeschlagen", user_id)
            return [f"Fehler bei {rollennamen.get(r, r)}" for r in
                    massnahme.vergeben + massnahme.entziehen]

    def _massnahme_fuer(user_id, zuordnung, verwaltbare, mitglieder_rollen):
        return rollenabgleich.massnahmen(
            rechte=set(database.get_user_permissions(user_id)),
            rollen=mitglieder_rollen.get(str(user_id), set()),
            zuordnung=zuordnung,
            verwaltbare_rollen=verwaltbare,
            vom_bot_vergeben=database.bot_vergebene_rollen(user_id),
        )

    def _mitglieder_rollen():
        async def _holen():
            if not bot.guilds:
                return {}
            return {
                str(m.id): {str(r.id) for r in m.roles if r.name != "@everyone"}
                for m in bot.guilds[0].members
            }
        return run_on_bot(_holen())

    def _rollen_nachziehen(user_id, grund: str) -> None:
        """Nach einer Rechteaenderung die Rollen sofort angleichen.

        Still im Hintergrund: Wer im Backend ein Recht vergibt, soll nicht noch
        einen zweiten Knopf druecken muessen. Ist keine Zuordnung eingerichtet,
        passiert nichts.
        """
        zuordnung = database.get_permission_role_map()
        if not zuordnung:
            return
        _, verwaltbare = _discord_rollen()
        massnahme = _massnahme_fuer(user_id, zuordnung, verwaltbare, _mitglieder_rollen())
        if massnahme.leer:
            return
        geschehen = _rollen_anwenden(user_id, massnahme, {}, grund)
        if geschehen:
            database.log_audit(
                session["discord_user_id"], "rollen_angeglichen", target_id=user_id,
                detail=", ".join(geschehen),
            )
            flash("Discord-Rollen angepasst: " + ", ".join(geschehen), "success")

    @app.route("/roles-mapping/sync", methods=["POST"])
    @require_permission("is_admin")
    def roles_mapping_sync():
        """Abgleich fuer ALLE - erst als Vorschau, dann nach Bestaetigung.

        Bewusst zweistufig: Ein Knopf, der auf einen Schlag Rollen bei allen
        Mitgliedern aendert, muss vorher zeigen, was er tut.
        """
        zuordnung = database.get_permission_role_map()
        if not zuordnung:
            flash("Es ist noch keine Rollen-Zuordnung eingerichtet.", "error")
            return redirect(url_for("roles_mapping_page"))

        rollen, verwaltbare = _discord_rollen()
        rollennamen = {r["id"]: r["name"] for r in rollen}
        mitglieder = _mitglieder_rollen()

        # Alle mit Berechtigungen, plus alle, die eine zugeordnete Rolle tragen
        betroffene = {u["discord_user_id"] for u in database.get_all_permissioned_users()}
        zugeordnet = set(zuordnung.values())
        betroffene |= {uid for uid, r in mitglieder.items() if r & zugeordnet}

        plan = []
        for uid in sorted(betroffene):
            if config.BOT_OWNER_ID and uid == config.BOT_OWNER_ID:
                continue
            m = _massnahme_fuer(uid, zuordnung, verwaltbare, mitglieder)
            if not m.leer:
                plan.append((uid, m))

        if request.form.get("bestaetigt") != "1":
            return render_template(
                "roles_sync_preview.html",
                plan=[{
                    "user_id": uid,
                    "vergeben": [rollennamen.get(r, r) for r in m.vergeben],
                    "entziehen": [rollennamen.get(r, r) for r in m.entziehen],
                } for uid, m in plan],
                anzahl=len(plan),
            )

        gesamt = []
        for uid, m in plan:
            geschehen = _rollen_anwenden(uid, m, rollennamen, "Abgleich")
            if geschehen:
                gesamt.append(f"{uid}: {', '.join(geschehen)}")
        if gesamt:
            database.log_audit(
                session["discord_user_id"], "rollen_abgleich", detail="; ".join(gesamt))
            flash(f"{len(gesamt)} Personen angepasst.", "success")
        else:
            flash("Es gab nichts anzugleichen.", "success")
        return redirect(url_for("users_page"))

    @app.route("/roles-mapping")
    @require_permission("is_admin")
    def roles_mapping_page():
        rollen, _ = _discord_rollen()
        return render_template(
            "roles_mapping.html",
            rollen=rollen,
            zuordnung=database.get_permission_role_map(),
            all_permissions=database.ALL_PERMISSIONS,
            permission_labels=config.PERMISSION_LABELS,
        )

    @app.route("/roles-mapping/save", methods=["POST"])
    @require_permission("is_admin")
    def roles_mapping_save():
        _, verwaltbar = _discord_rollen()
        alt = database.get_permission_role_map()
        geaendert = {}
        for key in database.ALL_PERMISSIONS:
            neu_wert = (request.form.get(f"role_{key}") or "").strip()
            # Nur Rollen zulassen, die der Bot auch verwalten kann. Ein
            # manipulierter Aufruf soll keine Zuordnung anlegen koennen, die
            # spaeter beim Vergeben an Discord scheitert.
            if neu_wert and neu_wert not in verwaltbar:
                flash("Diese Rolle steht über der Bot-Rolle und kann nicht zugeordnet werden.", "error")
                return redirect(url_for("roles_mapping_page"))
            if alt.get(key, "") != neu_wert:
                database.set_permission_role(key, neu_wert)
                geaendert[key] = neu_wert or "(keine)"
        if geaendert:
            database.log_audit(
                session["discord_user_id"], "permission_roles_change",
                detail="; ".join(f"{k} -> {v}" for k, v in geaendert.items()),
            )
            flash("Rollen-Zuordnung gespeichert.", "success")
        return redirect(url_for("roles_mapping_page"))

    @app.route("/users")
    @require_permission("is_admin")
    def users_page():
        async def _get_member_names():
            if not bot.guilds:
                return {}
            guild = bot.guilds[0]
            return {str(m.id): str(m) for m in guild.members}

        async def _rollen_je_mitglied():
            """Welche Rollen traegt wer - fuer den Abgleich mit den Backend-Rechten."""
            if not bot.guilds:
                return {}
            guild = bot.guilds[0]
            return {
                str(m.id): {str(r.id) for r in m.roles if r.name != "@everyone"}
                for m in guild.members
            }

        member_names = run_on_bot(_get_member_names())
        mitglieder_rollen = run_on_bot(_rollen_je_mitglied())
        rollen, verwaltbare = _discord_rollen()
        rollennamen = {r["id"]: r["name"] for r in rollen}
        zuordnung = database.get_permission_role_map()

        users = database.get_all_permissioned_users()
        for u in users:
            uid = u["discord_user_id"]
            u["name"] = member_names.get(uid, uid)
            u["auf_dem_server"] = uid in mitglieder_rollen
            eigene_rollen = mitglieder_rollen.get(uid, set())
            u["rollen"] = sorted(
                (rollennamen.get(r, r) for r in eigene_rollen), key=str.lower
            )
            ergebnis = rollenabgleich.abgleichen(
                rechte={p["key"] for p in u["permissions"]},
                rollen=eigene_rollen,
                zuordnung=zuordnung,
                verwaltbare_rollen=verwaltbare,
            )
            u["passt"] = ergebnis.passt
            u["abgleich"] = rollenabgleich.zusammenfassung(ergebnis, rollennamen)

        # Leute MIT zugeordneter Rolle, aber OHNE Backend-Rechte tauchen in
        # get_all_permissioned_users() gar nicht auf - sie haben ja keinen
        # Eintrag. Genau das ist aber ein Fall, den man sehen will.
        bekannte = {u["discord_user_id"] for u in users}
        zugeordnete_rollen = set(zuordnung.values())
        rollen_ohne_recht = []
        for uid, eigene in sorted(mitglieder_rollen.items()):
            if uid in bekannte:
                continue
            treffer = eigene & zugeordnete_rollen
            if treffer:
                rollen_ohne_recht.append({
                    "discord_user_id": uid,
                    "name": member_names.get(uid, uid),
                    "rollen": sorted((rollennamen.get(r, r) for r in treffer), key=str.lower),
                })

        return render_template(
            "users.html",
            zuordnung_aktiv=bool(zuordnung),
            rollen_ohne_recht=rollen_ohne_recht,
            users=users,
            all_permissions=database.ALL_PERMISSIONS,
            permission_labels=config.PERMISSION_LABELS,
            bot_owner_id=config.BOT_OWNER_ID,
        )

    @app.route("/users/save", methods=["POST"])
    @require_permission("is_admin")
    def users_save():
        user_id = request.form.get("user_id", "").strip()
        if not user_id.isdigit():
            return redirect(url_for("users_page"))
        if config.BOT_OWNER_ID and user_id == config.BOT_OWNER_ID:
            database.log_audit(
                session["discord_user_id"], "owner_protection_triggered",
                target_id=config.BOT_OWNER_ID, detail="Versuch, Owner-Rechte über /users/save zu ändern",
            )
            flash("Der Bot-Owner kann nicht über diese Seite verändert werden.", "error")
            return redirect(url_for("users_page"))
        selected = [p for p in request.form.getlist("permissions") if p in database.ALL_PERMISSIONS]
        database.set_user_permissions(user_id, selected, granted_by=session["discord_user_id"])
        _rollen_nachziehen(user_id, "Rechte geaendert")
        return redirect(url_for("users_page"))

    @app.route("/users/revoke_one", methods=["POST"])
    @require_permission("is_admin")
    def users_revoke_one():
        user_id = request.form.get("user_id", "").strip()
        permission = request.form.get("permission", "").strip()
        if config.BOT_OWNER_ID and user_id == config.BOT_OWNER_ID:
            database.log_audit(
                session["discord_user_id"], "owner_protection_triggered",
                target_id=config.BOT_OWNER_ID, detail=f"Versuch, Owner-Recht '{permission}' über /users/revoke_one zu entziehen",
            )
            flash("Der Bot-Owner kann nicht über diese Seite verändert werden.", "error")
            return redirect(url_for("users_page"))
        if user_id and permission in database.ALL_PERMISSIONS:
            database.revoke_user_permission(user_id, permission, actor_id=session["discord_user_id"])
            _rollen_nachziehen(user_id, f"Recht {permission} entzogen")
        return redirect(url_for("users_page"))

    # --- Audit-Log (nur is_admin/Owner - reine Ansicht, keine Aenderungen moeglich) ---

    @app.route("/audit_log")
    @require_permission("is_admin")
    def audit_log_page():
        per_page = 100
        page = request.args.get("page", 1, type=int)
        total = database.count_audit_log()
        total_pages = max(1, -(-total // per_page))
        page = max(1, min(page, total_pages))
        entries = database.get_audit_log(per_page, (page - 1) * per_page)

        async def _get_member_names():
            if not bot.guilds:
                return {}
            guild = bot.guilds[0]
            return {str(m.id): str(m) for m in guild.members}

        member_names = run_on_bot(_get_member_names())
        for e in entries:
            e["actor_name"] = member_names.get(e["actor_id"], e["actor_id"]) if e["actor_id"] else "—"
            e["target_name"] = member_names.get(e["target_id"], e["target_id"]) if e["target_id"] else None

        return render_template(
            "audit_log.html",
            entries=entries,
            page=page,
            total_pages=total_pages,
            total=total,
        )

    # --- Tunnel-Zugaenge der Android-App (nur is_admin/Owner, rein lesend) ---
    # Grants werden ausschliesslich ueber scripts/manage_bot_access.sh per SSH
    # verwaltet (add/revoke) - bewusste Sicherheitsentscheidung, damit das oeffentlich
    # erreichbare Web-Backend selbst keine systemd-Dienste anlegen/loeschen kann.
    # Diese Seite zeigt nur den aktuellen Stand aus der Datenbank an.

    @app.route("/tunnel_access")
    @require_permission("is_admin")
    def tunnel_access_page():
        grants = database.get_tunnel_access_grants()
        return render_template("tunnel_access.html", grants=grants)

    # --- Personality/Mood (Spec 1, Phase 9) - nutzt die bestehende
    # "manage_game_settings"-Berechtigung mit: das ist bereits die Berechtigung
    # fuer "wie verhaelt sich der Bot fuer den ganzen Server" (Quiz-/XP-/
    # Ankuendigungs-Einstellungen liegen ebenfalls dort) - eine neue, neunte
    # Berechtigung nur fuer diese eine weitere Verhaltens-Kategorie haette das
    # Berechtigungssystem unnoetig aufgeblaeht. ---

    PERSONALITY_SETTING_KEYS = (
        "personality_enabled", "mood_enabled", "mood_update_interval_minutes",
        "mood_night_start_hour", "mood_night_end_hour", "personality_response_frequency",
        "personality_humor_level", "personality_response_mode", "personality_spontaneous_channel_id",
        "personality_spontaneous_min_hours", "dynamic_status_enabled", "status_rotation_minutes",
    )
    # Zufallsereignisse (Phase 10) leben auf derselben Seite/Route wie
    # Personality (gleiche Berechtigung, thematisch eng verwandt - baut direkt
    # auf dem Mood-System auf) statt einer eigenen "/events"-Seite.
    EVENT_SETTING_KEYS = (
        "random_events_enabled", "event_check_interval_minutes", "event_probability_percent",
        "event_channel_id", "event_weight_humor", "event_weight_quiz", "event_weight_xpdrop",
        "event_weight_community", "event_xpdrop_amount", "event_community_bonus_multiplier",
        "event_community_bonus_minutes",
    )
    # Erweiterte Community-Reaktionen (Phase 11: Server-Rekorde, Quiz-Serien,
    # Server-Meilensteine) - eigener Schalter zusaetzlich zu announcements_enabled,
    # siehe announcements._community_reactions_allowed().
    COMMUNITY_REACTION_SETTING_KEYS = (
        "community_reactions_enabled", "streak_announcement_thresholds", "milestone_thresholds",
    )
    # Mood-Erkennungsschwellen (Spec 1, Phase B3) - eigene Unterseite statt im
    # großen Personality-Formular, da es ein klar abgegrenztes, tieferliegendes
    # Thema ist ("wie erkennt der Bot intern seine Stimmung", nicht "wie klingt
    # die Ausgabe").
    MOOD_THRESHOLD_SETTING_KEYS = (
        "mood_activity_window_minutes", "mood_excited_active_user_threshold",
        "mood_bored_inactivity_minutes", "mood_violation_window_minutes",
        "mood_annoyed_violation_threshold",
    )

    @app.route("/personality")
    @require_permission("manage_game_settings")
    def personality_page():
        settings = {
            key: database.get_setting(key, config.DEFAULT_SETTINGS[key]) for key in PERSONALITY_SETTING_KEYS
        }
        event_settings = {
            key: database.get_setting(key, config.DEFAULT_SETTINGS[key]) for key in EVENT_SETTING_KEYS
        }
        community_settings = {
            key: database.get_setting(key, config.DEFAULT_SETTINGS[key]) for key in COMMUNITY_REACTION_SETTING_KEYS
        }
        state = {
            "mood": database.get_bot_state("mood", "—"),
            "energy": database.get_bot_state("energy", "—"),
            "last_mood_change": database.get_bot_state("last_mood_change"),
            "last_status_change": database.get_bot_state("last_status_change"),
            "last_spontaneous_message": database.get_bot_state("last_spontaneous_message"),
            "last_event_check": database.get_bot_state("last_event_check"),
            "quiz_total_correct": modul_quiz.get_total_quiz_correct_count(),
        }
        return render_template(
            "personality.html",
            settings=settings,
            event_settings=event_settings,
            community_settings=community_settings,
            state=state,
            min_spontaneous_hours=mood.MINIMUM_SPONTANEOUS_HOURS,
        )

    @app.route("/personality/settings", methods=["POST"])
    @require_permission("manage_game_settings")
    def personality_save_settings():
        form = request.form
        old_values = {
            key: database.get_setting(key, config.DEFAULT_SETTINGS[key])
            for key in PERSONALITY_SETTING_KEYS + EVENT_SETTING_KEYS + COMMUNITY_REACTION_SETTING_KEYS
        }

        personality_enabled = "1" if form.get("personality_enabled") == "1" else "0"
        mood_enabled = "1" if form.get("mood_enabled") == "1" else "0"
        try:
            mood_update_interval_minutes = max(1, int(form.get("mood_update_interval_minutes", "30")))
        except (TypeError, ValueError):
            mood_update_interval_minutes = 30
        try:
            mood_night_start_hour = max(0, min(23, int(form.get("mood_night_start_hour", "0"))))
        except (TypeError, ValueError):
            mood_night_start_hour = 0
        try:
            mood_night_end_hour = max(0, min(23, int(form.get("mood_night_end_hour", "6"))))
        except (TypeError, ValueError):
            mood_night_end_hour = 6
        try:
            personality_response_frequency = max(0, min(100, int(form.get("personality_response_frequency", "50"))))
        except (TypeError, ValueError):
            personality_response_frequency = 50
        personality_humor_level = form.get("personality_humor_level", "medium")
        if personality_humor_level not in ("low", "medium", "high"):
            personality_humor_level = "medium"
        personality_response_mode = form.get("personality_response_mode", "status_only")
        if personality_response_mode not in ("status_only", "mention_only", "channel_messages"):
            personality_response_mode = "status_only"
        personality_spontaneous_channel_id = form.get("personality_spontaneous_channel_id", "").strip()
        if personality_spontaneous_channel_id and not personality_spontaneous_channel_id.isdigit():
            personality_spontaneous_channel_id = ""
        try:
            _raw_min_hours = float(form.get("personality_spontaneous_min_hours", "12"))
            # "inf"/"nan" sind fuer float() gueltige Eingaben, fuehren aber weiter
            # unten bei int(personality_spontaneous_min_hours) zu einem
            # ungefangenen OverflowError (500er) - Pruefung direkt nach dem
            # float()-Parsing, noch vor dem max()-Clamp (sonst wuerde z.B.
            # max(0.0, nan) je nach Python-Vergleichslogik still zu 0.0 statt
            # zum Default zurueckfallen).
            if not math.isfinite(_raw_min_hours):
                raise ValueError("personality_spontaneous_min_hours nicht endlich")
            personality_spontaneous_min_hours = max(0.0, _raw_min_hours)
        except (TypeError, ValueError):
            personality_spontaneous_min_hours = 12.0
        dynamic_status_enabled = "1" if form.get("dynamic_status_enabled") == "1" else "0"
        try:
            status_rotation_minutes = max(1, int(form.get("status_rotation_minutes", "20")))
        except (TypeError, ValueError):
            status_rotation_minutes = 20

        if personality_spontaneous_min_hours < mood.MINIMUM_SPONTANEOUS_HOURS:
            flash(
                f"Hinweis: {personality_spontaneous_min_hours:g}h liegt unter der serverseitig fest verankerten "
                f"Untergrenze von {mood.MINIMUM_SPONTANEOUS_HOURS:g}h. Der Wert wird gespeichert, aber im Betrieb "
                f"wird trotzdem nie haeufiger als alle {mood.MINIMUM_SPONTANEOUS_HOURS:g}h eine spontane "
                "Nachricht gesendet.",
                "error",
            )

        database.set_setting("personality_enabled", personality_enabled)
        database.set_setting("mood_enabled", mood_enabled)
        database.set_setting("mood_update_interval_minutes", str(mood_update_interval_minutes))
        database.set_setting("mood_night_start_hour", str(mood_night_start_hour))
        database.set_setting("mood_night_end_hour", str(mood_night_end_hour))
        database.set_setting("personality_response_frequency", str(personality_response_frequency))
        database.set_setting("personality_humor_level", personality_humor_level)
        database.set_setting("personality_response_mode", personality_response_mode)
        database.set_setting("personality_spontaneous_channel_id", personality_spontaneous_channel_id)
        # Ganzzahlige Werte ohne unnoetiges ".0" speichern (kosmetisch), Bruch-
        # stunden (z.B. 4.5) bleiben als Dezimalzahl erhalten.
        _min_hours_str = (
            str(int(personality_spontaneous_min_hours))
            if personality_spontaneous_min_hours == int(personality_spontaneous_min_hours)
            else str(personality_spontaneous_min_hours)
        )
        database.set_setting("personality_spontaneous_min_hours", _min_hours_str)
        database.set_setting("dynamic_status_enabled", dynamic_status_enabled)
        database.set_setting("status_rotation_minutes", str(status_rotation_minutes))

        # --- Zufallsereignisse (Phase 10) ---
        random_events_enabled = "1" if form.get("random_events_enabled") == "1" else "0"
        try:
            event_check_interval_minutes = max(1, int(form.get("event_check_interval_minutes", "15")))
        except (TypeError, ValueError):
            event_check_interval_minutes = 15
        try:
            event_probability_percent = max(0, min(100, int(form.get("event_probability_percent", "15"))))
        except (TypeError, ValueError):
            event_probability_percent = 15
        event_channel_id = form.get("event_channel_id", "").strip()
        if event_channel_id and not event_channel_id.isdigit():
            event_channel_id = ""

        def _parse_weight(field_name, default):
            try:
                return max(0, int(form.get(field_name, str(default))))
            except (TypeError, ValueError):
                return default

        event_weight_humor = _parse_weight("event_weight_humor", 40)
        event_weight_quiz = _parse_weight("event_weight_quiz", 25)
        event_weight_xpdrop = _parse_weight("event_weight_xpdrop", 20)
        event_weight_community = _parse_weight("event_weight_community", 15)
        try:
            event_xpdrop_amount = max(0, int(form.get("event_xpdrop_amount", "50")))
        except (TypeError, ValueError):
            event_xpdrop_amount = 50
        try:
            _raw_multiplier = float(form.get("event_community_bonus_multiplier", "2"))
            if not math.isfinite(_raw_multiplier):
                raise ValueError("event_community_bonus_multiplier nicht endlich")
            event_community_bonus_multiplier = max(1.0, _raw_multiplier)
        except (TypeError, ValueError):
            event_community_bonus_multiplier = 2.0
        try:
            event_community_bonus_minutes = max(1, int(form.get("event_community_bonus_minutes", "10")))
        except (TypeError, ValueError):
            event_community_bonus_minutes = 10

        database.set_setting("random_events_enabled", random_events_enabled)
        database.set_setting("event_check_interval_minutes", str(event_check_interval_minutes))
        database.set_setting("event_probability_percent", str(event_probability_percent))
        database.set_setting("event_channel_id", event_channel_id)
        database.set_setting("event_weight_humor", str(event_weight_humor))
        database.set_setting("event_weight_quiz", str(event_weight_quiz))
        database.set_setting("event_weight_xpdrop", str(event_weight_xpdrop))
        database.set_setting("event_weight_community", str(event_weight_community))
        database.set_setting("event_xpdrop_amount", str(event_xpdrop_amount))
        _multiplier_str = (
            str(int(event_community_bonus_multiplier))
            if event_community_bonus_multiplier == int(event_community_bonus_multiplier)
            else str(event_community_bonus_multiplier)
        )
        database.set_setting("event_community_bonus_multiplier", _multiplier_str)
        database.set_setting("event_community_bonus_minutes", str(event_community_bonus_minutes))

        # --- Erweiterte Community-Reaktionen (Phase 11) ---
        community_reactions_enabled = "1" if form.get("community_reactions_enabled") == "1" else "0"
        streak_thresholds_raw = form.get("streak_announcement_thresholds", "5,10,20,50")
        streak_thresholds_parsed = database.parse_csv_ints(streak_thresholds_raw)
        streak_announcement_thresholds = ",".join(str(v) for v in streak_thresholds_parsed) or "5,10,20,50"
        milestone_thresholds_raw = form.get("milestone_thresholds", "100,500,1000,5000,10000")
        milestone_thresholds_parsed = database.parse_csv_ints(milestone_thresholds_raw)
        milestone_thresholds = ",".join(str(v) for v in milestone_thresholds_parsed) or "100,500,1000,5000,10000"

        database.set_setting("community_reactions_enabled", community_reactions_enabled)
        database.set_setting("streak_announcement_thresholds", streak_announcement_thresholds)
        database.set_setting("milestone_thresholds", milestone_thresholds)

        new_values = {
            key: database.get_setting(key, config.DEFAULT_SETTINGS[key])
            for key in PERSONALITY_SETTING_KEYS + EVENT_SETTING_KEYS + COMMUNITY_REACTION_SETTING_KEYS
        }
        _log_settings_change("config_change", old_values, new_values)
        return redirect(url_for("personality_page"))

    # --- Mood-Erkennungsschwellen (Spec 1, Phase B3, eigene Unterseite) ---

    @app.route("/personality/thresholds")
    @require_permission("manage_game_settings")
    def personality_thresholds_page():
        settings = {
            key: database.get_setting(key, config.DEFAULT_SETTINGS[key]) for key in MOOD_THRESHOLD_SETTING_KEYS
        }
        return render_template("personality_thresholds.html", settings=settings)

    @app.route("/personality/thresholds/settings", methods=["POST"])
    @require_permission("manage_game_settings")
    def personality_thresholds_save():
        form = request.form
        old_values = {
            key: database.get_setting(key, config.DEFAULT_SETTINGS[key]) for key in MOOD_THRESHOLD_SETTING_KEYS
        }
        try:
            activity_window = max(1, int(form.get("mood_activity_window_minutes", "15")))
        except (TypeError, ValueError):
            activity_window = 15
        try:
            excited_threshold = max(1, int(form.get("mood_excited_active_user_threshold", "5")))
        except (TypeError, ValueError):
            excited_threshold = 5
        try:
            bored_minutes = max(1, int(form.get("mood_bored_inactivity_minutes", "30")))
        except (TypeError, ValueError):
            bored_minutes = 30
        try:
            violation_window = max(1, int(form.get("mood_violation_window_minutes", "60")))
        except (TypeError, ValueError):
            violation_window = 60
        try:
            annoyed_threshold = max(1, int(form.get("mood_annoyed_violation_threshold", "3")))
        except (TypeError, ValueError):
            annoyed_threshold = 3

        database.set_setting("mood_activity_window_minutes", str(activity_window))
        database.set_setting("mood_excited_active_user_threshold", str(excited_threshold))
        database.set_setting("mood_bored_inactivity_minutes", str(bored_minutes))
        database.set_setting("mood_violation_window_minutes", str(violation_window))
        database.set_setting("mood_annoyed_violation_threshold", str(annoyed_threshold))
        _log_settings_change("config_change", old_values, {
            "mood_activity_window_minutes": str(activity_window),
            "mood_excited_active_user_threshold": str(excited_threshold),
            "mood_bored_inactivity_minutes": str(bored_minutes),
            "mood_violation_window_minutes": str(violation_window),
            "mood_annoyed_violation_threshold": str(annoyed_threshold),
        })
        return redirect(url_for("personality_thresholds_page"))

    # --- Text-Varianten-Pools bearbeiten (Spec 1, Phase B3, eigene Unterseite) ---
    # Ersetzt die zuvor hartcodierten Pools in bot/mood.py durch admin-editierbare
    # Zeilen in der personality_texts-Tabelle (siehe database.get_personality_text_pool()
    # und Konsorten). mood.TEXT_POOL_INFO ist die eine Quelle der Wahrheit dafür,
    # welche Pools es gibt und ob sie Mood-/Tier-Dimensionen haben - eine neue
    # Pool-Art braucht nur einen weiteren Eintrag dort, keine Änderung hier.

    def _valid_pool_or_none(pool_name):
        return mood.TEXT_POOL_INFO.get(pool_name)

    @app.route("/personality/texts")
    @require_permission("manage_game_settings")
    def personality_texts_page():
        pools = {
            name: {
                "info": info,
                "entries": database.get_personality_texts_admin(name),
            }
            for name, info in mood.TEXT_POOL_INFO.items()
        }
        return render_template(
            "personality_texts.html",
            pools=pools,
            moods=mood.MOODS,
            humor_tiers=("low", "medium", "high"),
        )

    @app.route("/personality/texts/add", methods=["POST"])
    @require_permission("manage_game_settings")
    def personality_texts_add():
        form = request.form
        pool_name = form.get("pool", "")
        info = _valid_pool_or_none(pool_name)
        if not info:
            flash("Ungültiger Text-Pool.", "error")
            return redirect(url_for("personality_texts_page"))

        mood_key = form.get("mood", "").strip() if info["has_mood"] else None
        if info["has_mood"] and mood_key not in mood.MOODS:
            flash("Ungültiger Mood-Wert.", "error")
            return redirect(url_for("personality_texts_page"))
        tier = form.get("humor_tier", "").strip() if info["has_tier"] else None
        if info["has_tier"] and tier not in ("low", "medium", "high"):
            flash("Ungültige Humor-Stufe.", "error")
            return redirect(url_for("personality_texts_page"))
        text = (form.get("text") or "").strip()
        if not text:
            flash("Text darf nicht leer sein.", "error")
            return redirect(url_for("personality_texts_page"))

        new_id = database.add_personality_text(pool_name, mood_key, tier, text)
        database.log_audit(
            session["discord_user_id"], "personality_text_add", target_id=new_id,
            detail=f"pool={pool_name} mood={mood_key} tier={tier} text={text!r}",
        )
        return redirect(url_for("personality_texts_page"))

    @app.route("/personality/texts/update/<int:text_id>", methods=["POST"])
    @require_permission("manage_game_settings")
    def personality_texts_update(text_id):
        form = request.form
        pool_name = form.get("pool", "")
        info = _valid_pool_or_none(pool_name)
        if not info:
            flash("Ungültiger Text-Pool.", "error")
            return redirect(url_for("personality_texts_page"))

        mood_key = form.get("mood", "").strip() if info["has_mood"] else None
        if info["has_mood"] and mood_key not in mood.MOODS:
            flash("Ungültiger Mood-Wert.", "error")
            return redirect(url_for("personality_texts_page"))
        tier = form.get("humor_tier", "").strip() if info["has_tier"] else None
        if info["has_tier"] and tier not in ("low", "medium", "high"):
            flash("Ungültige Humor-Stufe.", "error")
            return redirect(url_for("personality_texts_page"))
        text = (form.get("text") or "").strip()
        if not text:
            flash("Text darf nicht leer sein.", "error")
            return redirect(url_for("personality_texts_page"))

        database.update_personality_text(text_id, mood_key, tier, text)
        database.log_audit(
            session["discord_user_id"], "personality_text_update", target_id=text_id,
            detail=f"pool={pool_name} mood={mood_key} tier={tier} text={text!r}",
        )
        return redirect(url_for("personality_texts_page"))

    @app.route("/personality/texts/delete/<int:text_id>", methods=["POST"])
    @require_permission("manage_game_settings")
    def personality_texts_delete(text_id):
        database.delete_personality_text(text_id)
        database.log_audit(session["discord_user_id"], "personality_text_delete", target_id=text_id)
        return redirect(url_for("personality_texts_page"))

    # --- Moderationsaktionen + Überwachungs-Übersicht (Spec 1, Phase B4) ---
    # Eigenes Flag "moderate_members" statt is_admin wiederzuverwenden (konsistent
    # mit dem granularen Flag-Modell) - is_admin impliziert es wie jedes andere
    # Flag automatisch (siehe database.has_permission()).
    #
    # HARTE REGEL, exakt wie der bestehende Owner-Schutz bei der Rechtevergabe
    # (Phase 1): der Bot-Owner darf NIEMALS Ziel von Kick/Ban/Timeout sein. Die
    # Prüfung läuft über database.is_protected_owner() - dieselbe zentrale
    # Funktion, die auch /users/save und /users/revoke_one verwenden - und wird
    # HIER im Backend geprüft, BEVOR überhaupt ein Discord-API-Aufruf versucht
    # wird (die Discord-API selbst kennt kein Konzept von "Bot-Owner").

    def _member_action_guard(user_id) -> bool:
        """True (und loggt + flasht), wenn eine Moderationsaktion gegen diese ID
        verhindert werden soll, weil sie der Bot-Owner ist."""
        if database.is_protected_owner(user_id):
            database.log_audit(
                session["discord_user_id"], "owner_protection_triggered", target_id=user_id,
                detail="Versuch, eine Moderationsaktion (Kick/Ban/Timeout) gegen den Bot-Owner auszuführen",
            )
            flash("Der Bot-Owner kann nicht Ziel einer Moderationsaktion sein.", "error")
            return True
        if _hierarchy_block(user_id):
            return True
        return False

    def _hierarchy_block(user_id) -> bool:
        """Prueft VOR der Aktion, ob Discord sie ueberhaupt zulassen wuerde, und
        flasht sonst eine Begruendung im Klartext. True = Aktion abbrechen.

        Hintergrund: Discords Rollenhierarchie ist eine eigene Schranke, die
        UNABHAENGIG von den Berechtigungen wirkt. Laut offizieller Dokumentation
        gilt: "A bot can only kick, ban, and edit nicknames for users whose
        highest role is lower than the bot's highest role." Selbst ADMINISTRATOR
        hebt das nicht auf.

        Ohne diese Pruefung liefert Discord nur ein generisches "Missing
        Permissions" (Fehlercode 50013), was die Oberflaeche als "Mir fehlt die
        Berechtigung" anzeigte. Wer daraufhin die Rechte kontrolliert, findet dort
        alles Noetige und sucht an der falschen Stelle weiter - genau das ist real
        passiert. Deshalb hier eine Meldung, die den Grund benennt und sagt, was
        zu tun ist.
        """
        async def _check():
            guild = bot.guilds[0]
            target = guild.get_member(int(user_id))
            if target is None:
                return None  # kein Mitglied (mehr) - der Aufrufer meldet das selbst
            me = guild.me
            return moderation_block_reason(
                is_guild_owner=target.id == guild.owner_id,
                target_role_name=target.top_role.name,
                target_role_position=target.top_role.position,
                bot_role_name=me.top_role.name,
                bot_role_position=me.top_role.position,
            )

        try:
            grund = run_on_bot(_check())
        except Exception:
            log.exception("Hierarchie-Pruefung fehlgeschlagen (user_id=%s)", user_id)
            return False  # im Zweifel die Aktion versuchen lassen
        if grund:
            flash(grund, "error")
            return True
        return False

    @app.route("/members")
    @require_permission("moderate_members")
    def members_overview():
        async def _get_members():
            if not bot.guilds:
                return []
            guild = bot.guilds[0]
            return [
                {
                    "id": str(m.id),
                    "username": str(m),
                    "display_name": m.display_name,
                    "avatar": str(m.display_avatar.url),
                    "joined_at": m.joined_at.isoformat() if m.joined_at else None,
                    "timed_out_until": m.timed_out_until.isoformat() if m.timed_out_until else None,
                    "is_owner": database.is_protected_owner(m.id),
                }
                for m in guild.members if not m.bot
            ]

        members = run_on_bot(_get_members())
        for m in members:
            m.update(database.get_user_overview(m["id"]))
            m["violation_count"] = database.count_user_violations_total(m["id"])
        # Auffälligste zuerst (meiste Verstöße), erleichtert die eigentliche
        # Überwachungs-Aufgabe ("wer braucht gerade Aufmerksamkeit").
        members.sort(key=lambda m: m["violation_count"], reverse=True)

        return render_template("members.html", members=members)

    # Fuer das Bann-Formular auf der Mitgliederseite.
    @app.context_processor
    def inject_ban_zeitraeume():
        return {"ban_loeschzeitraeume": BAN_LOESCHZEITRAEUME}

    @app.route("/members/<user_id>")
    @require_permission("moderate_members")
    def member_detail(user_id):
        if not user_id.isdigit():
            return render_template("no_access.html"), 404

        async def _get_member():
            if bot.guilds:
                guild = bot.guilds[0]
                m = guild.get_member(int(user_id))
                if m:
                    return {
                        "id": str(m.id), "username": str(m), "display_name": m.display_name,
                        "avatar": str(m.display_avatar.url),
                        "joined_at": m.joined_at.isoformat() if m.joined_at else None,
                        "roles": [r.name for r in m.roles if r.name != "@everyone"],
                        "timed_out_until": m.timed_out_until.isoformat() if m.timed_out_until else None,
                        "in_guild": True,
                    }
            # Kein aktuelles Mitglied (mehr) - trotzdem Basis-Infos versuchen,
            # z.B. damit ein Unban auch für bereits verlassene/gebannte User
            # möglich bleibt.
            try:
                u = await bot.fetch_user(int(user_id))
            except discord.NotFound:
                return None
            return {
                "id": str(u.id), "username": str(u), "display_name": str(u),
                "avatar": str(u.display_avatar.url), "joined_at": None,
                "roles": [], "timed_out_until": None, "in_guild": False,
            }

        member = run_on_bot(_get_member())
        if not member:
            flash("Kein Discord-Nutzer mit dieser ID gefunden.", "error")
            return redirect(url_for("members_overview"))

        return render_template(
            "member_detail.html",
            member=member,
            overview=database.get_user_overview(user_id),
            violations=database.get_user_violations(user_id, limit=50),
            is_owner=database.is_protected_owner(user_id),
        )

    @app.route("/members/<user_id>/kick", methods=["POST"])
    @require_permission("moderate_members")
    def member_kick(user_id):
        if not user_id.isdigit():
            return redirect(url_for("members_overview"))
        if _member_action_guard(user_id):
            return redirect(url_for("member_detail", user_id=user_id))
        reason_voll = (request.form.get("reason") or "").strip() or "Kein Grund angegeben"
        reason, gekuerzt = _kuerze_audit_grund(reason_voll)
        if gekuerzt:
            flash(
                "Der Grund war für Discords Audit-Log zu lang und wurde gekürzt "
                f"(Grenze: {AUDIT_REASON_MAX} URL-kodierte Zeichen, Umlaute zählen sechsfach). "
                "Im Audit-Log des Bots steht der vollständige Text.",
                "warning",
            )

        async def _kick():
            guild = bot.guilds[0]
            member = guild.get_member(int(user_id))
            if not member:
                raise ValueError("Nutzer ist kein Mitglied dieses Servers (mehr).")
            await guild.kick(member, reason=reason)

        try:
            run_on_bot(_kick())
        except discord.Forbidden:
            flash("Discord hat den Kick abgelehnt. Die Person trägt eine Rolle, die nicht unter der Bot-Rolle liegt. Wer über der Bot-Rolle steht, ist bewusst vor Aktionen des Bots geschützt. Berechtigungen ändern daran nichts, auch Administrator nicht. Gegen diese Person kann nur ein Administrator direkt in Discord vorgehen.", "error")
        except Exception as exc:
            log.exception("Kick fehlgeschlagen (user_id=%s)", user_id)
            flash(f"Kick fehlgeschlagen: {exc}", "error")
        else:
            database.log_audit(session["discord_user_id"], "member_kick", target_id=user_id, detail=f"Grund: {reason_voll}")
            flash("Nutzer wurde gekickt.", "success")
        return redirect(url_for("members_overview"))

    @app.route("/members/<user_id>/ban", methods=["POST"])
    @require_permission("moderate_members")
    def member_ban(user_id):
        if not user_id.isdigit():
            return redirect(url_for("members_overview"))
        if _member_action_guard(user_id):
            return redirect(url_for("member_detail", user_id=user_id))
        reason_voll = (request.form.get("reason") or "").strip() or "Kein Grund angegeben"
        reason, gekuerzt = _kuerze_audit_grund(reason_voll)
        if gekuerzt:
            flash(
                "Der Grund war für Discords Audit-Log zu lang und wurde gekürzt "
                f"(Grenze: {AUDIT_REASON_MAX} URL-kodierte Zeichen, Umlaute zählen sechsfach). "
                "Im Audit-Log des Bots steht der vollständige Text.",
                "warning",
            )

        # Gegen die feste Liste pruefen, nicht uebernehmen. Unbekannter Wert ->
        # 0, also der sicherste Fall.
        loeschen_key = (request.form.get("delete_messages") or "0").strip()
        _, loesch_sekunden = BAN_LOESCHZEITRAEUME.get(loeschen_key, BAN_LOESCHZEITRAEUME["0"])

        async def _ban():
            guild = bot.guilds[0]
            await guild.ban(
                discord.Object(id=int(user_id)), reason=reason,
                delete_message_seconds=loesch_sekunden,
            )

        try:
            run_on_bot(_ban())
        except discord.Forbidden:
            flash("Discord hat den Bann abgelehnt. Die Person trägt eine Rolle, die nicht unter der Bot-Rolle liegt. Wer über der Bot-Rolle steht, ist bewusst vor Aktionen des Bots geschützt. Berechtigungen ändern daran nichts, auch Administrator nicht. Gegen diese Person kann nur ein Administrator direkt in Discord vorgehen.", "error")
        except Exception as exc:
            log.exception("Ban fehlgeschlagen (user_id=%s)", user_id)
            flash(f"Ban fehlgeschlagen: {exc}", "error")
        else:
            loesch_label = BAN_LOESCHZEITRAEUME.get(loeschen_key, BAN_LOESCHZEITRAEUME["0"])[0]
            # Der geloeschte Zeitraum gehoert ins Audit-Log: Er ist nicht
            # rueckgaengig zu machen, also muss spaeter nachvollziehbar sein,
            # was verschwunden ist.
            database.log_audit(
                session["discord_user_id"], "member_ban", target_id=user_id,
                detail=f"Grund: {reason_voll} | Nachrichten gelöscht: {loesch_label}",
            )
            if loesch_sekunden:
                flash(f"Nutzer wurde gebannt. Nachrichten gelöscht: {loesch_label.lower()}.", "success")
            else:
                flash("Nutzer wurde gebannt.", "success")
        return redirect(url_for("members_overview"))

    @app.route("/members/<user_id>/unban", methods=["POST"])
    @require_permission("moderate_members")
    def member_unban(user_id):
        if not user_id.isdigit():
            return redirect(url_for("members_overview"))
        if _member_action_guard(user_id):
            return redirect(url_for("member_detail", user_id=user_id))

        async def _unban():
            guild = bot.guilds[0]
            await guild.unban(discord.Object(id=int(user_id)), reason="Über Backend entbannt")

        try:
            run_on_bot(_unban())
        except discord.NotFound:
            flash("Dieser Nutzer ist nicht gebannt.", "error")
        except discord.Forbidden:
            flash("Mir fehlt die Berechtigung, diesen Nutzer zu entbannen.", "error")
        except Exception as exc:
            log.exception("Unban fehlgeschlagen (user_id=%s)", user_id)
            flash(f"Unban fehlgeschlagen: {exc}", "error")
        else:
            database.log_audit(session["discord_user_id"], "member_unban", target_id=user_id)
            flash("Nutzer wurde entbannt.", "success")
        return redirect(url_for("member_detail", user_id=user_id))

    @app.route("/members/<user_id>/timeout", methods=["POST"])
    @require_permission("moderate_members")
    def member_timeout(user_id):
        if not user_id.isdigit():
            return redirect(url_for("members_overview"))
        if _member_action_guard(user_id):
            return redirect(url_for("member_detail", user_id=user_id))
        reason_voll = (request.form.get("reason") or "").strip() or "Kein Grund angegeben"
        reason, gekuerzt = _kuerze_audit_grund(reason_voll)
        if gekuerzt:
            flash(
                "Der Grund war für Discords Audit-Log zu lang und wurde gekürzt "
                f"(Grenze: {AUDIT_REASON_MAX} URL-kodierte Zeichen, Umlaute zählen sechsfach). "
                "Im Audit-Log des Bots steht der vollständige Text.",
                "warning",
            )
        try:
            duration_minutes = max(1, min(40320, int(request.form.get("duration_minutes", "10"))))
        except (TypeError, ValueError):
            duration_minutes = 10

        async def _timeout():
            guild = bot.guilds[0]
            member = guild.get_member(int(user_id))
            if not member:
                raise ValueError("Nutzer ist kein Mitglied dieses Servers (mehr).")
            await member.timeout(timedelta(minutes=duration_minutes), reason=reason)

        try:
            run_on_bot(_timeout())
        except discord.Forbidden:
            flash("Discord hat den Timeout abgelehnt. Die Person trägt eine Rolle, die nicht unter der Bot-Rolle liegt. Wer über der Bot-Rolle steht, ist bewusst vor Aktionen des Bots geschützt. Berechtigungen ändern daran nichts, auch Administrator nicht. Gegen diese Person kann nur ein Administrator direkt in Discord vorgehen.", "error")
        except Exception as exc:
            log.exception("Timeout fehlgeschlagen (user_id=%s)", user_id)
            flash(f"Timeout fehlgeschlagen: {exc}", "error")
        else:
            database.log_audit(
                session["discord_user_id"], "member_timeout", target_id=user_id,
                detail=f"{duration_minutes} Minuten, Grund: {reason_voll}",
            )
            flash(f"Nutzer wurde für {duration_minutes} Minuten stummgeschaltet.", "success")
        return redirect(url_for("member_detail", user_id=user_id))

    # --- Server-Setup (Menü-Reorganisation): Willkommensnachricht + Regel-Rolle,
    # vorher faelschlich unter Automod (/moderation) einsortiert - inhaltlich
    # Server-Onboarding, nicht Automod. Giveaway-Formular bleibt technisch unter
    # /giveaway (eigenes Template mit eigener JS-Kanalliste), wird nur zusaetzlich
    # ueber das Server-Setup-Dropdown in der Navbar verlinkt (geringstes Risiko:
    # keine bestehende, funktionierende Seite anfassen muessen). ---

    def _welcome_settings():
        return {
            "welcome_message_enabled": database.get_setting(
                "welcome_message_enabled", config.DEFAULT_SETTINGS["welcome_message_enabled"]
            ),
            "welcome_channel_id": database.get_setting(
                "welcome_channel_id", config.DEFAULT_SETTINGS["welcome_channel_id"]
            ),
        }

    def _morning_settings():
        """Taegliche Guten-Morgen-Nachricht (03.09.2026). Die TEXTE stehen nicht
        hier, sondern im Textpool "morning" unter Personality -> Texte - hier nur
        Ob, Wann und Wohin."""
        return {
            "morning_greeting_enabled": database.get_setting(
                "morning_greeting_enabled", config.DEFAULT_SETTINGS["morning_greeting_enabled"]
            ),
            "morning_greeting_time": database.get_setting(
                "morning_greeting_time", config.DEFAULT_SETTINGS["morning_greeting_time"]
            ),
            "morning_greeting_channel_id": database.get_setting(
                "morning_greeting_channel_id", config.DEFAULT_SETTINGS["morning_greeting_channel_id"]
            ),
            "morning_greeting_grace_minutes": database.get_setting(
                "morning_greeting_grace_minutes",
                config.DEFAULT_SETTINGS["morning_greeting_grace_minutes"],
            ),
        }

    @app.route("/server-setup")
    @require_permission("manage_badword_filter")
    def server_setup_page():
        return render_template(
            "server_setup.html",
            welcome_settings=_welcome_settings(),
            morning_settings=_morning_settings(),
        )

    @app.route("/server-setup/welcome_settings", methods=["POST"])
    @require_permission("manage_badword_filter")
    def server_setup_welcome_settings():
        form = request.form
        old_values = _welcome_settings()
        welcome_message_enabled = "1" if form.get("welcome_message_enabled") == "1" else "0"
        welcome_channel_id = form.get("welcome_channel_id", "").strip()
        if welcome_channel_id and not welcome_channel_id.isdigit():
            welcome_channel_id = ""

        database.set_setting("welcome_message_enabled", welcome_message_enabled)
        database.set_setting("welcome_channel_id", welcome_channel_id)
        _log_settings_change("config_change", old_values, {
            "welcome_message_enabled": welcome_message_enabled,
            "welcome_channel_id": welcome_channel_id,
        })
        return redirect(url_for("server_setup_page"))

    @app.route("/server-setup/morning_settings", methods=["POST"])
    @require_permission("manage_badword_filter")
    def server_setup_morning_settings():
        form = request.form
        old_values = _morning_settings()
        enabled = "1" if form.get("morning_greeting_enabled") == "1" else "0"

        # Uhrzeit gegen dieselbe Funktion pruefen, die auch der Bot benutzt -
        # sonst nimmt das Formular etwas an, das der Bot spaeter stillschweigend
        # verwirft, und der Gruss bleibt ohne erkennbaren Grund aus.
        zeit_roh = (form.get("morning_greeting_time") or "").strip()
        if scheduling.parse_uhrzeit(zeit_roh) is None:
            flash("Ungültige Uhrzeit. Erwartet wird HH:MM, zum Beispiel 07:00.", "error")
            return redirect(url_for("server_setup_page"))
        stunde, minute = scheduling.parse_uhrzeit(zeit_roh)
        zeit = f"{stunde:02d}:{minute:02d}"

        channel_id = form.get("morning_greeting_channel_id", "").strip()
        if channel_id and not channel_id.isdigit():
            channel_id = ""

        try:
            nachlauf = max(0, min(720, int(form.get("morning_greeting_grace_minutes", "60"))))
        except (TypeError, ValueError):
            nachlauf = 60

        if enabled == "1" and not channel_id:
            flash("Ohne Kanal kann der Bot nirgendwo grüßen - bitte einen Kanal auswählen.", "error")
            return redirect(url_for("server_setup_page"))

        database.set_setting("morning_greeting_enabled", enabled)
        database.set_setting("morning_greeting_time", zeit)
        database.set_setting("morning_greeting_channel_id", channel_id)
        database.set_setting("morning_greeting_grace_minutes", str(nachlauf))
        _log_settings_change("config_change", old_values, {
            "morning_greeting_enabled": enabled,
            "morning_greeting_time": zeit,
            "morning_greeting_channel_id": channel_id,
            "morning_greeting_grace_minutes": str(nachlauf),
        })
        flash("Guten-Morgen-Nachricht gespeichert.", "success")
        return redirect(url_for("server_setup_page"))

    return app
