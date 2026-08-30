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
from datetime import datetime, timedelta

import discord
from flask import Flask, flash, redirect, render_template, request, session, url_for, jsonify
from markupsafe import escape
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import database
import mood
from . import oauth

log = logging.getLogger(__name__)


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
        return redirect(url_for("dashboard"))

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # --- Dashboard ---

    @app.route("/dashboard")
    @require_guild_member
    def dashboard():
        stats = database.get_question_stats()
        xp_progress = database.get_user_xp(session["discord_user_id"])
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
        total_players = database.count_players()
        total_pages = max(1, -(-total_players // per_page))
        page = max(1, min(page, total_pages))
        leaderboard_rows = database.get_leaderboard_page(per_page, (page - 1) * per_page)

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
        user_questions = database.get_questions_by_user(session["discord_user_id"])
        return render_template("dbot.html", user_questions=user_questions, categories=database.get_categories())

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

        question_id = database.submit_question(
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
                    database.delete_question(int(key.split("_")[-1]))
                elif key.startswith("edit_question_"):
                    qid = int(key.split("_")[-1])
                    category_raw = form.get(f"category_{qid}")
                    database.update_question(
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

        unverified = database.get_questions(False, per_page, (page_unverified - 1) * per_page)
        verified = database.get_questions(True, per_page, (page_verified - 1) * per_page)

        total_unverified = database.count_questions(False)
        total_verified = database.count_questions(True)

        return render_template(
            "verify_questions.html",
            unverified_questions=unverified,
            verified_questions=verified,
            page_unverified=page_unverified,
            page_verified=page_verified,
            total_unverified_pages=max(1, -(-total_unverified // per_page)),
            total_verified_pages=max(1, -(-total_verified // per_page)),
            categories=database.get_categories(),
        )

    # --- Kategorien verwalten (manage_categories) ---

    @app.route("/categories")
    @require_permission("manage_categories")
    def categories_page():
        return render_template(
            "categories.html",
            categories=database.get_categories(),
            question_counts=database.count_questions_by_category(),
        )

    @app.route("/categories/add", methods=["POST"])
    @require_permission("manage_categories")
    def categories_add():
        name = request.form.get("name", "").strip()
        emoji = request.form.get("emoji", "").strip() or None
        if name:
            database.add_category(name, emoji)
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
                    database.update_category(category_id, name, emoji)
        return redirect(url_for("categories_page"))

    @app.route("/categories/delete/<int:category_id>", methods=["POST"])
    @require_permission("manage_categories")
    def categories_delete(category_id):
        database.delete_category(category_id)
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
        required = ["channel_id", "title", "description", "color"]
        if not all(k in data for k in required):
            return jsonify({"error": "Fehlende Felder"}), 400

        async def _post():
            channel = bot.get_channel(int(data["channel_id"]))
            if not channel:
                raise ValueError("Kanal nicht gefunden")
            embed = discord.Embed(
                title=data["title"],
                description=data["description"],
                color=int(data["color"].lstrip("#"), 16),
            )
            for field in data.get("fields", []):
                if field.get("name") and field.get("value"):
                    embed.add_field(name=field["name"], value=field["value"], inline=field.get("inline", False))
            if data.get("thumbnail"):
                embed.set_thumbnail(url=data["thumbnail"])
            if data.get("banner"):
                embed.set_image(url=data["banner"])
            if data.get("footer"):
                embed.set_footer(text=data["footer"])
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
        if not user_id or not message:
            return jsonify({"error": "Nutzer-ID und Nachricht sind erforderlich."}), 400
        if not user_id.isdigit():
            return jsonify({"error": "Nutzer-ID muss die numerische Discord-User-ID sein."}), 400

        async def _send():
            user = await bot.fetch_user(int(user_id))
            await user.send(message)
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

    @app.route("/users")
    @require_permission("is_admin")
    def users_page():
        async def _get_member_names():
            if not bot.guilds:
                return {}
            guild = bot.guilds[0]
            return {str(m.id): str(m) for m in guild.members}

        member_names = run_on_bot(_get_member_names())

        users = database.get_all_permissioned_users()
        for u in users:
            u["name"] = member_names.get(u["discord_user_id"], u["discord_user_id"])

        return render_template(
            "users.html",
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
            "quiz_total_correct": database.get_total_quiz_correct_count(),
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
        reason = (request.form.get("reason") or "").strip() or "Kein Grund angegeben"

        async def _kick():
            guild = bot.guilds[0]
            member = guild.get_member(int(user_id))
            if not member:
                raise ValueError("Nutzer ist kein Mitglied dieses Servers (mehr).")
            await guild.kick(member, reason=reason)

        try:
            run_on_bot(_kick())
        except discord.Forbidden:
            flash("Mir fehlt die Berechtigung, diesen Nutzer zu kicken.", "error")
        except Exception as exc:
            log.exception("Kick fehlgeschlagen (user_id=%s)", user_id)
            flash(f"Kick fehlgeschlagen: {exc}", "error")
        else:
            database.log_audit(session["discord_user_id"], "member_kick", target_id=user_id, detail=f"Grund: {reason}")
            flash("Nutzer wurde gekickt.", "success")
        return redirect(url_for("members_overview"))

    @app.route("/members/<user_id>/ban", methods=["POST"])
    @require_permission("moderate_members")
    def member_ban(user_id):
        if not user_id.isdigit():
            return redirect(url_for("members_overview"))
        if _member_action_guard(user_id):
            return redirect(url_for("member_detail", user_id=user_id))
        reason = (request.form.get("reason") or "").strip() or "Kein Grund angegeben"

        async def _ban():
            guild = bot.guilds[0]
            await guild.ban(discord.Object(id=int(user_id)), reason=reason, delete_message_seconds=0)

        try:
            run_on_bot(_ban())
        except discord.Forbidden:
            flash("Mir fehlt die Berechtigung, diesen Nutzer zu bannen.", "error")
        except Exception as exc:
            log.exception("Ban fehlgeschlagen (user_id=%s)", user_id)
            flash(f"Ban fehlgeschlagen: {exc}", "error")
        else:
            database.log_audit(session["discord_user_id"], "member_ban", target_id=user_id, detail=f"Grund: {reason}")
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
        reason = (request.form.get("reason") or "").strip() or "Kein Grund angegeben"
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
            flash("Mir fehlt die Berechtigung, diesen Nutzer zu timeouten.", "error")
        except Exception as exc:
            log.exception("Timeout fehlgeschlagen (user_id=%s)", user_id)
            flash(f"Timeout fehlgeschlagen: {exc}", "error")
        else:
            database.log_audit(
                session["discord_user_id"], "member_timeout", target_id=user_id,
                detail=f"{duration_minutes} Minuten, Grund: {reason}",
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

    def _role_settings():
        return {
            "role_reaction_enabled": database.get_setting(
                "role_reaction_enabled", config.DEFAULT_SETTINGS["role_reaction_enabled"]
            ),
            "member_role_name": database.get_setting(
                "member_role_name", config.DEFAULT_SETTINGS["member_role_name"]
            ),
            "rules_channel_name": database.get_setting(
                "rules_channel_name", config.DEFAULT_SETTINGS["rules_channel_name"]
            ),
            "rules_message_id": database.get_setting(
                "rules_message_id", config.DEFAULT_SETTINGS["rules_message_id"]
            ),
        }

    @app.route("/server-setup")
    @require_permission("manage_badword_filter")
    def server_setup_page():
        return render_template(
            "server_setup.html",
            welcome_settings=_welcome_settings(),
            role_settings=_role_settings(),
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

    @app.route("/server-setup/role_settings", methods=["POST"])
    @require_permission("manage_badword_filter")
    def server_setup_role_settings():
        form = request.form
        old_values = _role_settings()
        role_reaction_enabled = "1" if form.get("role_reaction_enabled") == "1" else "0"
        member_role_name = (form.get("member_role_name") or "").strip() or config.DEFAULT_SETTINGS["member_role_name"]
        rules_channel_name = (form.get("rules_channel_name") or "").strip() or config.DEFAULT_SETTINGS["rules_channel_name"]
        rules_message_id = (form.get("rules_message_id") or "").strip()
        if rules_message_id and not rules_message_id.isdigit():
            rules_message_id = old_values["rules_message_id"]

        database.set_setting("role_reaction_enabled", role_reaction_enabled)
        database.set_setting("member_role_name", member_role_name)
        database.set_setting("rules_channel_name", rules_channel_name)
        database.set_setting("rules_message_id", rules_message_id)
        _log_settings_change("config_change", old_values, {
            "role_reaction_enabled": role_reaction_enabled,
            "member_role_name": member_role_name,
            "rules_channel_name": rules_channel_name,
            "rules_message_id": rules_message_id,
        })
        return redirect(url_for("server_setup_page"))

    return app
