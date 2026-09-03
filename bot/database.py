"""Zentrale Datenbank-Schicht für Bot und Backend (gemeinsam genutzte SQLite-Datei)."""
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import achievements
import config
import module
import schema_helfer
import mood

DB_PATH = Path(__file__).parent / "data" / "bot.db"

SCHEMA = """



-- Verdiente Rollen (03.09.2026): Wer eine Bedingung erfuellt, bekommt die Rolle
-- angeboten. Anders als bei den Reaktionsrollen ist der Ausloeser kein Klick,
-- sondern eine Zahl, die der Bot ohnehin fuehrt (richtige Quizantworten, Level,
-- XP, eingereichte Fragen, Tage auf dem Server).
CREATE TABLE IF NOT EXISTS earned_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id TEXT NOT NULL,
    bedingung_art TEXT NOT NULL,
    schwelle INTEGER NOT NULL,
    -- Text der Ankuendigung, mit Platzhaltern. Leer = Standardtext.
    ankuendigung TEXT,
    -- Kanal fuer die Ankuendigung. Leer = per Direktnachricht.
    channel_id TEXT,
    -- 1 = sofort vergeben, 0 = anbieten und auf den Abhol-Knopf warten.
    automatisch INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Wem wurde welche verdiente Rolle schon angeboten bzw. gegeben. Ohne das
-- wuerde der Bot bei jedem Ausloeser erneut ankuendigen - derselbe Fehler, den
-- der Tagesmerker beim Morgengruss verhindert.
CREATE TABLE IF NOT EXISTS earned_role_grants (
    rule_id INTEGER NOT NULL,
    discord_user_id TEXT NOT NULL,
    angekuendigt_at TEXT,
    vergeben_at TEXT,
    PRIMARY KEY (rule_id, discord_user_id)
);

-- Reaktionsrollen (03.09.2026): Wer auf eine bestimmte Nachricht mit einem
-- bestimmten Emoji reagiert, bekommt eine Rolle. Loest die frueher fest
-- verdrahtete Regel-Reaktion ab, die als vier einzelne Settings existierte
-- (role_reaction_enabled, rules_message_id, rules_channel_name,
-- member_role_name) und deshalb genau EINMAL vorkommen konnte.
--
-- Kanal und Rolle als ID, nicht als Name: Namen aendern sich (jemand benennt
-- den Regelkanal um), IDs nicht. Die alte Loesung suchte den Kanal ueber
-- channel.name und die Rolle ueber discord.utils.get(name=...) - beides
-- zerbricht still bei einer Umbenennung.
--
-- UNIQUE(message_id, emoji): Ein Emoji auf einer Nachricht kann nur fuer eine
-- Rolle stehen. Zwei Regeln dafuer waeren nicht aufloesbar.
CREATE TABLE IF NOT EXISTS reaction_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    emoji TEXT NOT NULL,
    role_id TEXT NOT NULL,
    -- 1 = Reaktion entfernen nimmt die Rolle wieder weg (Verhalten der alten
    -- Regel-Reaktion). 0 = einmal vergeben, bleibt.
    entzug_erlaubt INTEGER NOT NULL DEFAULT 1,
    -- Optionale Direktnachricht beim Vergeben. Leer = keine.
    dm_text TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(message_id, emoji)
);

-- Zuordnung Backend-Recht -> Discord-Rolle (03.09.2026). Damit spiegelt der Bot
-- vergebene Rechte auf dem Server wider. Bewusst eine eigene Tabelle statt eines
-- Settings-Eintrags: Es sind mehrere Zeilen mit Beziehung zueinander, und der
-- PRIMARY KEY verhindert strukturell, dass ein Recht zwei Rollen zugeordnet ist.
--
-- Umgekehrt DARF eine Rolle mehreren Rechten zugeordnet sein (z.B. zwei
-- Moderationsrechte, die beide die Moderator-Rolle bedeuten) - deshalb keine
-- UNIQUE-Bedingung auf role_id.
CREATE TABLE IF NOT EXISTS permission_roles (
    permission_key TEXT PRIMARY KEY,
    role_id TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Konfigurierbare Bot-Einstellungen (Kanal-IDs etc.), später über Backend änderbar.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Giveaways werden jetzt persistiert (übersteht Bot-Neustart), statt nur im Speicher zu leben.
CREATE TABLE IF NOT EXISTS giveaways (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    prize TEXT NOT NULL,
    end_time TEXT NOT NULL,
    winner_id TEXT,
    ended INTEGER DEFAULT 0
);

-- Schimpfwortliste, jetzt in der DB statt einer statischen Textdatei (über das
-- Backend verwaltbar, ohne Bot-Neustart).
CREATE TABLE IF NOT EXISTS badwords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL UNIQUE,
    added_by TEXT,
    added_at TEXT DEFAULT (datetime('now'))
);

-- Verstoß-Historie des Schimpfwortfilters. Nur ein kurzer Ausschnitt der Nachricht
-- wird gespeichert (nicht der volle Text), aus Datenschutzgründen.
-- Verstoß-Historie für ALLE Automod-Filter (Schimpfwort, Spam/Flut, Link), über
-- "violation_type" unterschieden, statt drei fast identischer Tabellen.
CREATE TABLE IF NOT EXISTS badword_violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id TEXT NOT NULL,
    message_excerpt TEXT,
    matched_word TEXT,
    timestamp TEXT DEFAULT (datetime('now')),
    violation_type TEXT DEFAULT 'badword'
);

-- Domain-Whitelist für den Link-Filter (Regel "Nur Whitelist-Domains erlaubt").
CREATE TABLE IF NOT EXISTS link_whitelist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL UNIQUE,
    added_by TEXT,
    added_at TEXT DEFAULT (datetime('now'))
);

-- Rollen, die komplett vom Link-Filter ausgenommen sind (Link-Filter-
-- Ueberarbeitung) - nur die role_id wird gespeichert, der Anzeigename wird bei
-- Bedarf live ueber den Bot aufgeloest statt redundant/veraltbar gecacht.
CREATE TABLE IF NOT EXISTS link_filter_whitelisted_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id TEXT NOT NULL UNIQUE,
    added_by TEXT,
    added_at TEXT DEFAULT (datetime('now'))
);

-- Kanäle, die komplett vom Link-Filter ausgenommen sind (z.B. ein
-- "Ressourcen"-Kanal) - unabhängig von Rollen und den drei Regeln unten.
CREATE TABLE IF NOT EXISTS link_filter_exempt_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL UNIQUE,
    added_by TEXT,
    added_at TEXT DEFAULT (datetime('now'))
);


-- Granulares Berechtigungssystem: ein Flag pro (User, Fähigkeit) statt fester Rollen.
-- Ersetzt die alte "permissions"-Tabelle (role admin/moderator/bot_user), die zur
-- Migration erhalten bleibt, aber von neuem Code nicht mehr geschrieben wird.
CREATE TABLE IF NOT EXISTS user_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id TEXT NOT NULL,
    permission_key TEXT NOT NULL,
    granted_by TEXT,
    granted_at TEXT DEFAULT (datetime('now')),
    UNIQUE(discord_user_id, permission_key)
);

-- Insert-only Audit-Log fuer sicherheitsrelevante Aktionen (Rechte-Aenderungen,
-- abgelehnte Owner-Zugriffsversuche, Config-Aenderungen). Es gibt bewusst KEINE
-- UPDATE/DELETE-Funktion dafuer im gesamten Projekt - ein Audit-Log, das sich
-- nachtraeglich veraendern liesse, waere wertlos.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id TEXT,
    action TEXT NOT NULL,
    target_id TEXT,
    detail TEXT,
    timestamp TEXT DEFAULT (datetime('now'))
);


-- Achievements (Spec 1, Phase 6). Definitionen werden per Code vorbefuellt
-- (siehe _seed_achievements()), nicht ueber die UI verwaltet - das waere ein
-- spaeterer Ausbau. secret=1: Name/Beschreibung erst nach Freischaltung sichtbar.
CREATE TABLE IF NOT EXISTS achievements (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    xp_reward INTEGER DEFAULT 0,
    secret INTEGER DEFAULT 0
);

-- UNIQUE(discord_user_id, achievement_id) verhindert doppelte Freischaltung
-- strukturell auf DB-Ebene (INSERT OR IGNORE in check_and_unlock_achievements()),
-- nicht nur durch Anwendungslogik.
CREATE TABLE IF NOT EXISTS user_achievements (
    discord_user_id TEXT NOT NULL,
    achievement_id TEXT NOT NULL,
    unlocked_at TEXT DEFAULT (datetime('now')),
    UNIQUE(discord_user_id, achievement_id)
);


-- Server-Statistik-Tracking (Spec 1, Phase 4 "Server-Gedaechtnis" + Phase 11:
-- erweiterte Community-Reaktionen). Tages-Aggregate statt Einzelereignis-Log,
-- damit Rekord-Vergleiche billig bleiben (ein SELECT statt COUNT ueber eine
-- waechst-endlos-Tabelle).
CREATE TABLE IF NOT EXISTS server_stats_daily (
    date TEXT PRIMARY KEY,
    messages_count INTEGER DEFAULT 0,
    quiz_answered_count INTEGER DEFAULT 0,
    games_count INTEGER DEFAULT 0,
    active_users INTEGER DEFAULT 0
);

-- Zwischenspeicherung fuer die Ermittlung eindeutiger aktiver User pro Tag
-- (server_stats_daily.active_users wird daraus abgeleitet/gezaehlt).
CREATE TABLE IF NOT EXISTS daily_active_users (
    date TEXT NOT NULL,
    discord_user_id TEXT NOT NULL,
    PRIMARY KEY (date, discord_user_id)
);

CREATE TABLE IF NOT EXISTS server_records (
    stat_key TEXT PRIMARY KEY,
    record_value INTEGER,
    record_date TEXT
);

-- Laufzeit-Zustand des Bots (Spec 1, Phase 9: Mood/Status), bewusst getrennt
-- von "settings" (das ist Admin-KONFIGURATION, hier steht dagegen der aktuell
-- ausgewertete ZUSTAND: mood, energy, last_mood_change, last_status_change,
-- last_spontaneous_message). Gleiche simple Key/Value-Struktur wie settings.
CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Admin-editierbare Text-Varianten-Pools fuer Personality (Spec 1, Phase B3) -
-- ersetzt die zuvor hartcodierten Pools in bot/mood.py (HELLO_VARIANTS etc.),
-- die jetzt nur noch als einmalige Seed-Vorlage dienen (siehe
-- _seed_personality_texts()). "pool" = Pool-Name (z.B. "hello", "status"),
-- "mood" = Mood-Schluessel oder NULL (bei Pools ohne Mood-Bezug, z.B.
-- "humor_event"), "humor_tier" = 'low'/'medium'/'high' oder NULL (bei Pools
-- ohne Tier-Staffelung, z.B. "status").
CREATE TABLE IF NOT EXISTS personality_texts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool TEXT NOT NULL,
    mood TEXT,
    humor_tier TEXT,
    text TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Verwaltung der einzelnen Iroh-Tunnel-Zugaenge fuer die Android-App (Spec 1,
-- Android-Folgeauftrag: mehrere Personen, je eigenes Ticket, einzeln widerrufbar).
-- Diese Tabelle wird NICHT vom Backend beschrieben (rein lesende Uebersichtsseite,
-- siehe app.py /tunnel_access), sondern ausschliesslich von
-- scripts/manage_bot_access.sh ueber die sqlite3-CLI - dieselbe Tabelle wird hier
-- trotzdem idempotent mitangelegt, damit eine frisch aufgesetzte bot.db (z.B. nach
-- einem git clone auf einem neuen Pi) die Tabelle schon kennt, bevor das Skript
-- zum ersten Mal laeuft (das Skript legt sie zusaetzlich defensiv selbst an).
-- revoked_at = NULL bedeutet aktiv; Zeilen werden beim Widerruf nie geloescht,
-- nur markiert (gleiches Nachvollziehbarkeits-Prinzip wie beim Audit-Log).
CREATE TABLE IF NOT EXISTS tunnel_access_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    slug TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);

-- Erzwungener Session-Logout (Sicherheits-Fix: ein widerrufener Tunnel-Zugang
-- muss auch eine bereits bestehende Browser-Login-Session dieser Person im
-- Haupt-Backend invalidieren, sonst bleibt ein Discord-OAuth-Session-Cookie trotz
-- entzogenem Zugang technisch gueltig). Pro Discord-User HOECHSTENS eine Zeile
-- (PRIMARY KEY) - "invalidiert seit wann", nicht ein Log mehrerer Ereignisse.
-- Bewusst allgemein gehalten (nicht tunnel-spezifisch benannt), da nuetzlich fuer
-- jeden Fall von "Session eines Users sofort beenden", nicht nur beim Tunnel-Widerruf.
CREATE TABLE IF NOT EXISTS user_session_invalidations (
    discord_user_id TEXT PRIMARY KEY,
    invalidated_at TEXT NOT NULL
);
"""

# Alle vergebbaren Berechtigungs-Flags. "is_admin" ist ein Sonderflag, das ALLE
# anderen impliziert (siehe has_permission()) und NUR über /users bzw. die
# admin-only Bot-Befehle vergeben werden kann - es gibt bewusst kein eigenes
# "manage_users"-Flag, um Rechte-Eskalation zu verhindern (ein Nutzer mit
# irgendeiner Berechtigung könnte sich sonst sonst selbst is_admin geben).
ALL_PERMISSIONS = [
    "is_admin",
    "verify_questions",
    "manage_categories",
    "create_embed",
    "create_giveaway",
    "send_dm",
    "manage_badword_filter",
    "manage_game_settings",
    # Kick/Ban/Unban/manueller Timeout ueber das Backend (Spec 1, Phase B4) -
    # bewusst ein EIGENES Flag statt is_admin wiederzuverwenden, konsistent mit
    # dem granularen 8(jetzt 9)-Flags-Modell. is_admin impliziert es weiterhin
    # automatisch wie jedes andere Flag (siehe has_permission()).
    "moderate_members",
]

# Liegt seit dem 03.09.2026 in schema_helfer.py, damit die Moduldateien es
# benutzen koennen, ohne database (und damit config und dotenv) zu laden.
_ensure_column = schema_helfer.ensure_column


def _cleanup_veraltetes(conn):
    """Einmaliges, idempotentes Aufraeumen von Altlasten (03.09.2026, Freigabe
    von Patrick).

    1. Die Tabelle "permissions" stammt aus dem frueheren rollenbasierten System
       ('admin'/'moderator'/'bot_user') und wurde von "user_permissions"
       (Einzel-Flags) abgeloest. Die Uebersetzung ist laengst gelaufen - der
       einzige alte Eintrag (role=admin) steht als is_admin mit granted_by=
       'migration' in der neuen Tabelle. Damit ist die alte Tabelle tot, ebenso
       die drei Funktionen get_permission/set_permission/revoke_permission, die
       nur sie angefasst haben und von niemandem mehr aufgerufen wurden.

    2. Der Textpool "hello" hatte genau einen Nutzer: den >hallo-Befehl. Der ist
       entfallen, damit stehen die 17 Texte nur noch in der Backend-Oberflaeche
       herum und laden dazu ein, etwas zu pflegen, das nirgends erscheint. Als
       Morgengruesse taugten sie nicht: alle sprechen mit {name} eine einzelne
       ankommende Person an, keiner erwaehnt den Morgen, und alle haengen an
       Mood und Humorstufe - was beim Morgengruss ausdruecklich nicht gewollt
       ist.

    Beides laeuft ins Leere, sobald es einmal passiert ist.
    """
    conn.execute("DROP TABLE IF EXISTS permissions")
    conn.execute("DELETE FROM personality_texts WHERE pool = 'hello'")


# Feste Achievement-Definitionen (Spec 1, Phase 6). INSERT OR IGNORE macht das
# idempotent - ein spaeterer manueller Eingriff an einer Zeile (z.B. via SQL)
# wird bei jedem Neustart NICHT ueberschrieben, nur fehlende IDs werden ergaenzt.
def _seed_achievements(conn):
    """Traegt die Achievements der INSTALLIERTEN Module in die Datenbank ein.

    Die Definitionen stehen nicht mehr hier, sondern in den Modulen selbst
    (achievements_quiz.py, achievements_xp_level.py) - siehe achievements.py
    fuer die Begruendung. Diese Funktion kennt keinen einzigen
    Achievement-Namen mehr, nur noch den Weg in die Tabelle.

    INSERT OR IGNORE macht es idempotent: Ein spaeter von Hand geaenderter
    Eintrag wird bei jedem Neustart NICHT ueberschrieben, nur fehlende IDs
    kommen dazu. Achievements eines entfernten Moduls bleiben als Datenzeile
    stehen - wer sie frueher freigeschaltet hat, verliert sie also nicht.
    """
    for achievement in achievements.REGISTRATUR.alle():
        conn.execute(
            "INSERT OR IGNORE INTO achievements (id, name, description, xp_reward, secret) "
            "VALUES (?, ?, ?, ?, ?)",
            (achievement.id, achievement.name, achievement.beschreibung,
             achievement.xp_belohnung, int(achievement.geheim)),
        )


def _seed_personality_texts(conn):
    """Einmaliger, idempotenter Import der zuvor hartcodierten Text-Pools aus
    mood.TEXT_POOL_SEEDS in die admin-editierbare personality_texts-Tabelle
    (Spec 1, Phase B3). Importiert PRO POOL nur, wenn dieser Pool noch komplett
    leer ist - damit spaetere Admin-Aenderungen (Hinzufuegen/Aendern/Loeschen
    ueber das Backend) bei jedem Bot-Neustart erhalten bleiben statt
    ueberschrieben zu werden (gleiches Muster wie import_badwords_from_list())."""
    for pool_name, (shape, data) in mood.TEXT_POOL_SEEDS.items():
        count = conn.execute(
            "SELECT COUNT(*) c FROM personality_texts WHERE pool = ?", (pool_name,)
        ).fetchone()["c"]
        if count > 0:
            continue
        rows = []
        if shape == "mood_tier":
            for mood_key, variants in data.items():
                for tier, text in variants:
                    rows.append((pool_name, mood_key, tier, text))
        elif shape == "mood_only":
            for mood_key, texts in data.items():
                for text in texts:
                    rows.append((pool_name, mood_key, None, text))
        elif shape == "tier_only":
            for tier, text in data:
                rows.append((pool_name, None, tier, text))
        elif shape == "plain":
            # Weder Mood noch Humorstufe - eine flache Liste gleichberechtigter
            # Texte (Pool "morning"). Beide Spalten bleiben NULL, die
            # Bearbeitungsseite blendet die Auswahlfelder dann von selbst aus
            # (siehe mood.TEXT_POOL_INFO, has_mood/has_tier = False).
            for text in data:
                rows.append((pool_name, None, None, text))
        if rows:
            conn.executemany(
                "INSERT INTO personality_texts (pool, mood, humor_tier, text) VALUES (?, ?, ?, ?)",
                rows,
            )


def _migrate_link_filter_mode(conn):
    """Einmalige Migration vom frueheren EXKLUSIVEN link_filter_mode
    (block_all/whitelist/new_members_only) zu den drei unabhaengig
    kombinierbaren Regel-Schaltern (Link-Filter-Ueberarbeitung). Migriert NUR,
    wenn noch KEINE der drei neuen Regel-Settings existiert (echtes
    Erststart-/Erst-Upgrade-Signal) - ein spaeterer Bot-Neustart ueberschreibt
    dann nicht mehr, was der Admin inzwischen ueber das neue Backend
    eingestellt hat. Bestehende Konfiguration wird 1:1 uebersetzt (block_all
    bleibt "alle Links blockieren", whitelist bleibt "nur Whitelist-Domains",
    new_members_only bleibt "neue Mitglieder duerfen keine Links") - nie
    ploetzlich alles erlauben oder alles blockieren."""
    new_keys = ("link_filter_block_all_enabled", "link_filter_whitelist_enabled", "link_filter_new_members_enabled")
    placeholders = ",".join("?" * len(new_keys))
    already_migrated = conn.execute(
        f"SELECT 1 FROM settings WHERE key IN ({placeholders})", new_keys
    ).fetchone()
    if already_migrated:
        return

    old_mode_row = conn.execute("SELECT value FROM settings WHERE key = 'link_filter_mode'").fetchone()
    old_mode = old_mode_row["value"] if old_mode_row else config.DEFAULT_SETTINGS["link_filter_mode"]

    values = {
        "link_filter_block_all_enabled": "1" if old_mode == "block_all" else "0",
        "link_filter_whitelist_enabled": "1" if old_mode == "whitelist" else "0",
        "link_filter_new_members_enabled": "1" if old_mode == "new_members_only" else "0",
    }
    for key, value in values.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING", (key, value)
        )


def _migrate_drop_tunnel_slug_unique(conn):
    """Bugfix: tunnel_access_grants.slug hatte urspruenglich eine GLOBALE,
    dauerhafte UNIQUE-Constraint (nicht nur "unter aktiven Zugaengen"). Dadurch
    schlug jeder erneute Zugang mit einem frueher schon einmal genutzten (und
    laengst widerrufenen) Namen mit einem unbehandelten sqlite3.IntegrityError
    fehl. Migriert idempotent auf einen normalen, nicht-eindeutigen Index -
    Eindeutigkeit wird weiterhin nur unter AKTIVEN Zugaengen erzwungen (siehe
    scripts/tunnel_access_core.py add_grant()), History bleibt vollstaendig
    erhalten."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tunnel_access_grants'"
    ).fetchone()
    if not row or not row["sql"] or "UNIQUE" not in row["sql"]:
        return
    conn.execute("ALTER TABLE tunnel_access_grants RENAME TO tunnel_access_grants_old_unique")
    conn.execute(
        """
        CREATE TABLE tunnel_access_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            slug TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT,
            discord_user_id TEXT
        )
        """
    )
    old_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tunnel_access_grants_old_unique)")}
    discord_col = "discord_user_id" if "discord_user_id" in old_cols else "NULL"
    conn.execute(
        f"INSERT INTO tunnel_access_grants (id, label, slug, created_at, revoked_at, discord_user_id) "
        f"SELECT id, label, slug, created_at, revoked_at, {discord_col} FROM tunnel_access_grants_old_unique"
    )
    conn.execute("DROP TABLE tunnel_access_grants_old_unique")


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA)

        # Danach die Tabellen der installierten Module. MUSS vor den
        # Migrationen unten stehen: Die greifen auf Tabellen zu, die es sonst
        # noch nicht gibt.
        angewendet = module.REGISTRATUR.schema_anwenden(conn)
        if angewendet:
            print(f"[module] Schema angewendet fuer: {', '.join(angewendet)}")
        # Bestehende questions-Tabelle um category_id erweitern, ohne Daten zu
        # verlieren. ON DELETE SET NULL sorgt dafür, dass beim Löschen einer
        # Kategorie die zugehörigen Fragen nur ihre Zuordnung verlieren, nicht
        # gelöscht werden (zusätzlich in delete_category() auch explizit gesetzt,
        # falls PRAGMA foreign_keys in einer Verbindung mal nicht aktiv sein sollte).
        # Bestehende badword_violations-Zeilen (aus der Zeit vor Spam-/Link-Filter)
        # bekommen automatisch 'badword' als Typ - SQLite füllt bei ALTER TABLE ADD
        # COLUMN mit einem konstanten DEFAULT alle bestehenden Zeilen damit auf.
        _ensure_column(conn, "badword_violations", "violation_type", "TEXT DEFAULT 'badword'")
        # Tages-Zaehler fuer Nachrichten-XP (Spec 1: daily_xp_limit) - persistent
        # statt In-Memory, da er einen ganzen Tag und Bot-Neustarts ueberstehen muss.
        # Quiz-Serien (Streaks, Phase 11) - im bereits vorhandenen user_stats
        # mitgefuehrt statt einer eigenen Tabelle, da es thematisch genau dort
        # hingehoert (Zaehler fuer Achievement-/Reaktions-Bedingungen).
        # Pro-User Nachrichten-Zaehler + letzte Aktivitaet (Spec 1, Phase B4,
        # Ueberwachungs-Uebersicht) - server_stats_daily/daily_active_users
        # (Phase 11) kennen nur serverweite Tages-Aggregate, keine Werte pro
        # einzelnem User. Im bereits vorhandenen user_stats mitgefuehrt statt
        # einer eigenen Tabelle, aus demselben Grund wie die Quiz-Serien oben.
        # Sicherheits-Fix (Android-Folgeauftrag): jeder Tunnel-Zugang wird jetzt an
        # eine konkrete Discord-User-ID gebunden statt nur an einen freien Namen,
        # damit ein Widerruf gezielt die Session GENAU dieser Person invalidieren
        # kann (siehe invalidate_user_session()). NULL bei bereits vor diesem Fix
        # angelegten (in diesem Projekt ausschliesslich bereits widerrufenen
        # Test-)Eintraegen - fuer die ist keine Session-Invalidierung mehr noetig.
        _ensure_column(conn, "tunnel_access_grants", "discord_user_id", "TEXT")
        # Bugfix: globale UNIQUE-Constraint auf slug entfernen (siehe Docstring der
        # Funktion) - MUSS vor dem Index unten laufen, da sie die Tabelle neu aufbaut.
        _migrate_drop_tunnel_slug_unique(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tunnel_access_grants_slug ON tunnel_access_grants (slug)"
        )
        # Indizes fuer die beiden am haeufigsten gefilterten Spalten (jeder Automod-
        # Check fragt count_user_violations()/get_badword_violations() nach
        # discord_user_id ab, jede Quizfrage-Auswahl/Kategorie-Verwaltung nach
        # category_id) - ohne Index bislang ein Full-Table-Scan bei wachsender
        # Tabelle. CREATE INDEX IF NOT EXISTS ist wie CREATE TABLE IF NOT EXISTS
        # von sich aus idempotent, kein _ensure_column-Umweg noetig. Muss NACH den
        # obigen _ensure_column-Aufrufen stehen, da questions.category_id auf einer
        # bestehenden (alten) DB erst dort hinzugefuegt wird.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_badword_violations_user "
            "ON badword_violations (discord_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_personality_texts_pool "
            "ON personality_texts (pool)"
        )
        # Achievements der installierten Module laden, BEVOR sie geseedet
        # werden. Sobald Module einzeln verkauft werden, kommt diese Liste aus
        # der Konfiguration - bis dahin steht hier an einer Stelle, was aktiv ist.
        # Die Tabellen der installierten Module anlegen, falls sie fehlen.
        # Damit baut sich die Datenbank selbst auf, wenn ein Modul spaeter
        # dazukommt - niemand muss von Hand etwas anlegen.
        _cleanup_veraltetes(conn)
        _migrate_link_filter_mode(conn)
        _seed_achievements(conn)
        _seed_personality_texts(conn)
        conn.commit()


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def get_setting(key: str, default=None):
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def get_bot_state(key: str, default=None):
    """Laufzeit-Zustand (Mood/Status, Phase 9) - eigene Tabelle statt "settings",
    da das kein Admin-konfigurierter Wert ist, sondern ein vom Bot selbst
    laufend aktualisierter Zustand."""
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_bot_state(key: str, value: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bot_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()


def get_personality_text_pool(pool: str) -> dict:
    """Liefert einen Text-Varianten-Pool zur Laufzeit-Nutzung, in exakt der Form,
    die mood.pick_variant()/pick_status_text() erwarten: {mood_or_'none': [(tier, text), ...]}.
    'none' wird als mood-Schluessel benutzt, wenn die Zeile kein mood hat (z.B.
    "humor_event") - passend zu mood.pick_humor_event_text(), das den Pool per
    pool.get("none", []) als flache Liste erwartet."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT mood, humor_tier, text FROM personality_texts WHERE pool = ? ORDER BY id",
            (pool,),
        ).fetchall()
    result = {}
    for row in rows:
        key = row["mood"] or "none"
        tier = row["humor_tier"] or "low"
        result.setdefault(key, []).append((tier, row["text"]))
    return result


def get_personality_texts_admin(pool: str):
    """Wie get_personality_text_pool(), aber mit id (fuer Loeschen/Bearbeiten auf
    der Backend-Seite) und ohne die 'none'-Umbenennung - roh fuer die UI."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, pool, mood, humor_tier, text FROM personality_texts WHERE pool = ? ORDER BY "
            "COALESCE(mood, ''), COALESCE(humor_tier, ''), id",
            (pool,),
        ).fetchall()


def add_personality_text(pool: str, mood_key, humor_tier, text: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO personality_texts (pool, mood, humor_tier, text) VALUES (?, ?, ?, ?)",
            (pool, mood_key or None, humor_tier or None, text),
        )
        conn.commit()
        return cur.lastrowid


def update_personality_text(text_id: int, mood_key, humor_tier, text: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE personality_texts SET mood = ?, humor_tier = ?, text = ? WHERE id = ?",
            (mood_key or None, humor_tier or None, text, text_id),
        )
        conn.commit()


def delete_personality_text(text_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM personality_texts WHERE id = ?", (text_id,))
        conn.commit()


def count_recent_violations(minutes: int = 60) -> int:
    """Anzahl ALLER Automod-Verstöße (badword+link+spam, siehe violation_type)
    der letzten `minutes` Minuten - für die Mood-Regel "annoyed" (Phase 9)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM badword_violations WHERE timestamp > datetime('now', ?)",
            (f"-{int(minutes)} minutes",),
        ).fetchone()["c"]


def bootstrap_admin(discord_user_id: str):
    """Trägt discord_user_id einmalig als is_admin ein, falls user_permissions noch
    komplett leer ist (nach der Rollen-Migration bereits befüllt, falls es vorher
    einen Admin gab - dieser Aufruf ist dann automatisch ein No-Op)."""
    if not discord_user_id or discord_user_id == "CHANGE_ME":
        return
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) c FROM user_permissions").fetchone()["c"]
        if count == 0:
            conn.execute(
                "INSERT OR IGNORE INTO user_permissions (discord_user_id, permission_key, granted_by) "
                "VALUES (?, 'is_admin', 'bootstrap')",
                (str(discord_user_id),),
            )
            conn.commit()


# --- Granulares Berechtigungssystem ---

def is_protected_owner(discord_user_id) -> bool:
    """True, wenn diese ID der Bot-Owner ist - EINE zentrale Pruefung fuer jede
    Stelle, die den Owner vor einer Aktion eines ANDEREN Users schuetzen muss
    (bisher: Rechte-Entzug in /users/save, /users/revoke_one, grant_access/
    revoke_access; seit Phase B4 zusaetzlich Kick/Ban/Timeout). Verhindert, dass
    diese Pruefung an mehreren Stellen leicht unterschiedlich (und potenziell
    fehlerhaft) dupliziert wird."""
    return bool(config.BOT_OWNER_ID) and str(discord_user_id) == config.BOT_OWNER_ID


def has_permission(discord_user_id, key: str) -> bool:
    """True, wenn der User genau dieses Flag ODER is_admin hat (is_admin impliziert alles).

    Der Bot-Owner (config.BOT_OWNER_ID) ist ein Sonderfall VOR der eigentlichen
    Abfrage: er hat immer alle Rechte, unabhaengig vom Zustand von
    user_permissions (auch wenn die Tabelle leer/manipuliert ist) - analog zum
    bestehenden is_admin-Bypass, nur eine Ebene davor."""
    if is_protected_owner(discord_user_id):
        return True
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_permissions WHERE discord_user_id = ? AND permission_key IN (?, 'is_admin')",
            (str(discord_user_id), key),
        ).fetchone()
        return row is not None


def log_audit(actor_id, action: str, target_id=None, detail=None):
    """Schreibt einen Audit-Log-Eintrag. Reines INSERT, keine Rueckgabe -
    wird zusaetzlich zur eigentlichen Aktion aufgerufen, nie anstelle davon."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO audit_log (actor_id, action, target_id, detail) VALUES (?, ?, ?, ?)",
            (str(actor_id) if actor_id is not None else None, action,
             str(target_id) if target_id is not None else None, detail),
        )
        conn.commit()


def get_audit_log(limit: int = 100, offset: int = 0):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, actor_id, action, target_id, detail, timestamp FROM audit_log "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]


def count_audit_log() -> int:
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) c FROM audit_log").fetchone()["c"]


def get_tunnel_access_grants():
    """Reine Lesefunktion fuer die Uebersichtsseite - Zeilen werden ausschliesslich
    von scripts/tunnel_access_core.py (CLI-Wrapper ODER Bot Connect Dashboard)
    geschrieben, nie vom Haupt-Backend selbst."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT label, slug, discord_user_id, created_at, revoked_at FROM tunnel_access_grants "
            "ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def invalidate_user_session(discord_user_id) -> None:
    """Erzwingt beim naechsten Request einen Logout fuer diesen User (jede
    bestehende Browser-Session, die vor jetzt ausgestellt wurde, wird ab sofort
    als ungueltig behandelt, siehe app.py _enforce_session_invalidation()).
    Genutzt beim Widerruf eines Tunnel-Zugangs (tunnel_access_core.py), aber
    bewusst allgemein nutzbar - nicht tunnel-spezifisch."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO user_session_invalidations (discord_user_id, invalidated_at) VALUES (?, ?) "
            "ON CONFLICT(discord_user_id) DO UPDATE SET invalidated_at = excluded.invalidated_at",
            (str(discord_user_id), now),
        )
        conn.commit()


def get_session_invalidated_at(discord_user_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT invalidated_at FROM user_session_invalidations WHERE discord_user_id = ?",
            (str(discord_user_id),),
        ).fetchone()
        return row["invalidated_at"] if row else None


def grant_permission(discord_user_id, key: str, granted_by):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO user_permissions (discord_user_id, permission_key, granted_by) VALUES (?, ?, ?) "
            "ON CONFLICT(discord_user_id, permission_key) DO UPDATE SET "
            "granted_by = excluded.granted_by, granted_at = datetime('now')",
            (str(discord_user_id), key, str(granted_by)),
        )
        conn.commit()
    log_audit(granted_by, "grant_permission", target_id=discord_user_id, detail=key)


def revoke_user_permission(discord_user_id, key: str, actor_id=None):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM user_permissions WHERE discord_user_id = ? AND permission_key = ?",
            (str(discord_user_id), key),
        )
        conn.commit()
    log_audit(actor_id, "revoke_permission", target_id=discord_user_id, detail=key)


def set_user_permissions(discord_user_id, keys, granted_by):
    """Setzt EXAKT die übergebenen Flags für diesen User - alle anderen, die er
    vorher hatte, werden entfernt (für das Formular auf /users, das den kompletten
    Berechtigungssatz eines Users auf einmal speichert)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM user_permissions WHERE discord_user_id = ?", (str(discord_user_id),))
        for key in keys:
            conn.execute(
                "INSERT INTO user_permissions (discord_user_id, permission_key, granted_by) VALUES (?, ?, ?)",
                (str(discord_user_id), key, str(granted_by)),
            )
        conn.commit()
    log_audit(granted_by, "set_user_permissions", target_id=discord_user_id, detail=", ".join(keys) if keys else "(keine)")


def get_user_permissions(discord_user_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT permission_key FROM user_permissions WHERE discord_user_id = ? ORDER BY permission_key",
            (str(discord_user_id),),
        ).fetchall()
        return [r["permission_key"] for r in rows]


def get_earned_roles(nur_aktive: bool = False):
    with get_connection() as conn:
        sql = "SELECT * FROM earned_roles"
        if nur_aktive:
            sql += " WHERE enabled = 1"
        return [dict(r) for r in conn.execute(sql + " ORDER BY schwelle, id")]


def get_earned_role(regel_id: int):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM earned_roles WHERE id = ?", (int(regel_id),)).fetchone()
        return dict(row) if row else None


def add_earned_role(role_id, bedingung_art: str, schwelle: int, ankuendigung: str = "",
                    channel_id: str = "", automatisch: bool = False) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO earned_roles (role_id, bedingung_art, schwelle, ankuendigung, "
            "channel_id, automatisch) VALUES (?, ?, ?, ?, ?, ?)",
            (str(role_id), bedingung_art, int(schwelle), (ankuendigung or "").strip(),
             (channel_id or "").strip(), 1 if automatisch else 0),
        )
        conn.commit()
        return cur.lastrowid


def update_earned_role(regel_id: int, *, schwelle=None, ankuendigung=None,
                       channel_id=None, automatisch=None, enabled=None):
    felder, werte = [], []
    if schwelle is not None:
        felder.append("schwelle = ?"); werte.append(int(schwelle))
    if ankuendigung is not None:
        felder.append("ankuendigung = ?"); werte.append((ankuendigung or "").strip())
    if channel_id is not None:
        felder.append("channel_id = ?"); werte.append((channel_id or "").strip())
    if automatisch is not None:
        felder.append("automatisch = ?"); werte.append(1 if automatisch else 0)
    if enabled is not None:
        felder.append("enabled = ?"); werte.append(1 if enabled else 0)
    if not felder:
        return
    with get_connection() as conn:
        conn.execute(f"UPDATE earned_roles SET {', '.join(felder)} WHERE id = ?",
                     (*werte, int(regel_id)))
        conn.commit()


def delete_earned_role(regel_id: int) -> None:
    """Loescht die Regel UND die Merker dazu.

    Die Merker allein zurueckzulassen brächte nichts: Legt jemand spaeter eine
    neue Regel an, bekaeme sie eine neue id, und die alten Zeilen zeigten ins
    Leere. Bereits VERGEBENE Rollen bleiben davon unberuehrt - die hat der
    Nutzer, und er soll sie behalten.
    """
    with get_connection() as conn:
        conn.execute("DELETE FROM earned_role_grants WHERE rule_id = ?", (int(regel_id),))
        conn.execute("DELETE FROM earned_roles WHERE id = ?", (int(regel_id),))
        conn.commit()


def earned_role_status(regel_id: int, discord_user_id):
    """Was ist bei dieser Person zu dieser Regel schon passiert?"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT angekuendigt_at, vergeben_at FROM earned_role_grants "
            "WHERE rule_id = ? AND discord_user_id = ?",
            (int(regel_id), str(discord_user_id)),
        ).fetchone()
        return dict(row) if row else {"angekuendigt_at": None, "vergeben_at": None}


def mark_earned_role_announced(regel_id: int, discord_user_id) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO earned_role_grants (rule_id, discord_user_id, angekuendigt_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(rule_id, discord_user_id) DO UPDATE SET "
            "angekuendigt_at = COALESCE(angekuendigt_at, datetime('now'))",
            (int(regel_id), str(discord_user_id)),
        )
        conn.commit()


def mark_earned_role_granted(regel_id: int, discord_user_id) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO earned_role_grants (rule_id, discord_user_id, angekuendigt_at, vergeben_at) "
            "VALUES (?, ?, datetime('now'), datetime('now')) "
            "ON CONFLICT(rule_id, discord_user_id) DO UPDATE SET vergeben_at = datetime('now')",
            (int(regel_id), str(discord_user_id)),
        )
        conn.commit()


def get_reaction_roles(nur_aktive: bool = False):
    """Alle Reaktionsrollen-Regeln."""
    with get_connection() as conn:
        sql = "SELECT * FROM reaction_roles"
        if nur_aktive:
            sql += " WHERE enabled = 1"
        return [dict(r) for r in conn.execute(sql + " ORDER BY id")]


def find_reaction_role(message_id, emoji: str):
    """Die Regel zu genau dieser Nachricht und diesem Emoji, oder None.

    Nur aktive Regeln - eine abgeschaltete soll sich verhalten, als gaebe es sie
    nicht, statt weiter Rollen zu vergeben.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM reaction_roles WHERE message_id = ? AND emoji = ? AND enabled = 1",
            (str(message_id), emoji),
        ).fetchone()
        return dict(row) if row else None


def add_reaction_role(channel_id, message_id, emoji: str, role_id,
                      entzug_erlaubt: bool = True, dm_text: str = "") -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO reaction_roles (channel_id, message_id, emoji, role_id, "
            "entzug_erlaubt, dm_text) VALUES (?, ?, ?, ?, ?, ?)",
            (str(channel_id), str(message_id), emoji, str(role_id),
             1 if entzug_erlaubt else 0, (dm_text or "").strip()),
        )
        conn.commit()
        return cur.lastrowid


def update_reaction_role(regel_id: int, *, enabled=None, entzug_erlaubt=None, dm_text=None):
    felder, werte = [], []
    if enabled is not None:
        felder.append("enabled = ?"); werte.append(1 if enabled else 0)
    if entzug_erlaubt is not None:
        felder.append("entzug_erlaubt = ?"); werte.append(1 if entzug_erlaubt else 0)
    if dm_text is not None:
        felder.append("dm_text = ?"); werte.append((dm_text or "").strip())
    if not felder:
        return
    with get_connection() as conn:
        conn.execute(f"UPDATE reaction_roles SET {', '.join(felder)} WHERE id = ?",
                     (*werte, int(regel_id)))
        conn.commit()


def delete_reaction_role(regel_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM reaction_roles WHERE id = ?", (int(regel_id),))
        conn.commit()


def get_permission_role_map() -> dict:
    """{Backend-Recht: Discord-Rollen-ID}. Leer, solange nichts zugeordnet ist -
    dann macht der Abgleich schlicht nichts."""
    with get_connection() as conn:
        return {
            row["permission_key"]: row["role_id"]
            for row in conn.execute("SELECT permission_key, role_id FROM permission_roles")
        }


def set_permission_role(permission_key: str, role_id: str) -> None:
    """Ordnet einem Recht eine Rolle zu. Leere role_id hebt die Zuordnung auf.

    permission_key wird gegen ALL_PERMISSIONS geprueft: Ein Tippfehler wuerde
    sonst eine Zeile anlegen, die nie greift, und niemandem faellt auf warum.
    """
    if permission_key not in ALL_PERMISSIONS:
        raise ValueError(f"Unbekannte Berechtigung: {permission_key}")
    role_id = (role_id or "").strip()
    with get_connection() as conn:
        if not role_id:
            conn.execute("DELETE FROM permission_roles WHERE permission_key = ?", (permission_key,))
        else:
            conn.execute(
                "INSERT INTO permission_roles (permission_key, role_id) VALUES (?, ?) "
                "ON CONFLICT(permission_key) DO UPDATE SET role_id = excluded.role_id, "
                "updated_at = datetime('now')",
                (permission_key, role_id),
            )
        conn.commit()


def get_all_permissioned_users():
    """Für die Übersichtstabelle auf /users: alle User mit mindestens einer
    Berechtigung, gruppiert mit ihren einzelnen Flags."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT discord_user_id, permission_key, granted_by, granted_at "
            "FROM user_permissions ORDER BY discord_user_id, permission_key"
        ).fetchall()
    users = {}
    for row in rows:
        uid = row["discord_user_id"]
        users.setdefault(uid, {"discord_user_id": uid, "permissions": []})
        users[uid]["permissions"].append(
            {"key": row["permission_key"], "granted_by": row["granted_by"], "granted_at": row["granted_at"]}
        )
    return list(users.values())


# --- Quiz ---

def validate_question_submission(question, option_a, option_b, option_c, option_d, correct_option):
    """Gemeinsame Validierung fuer JEDEN Weg, eine Frage einzureichen (Web-Formular
    /qinput UND der Discord-Befehl >frage_einreichen) - eine einzige Quelle der
    Wahrheit fuer die Regeln, damit sie nicht an zwei Stellen unabhaengig
    voneinander gepflegt werden und dabei auseinanderlaufen koennen. Gibt bei
    einem Validierungsfehler eine fuer Menschen lesbare Fehlermeldung zurueck,
    sonst None."""
    if not all([question, option_a, option_b, option_c, option_d]):
        return "Frage und alle vier Antworten sind erforderlich."
    if correct_option not in ("A", "B", "C", "D"):
        return "Richtige Antwort muss A, B, C oder D sein."
    return None


# --- Kategorien ---

# --- Giveaways ---

def create_giveaway(message_id, channel_id, prize, end_time_iso):
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO giveaways (message_id, channel_id, prize, end_time) VALUES (?, ?, ?, ?)",
            (str(message_id), str(channel_id), prize, end_time_iso),
        )
        conn.commit()
        return cur.lastrowid


def get_active_giveaways():
    with get_connection() as conn:
        return conn.execute("SELECT * FROM giveaways WHERE ended = 0").fetchall()


def end_giveaway(message_id, winner_id):
    with get_connection() as conn:
        conn.execute(
            "UPDATE giveaways SET ended = 1, winner_id = ? WHERE message_id = ?",
            (str(winner_id) if winner_id else None, str(message_id)),
        )
        conn.commit()

# --- Badwords (Schimpfwortfilter) ---

def get_badwords():
    """Nur die Wörter als Liste (für den Filter-Abgleich)."""
    with get_connection() as conn:
        return [r["word"] for r in conn.execute("SELECT word FROM badwords ORDER BY word").fetchall()]


def get_badwords_full():
    """Volle Zeilen (für die Verwaltungsseite im Backend)."""
    with get_connection() as conn:
        return conn.execute("SELECT * FROM badwords ORDER BY word").fetchall()


def add_badword(word: str, added_by):
    word = (word or "").strip().lower()
    if not word:
        return
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO badwords (word, added_by) VALUES (?, ?)",
            (word, str(added_by)),
        )
        conn.commit()


def remove_badword(word_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM badwords WHERE id = ?", (word_id,))
        conn.commit()


def import_badwords_from_list(words):
    """Einmaliger, idempotenter Import aus der alten badwords.txt.

    Importiert nur, wenn die Tabelle noch komplett leer ist, damit spätere manuelle
    Änderungen (Hinzufügen/Entfernen über das Backend) bei jedem Bot-Neustart erhalten
    bleiben statt überschrieben zu werden.
    """
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) c FROM badwords").fetchone()["c"]
        if count > 0:
            return 0
        imported = 0
        for w in words:
            w = (w or "").strip().lower()
            if not w:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO badwords (word, added_by) VALUES (?, 'import')", (w,)
            )
            imported += 1
        conn.commit()
        return imported


def log_badword_violation(discord_user_id, message_excerpt: str, matched_word: str, violation_type: str = "badword"):
    """violation_type: 'badword' | 'spam' | 'link' - eine gemeinsame Tabelle für
    alle drei Automod-Filter statt drei fast identischer Tabellen."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO badword_violations (discord_user_id, message_excerpt, matched_word, violation_type) "
            "VALUES (?, ?, ?, ?)",
            (str(discord_user_id), (message_excerpt or "")[:60], matched_word, violation_type),
        )
        conn.commit()


def get_badword_violations(limit: int = 50, violation_type: str = None):
    with get_connection() as conn:
        if violation_type:
            return conn.execute(
                "SELECT * FROM badword_violations WHERE violation_type = ? ORDER BY id DESC LIMIT ?",
                (violation_type, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM badword_violations ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def count_user_violations_total(discord_user_id) -> int:
    """Rohe Gesamtzahl ALLER Automod-Verstöße eines Users (alle Typen, ohne
    Verjährung/manuellen Reset berücksichtigt) - für die schnelle Übersicht auf
    /members. Für die Timeout-Schwellen-Logik weiterhin count_user_violations()
    verwenden (das berücksichtigt Verjährung/Reset korrekt, siehe dort)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) c FROM badword_violations WHERE discord_user_id = ?",
            (str(discord_user_id),),
        ).fetchone()
        return row["c"]


def get_user_violations(discord_user_id, limit: int = 50):
    """Automod-Verstoß-Historie EINES Users, alle drei Filtertypen gemeinsam
    (fuer die Ueberwachungs-Uebersicht, Spec 1, Phase B4) - im Unterschied zu
    get_badword_violations() (serverweite Liste, optional nach Typ gefiltert)
    hier nach discord_user_id gefiltert, absteigend nach Zeit."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM badword_violations WHERE discord_user_id = ? ORDER BY id DESC LIMIT ?",
            (str(discord_user_id), limit),
        ).fetchall()


def _violation_reset_key(discord_user_id, violation_type: str) -> str:
    # "badword" behält den historischen Schlüsselnamen (schon vor der Mehrtypen-
    # Erweiterung so verwendet und bereits produktiv im Einsatz) - andere Typen
    # bekommen ihren eigenen, damit ein Reset eines Typs nicht versehentlich die
    # Zählung eines anderen mit zurücksetzt.
    if violation_type == "badword":
        return f"badword_reset_{discord_user_id}"
    return f"{violation_type}_reset_{discord_user_id}"


def count_user_violations(discord_user_id, violation_type: str = "badword") -> int:
    """Anzahl der Verstöße dieses Users EINES Typs, die für die Timeout-Schwelle zählen.

    Zwei Modi, einstellbar über "badword_violation_expiry_mode" (gilt einheitlich für
    alle Typen, die eine Verstöße-über-Zeit-Schwelle nutzen - aktuell badword und link):
    - "auto_30d" (Default): nur Verstöße der letzten 30 Tage zählen. Alte
      Verstöße "verjähren" dadurch automatisch, ohne dass ein Cron-Job o.ä.
      nötig ist.
    - "manual_only": kein Zeitfenster - Verstöße zählen unbegrenzt weiter, bis
      ein Mod manuell zurücksetzt.

    In beiden Fällen bleibt die volle Historie unangetastet in der Tabelle und
    wird weiterhin komplett über get_badword_violations() angezeigt - nur diese
    Zählung hier ist (je nach Modus) eingeschränkt.

    Berücksichtigt außerdem einen manuellen Reset (siehe reset_user_violations): nur
    Verstöße mit einer höheren id als die beim Reset gemerkte Grenz-id zählen. Eine
    id-Grenze statt eines Zeitstempel-Vergleichs, weil SQLites datetime('now') nur
    Sekundengenauigkeit hat - bei einem Verstoß in derselben Sekunde wie ein Reset
    wäre ein Zeitstempel-Vergleich (</>=/>=) je nach Richtung entweder mehrdeutig
    oder würde den alten UND den neuen Verstoß fälschlich gemeinsam zählen. Die
    fortlaufende, eindeutig geordnete id kennt dieses Problem nicht.
    """
    with get_connection() as conn:
        reset_row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (_violation_reset_key(discord_user_id, violation_type),),
        ).fetchone()
        boundary_id = int(reset_row["value"]) if reset_row else 0

        mode_row = conn.execute(
            "SELECT value FROM settings WHERE key = 'badword_violation_expiry_mode'"
        ).fetchone()
        mode = mode_row["value"] if mode_row else "auto_30d"

        if mode == "manual_only":
            return conn.execute(
                "SELECT COUNT(*) c FROM badword_violations "
                "WHERE discord_user_id = ? AND violation_type = ? AND id > ?",
                (str(discord_user_id), violation_type, boundary_id),
            ).fetchone()["c"]

        return conn.execute(
            "SELECT COUNT(*) c FROM badword_violations WHERE discord_user_id = ? AND violation_type = ? "
            "AND timestamp >= datetime('now', '-30 days') AND id > ?",
            (str(discord_user_id), violation_type, boundary_id),
        ).fetchone()["c"]


def reset_user_violations(discord_user_id, violation_type: str = "badword"):
    """Setzt die Verstoß-ZÄHLUNG für die Timeout-Schwelle dieses Users (nur für
    diesen einen violation_type) zurück.

    Löscht bewusst NICHTS aus badword_violations - die volle Historie bleibt für
    die Anzeige/Audit auf der Moderationsseite erhalten. Stattdessen wird die aktuell
    höchste vorhandene id dieses Users/Typs als Grenze in der settings-Tabelle
    hinterlegt; count_user_violations() zählt danach nur noch Verstöße mit einer
    höheren id.
    """
    with get_connection() as conn:
        boundary_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) m FROM badword_violations WHERE discord_user_id = ? AND violation_type = ?",
            (str(discord_user_id), violation_type),
        ).fetchone()["m"]
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_violation_reset_key(discord_user_id, violation_type), str(boundary_id)),
        )
        conn.commit()


# --- Link-Whitelist (für den Link-Filter-Modus "whitelist") ---

def get_link_whitelist():
    with get_connection() as conn:
        return [r["domain"] for r in conn.execute("SELECT domain FROM link_whitelist ORDER BY domain").fetchall()]


def get_link_whitelist_full():
    with get_connection() as conn:
        return conn.execute("SELECT * FROM link_whitelist ORDER BY domain").fetchall()


def add_link_domain(domain: str, added_by):
    domain = (domain or "").strip().lower()
    for prefix in ("https://", "http://", "www."):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    domain = domain.split("/")[0]
    if not domain:
        return
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO link_whitelist (domain, added_by) VALUES (?, ?)", (domain, str(added_by)))
        conn.commit()


def remove_link_domain(domain_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM link_whitelist WHERE id = ?", (domain_id,))
        conn.commit()


# --- Link-Filter: Rollen-Whitelist + Kanal-Ausnahmeliste (Link-Filter-Ueberarbeitung) ---

def get_link_filter_whitelisted_role_ids():
    with get_connection() as conn:
        return [r["role_id"] for r in conn.execute("SELECT role_id FROM link_filter_whitelisted_roles").fetchall()]


def set_link_filter_whitelisted_roles(role_ids, added_by):
    """Ersetzt die komplette Rollen-Whitelist durch `role_ids` (Set-Semantik,
    passend zu einem Mehrfachauswahl-Formular - einfacher und robuster als
    einzelne Add/Remove-Aufrufe pro geänderter Checkbox nachzuvollziehen)."""
    clean_ids = [str(r).strip() for r in role_ids if str(r).strip().isdigit()]
    with get_connection() as conn:
        conn.execute("DELETE FROM link_filter_whitelisted_roles")
        conn.executemany(
            "INSERT INTO link_filter_whitelisted_roles (role_id, added_by) VALUES (?, ?)",
            [(rid, str(added_by)) for rid in clean_ids],
        )
        conn.commit()


def get_link_filter_exempt_channel_ids():
    with get_connection() as conn:
        return [r["channel_id"] for r in conn.execute("SELECT channel_id FROM link_filter_exempt_channels").fetchall()]


def set_link_filter_exempt_channels(channel_ids, added_by):
    """Ersetzt die komplette Kanal-Ausnahmeliste durch `channel_ids` - gleiche
    Set-Semantik wie set_link_filter_whitelisted_roles()."""
    clean_ids = [str(c).strip() for c in channel_ids if str(c).strip().isdigit()]
    with get_connection() as conn:
        conn.execute("DELETE FROM link_filter_exempt_channels")
        conn.executemany(
            "INSERT INTO link_filter_exempt_channels (channel_id, added_by) VALUES (?, ?)",
            [(cid, str(added_by)) for cid in clean_ids],
        )
        conn.commit()


# --- XP/Level-System (Spec 1) ---
# Bewusst getrennt von players.score (Quiz-Highscore, siehe Punkt B der Analyse):
# user_xp ist das umfassendere Fortschritts-System, players.score bleibt unveraendert
# der reine Quiz-Punktestand.

# In-Memory-Cooldown fuer generische XP-Vergabe, analog zum Quiz-Cooldown in
# cogs/quiz.py (_last_attempt) - muss keinen Bot-Neustart ueberstehen.
_xp_cooldowns: dict = {}




# --- Zaehler fuer Achievement-Bedingungen ---

# --- Achievements (Spec 1, Phase 6) ---

def check_and_unlock_achievements(discord_user_id, event: str, **context) -> list:
    """Zentrale Freischalt-Pruefung - die MECHANIK, nicht die Bedingungen.

    Welche Achievements es gibt und wann sie fallen, steht seit dem 03.09.2026
    in den Modulen (achievements_quiz.py, achievements_xp_level.py). Hier wird
    nur noch der Kontext zusammengestellt, den die Bedingungen brauchen, und das
    Ergebnis in die Datenbank geschrieben.

    Der Kontext wird BEWUSST hier besorgt und nicht in den Bedingungen: Die
    Bedingungen sind reine Funktionen ohne Datenbankzugriff, damit sie einzeln
    testbar sind. Vorher zaehlte die Quiz-Bedingung als Nebenwirkung den Zaehler
    hoch - eine Pruefung veraenderte also Daten, was beim Lesen nicht zu erwarten
    ist.

    Gibt die JETZT NEU freigeschalteten Achievements als dicts zurueck. Die
    Discord-Ankuendigung ist nicht Teil dieser Funktion (analog zum
    leveled_up-Rueckgabewert von award_xp()).

    Mehrfachauswertung ist unschaedlich: INSERT OR IGNORE gegen den
    UNIQUE(discord_user_id, achievement_id)-Constraint verhindert eine doppelte
    Freischaltung strukturell auf DB-Ebene, nicht nur durch diese Funktion.
    """
    uid = str(discord_user_id)

    # Der Kontext kommt von den MODULEN, nicht von hier. Bis zum 03.09.2026
    # stand an dieser Stelle eine if/elif-Kette mit Quiz-Wissen darin
    # (increment_user_stat(uid, "quiz_correct_count")) - ohne das Quizmodul
    # haette sie auf eine Tabelle gezeigt, die es gar nicht gibt.
    #
    # Was der Aufrufer schon weiss, wird durchgereicht und dabei auf die Namen
    # gebracht, die die Bedingungen erwarten.
    kontext = dict(context)
    if "hour" in context:
        kontext["stunde"] = context["hour"]
    if "new_level" in context:
        kontext["neues_level"] = context["new_level"]
    kontext.update(module.REGISTRATUR.kontext(event, uid))

    candidates = achievements.REGISTRATUR.kandidaten(event, kontext)
    if not candidates:
        return []

    unlocked = []
    with get_connection() as conn:
        for achievement_id in candidates:
            cur = conn.execute(
                "INSERT OR IGNORE INTO user_achievements (discord_user_id, achievement_id) VALUES (?, ?)",
                (uid, achievement_id),
            )
            if cur.rowcount > 0:
                row = conn.execute("SELECT * FROM achievements WHERE id = ?", (achievement_id,)).fetchone()
                if row:
                    unlocked.append(dict(row))
        conn.commit()

    for achievement in unlocked:
        if achievement["xp_reward"]:
            # Die XP-Vergabe gehoert zum Modul XP & Level. Erst hier importiert,
            # weil es umgekehrt ebenfalls hierher zurueckruft (Levelaufstieg ->
            # Achievement-Pruefung) - ein Import auf oberster Ebene waere ein Kreis.
            from modul_xp_level import award_xp

            award_xp(
                uid, achievement["xp_reward"], reason=f"achievement:{achievement['id']}",
                bypass_cooldown=True, _check_achievements=False,
            )

    return unlocked


def get_achievements_overview(discord_user_id) -> list:
    """Alle Achievement-Definitionen mit Freischalt-Status fuer diesen User -
    fuer die Backend-Uebersicht und den Bot-Befehl. Geheime, noch nicht
    freigeschaltete Achievements werden ohne Name/Beschreibung ausgeliefert
    (nur "???"), damit sie auch bei einer kompromittierten API-Antwort nicht
    vorab verraten werden."""
    uid = str(discord_user_id)
    with get_connection() as conn:
        definitions = conn.execute("SELECT * FROM achievements ORDER BY id").fetchall()
        unlocked_rows = conn.execute(
            "SELECT achievement_id, unlocked_at FROM user_achievements WHERE discord_user_id = ?", (uid,)
        ).fetchall()
    unlocked_map = {row["achievement_id"]: row["unlocked_at"] for row in unlocked_rows}

    overview = []
    for ach in definitions:
        unlocked_at = unlocked_map.get(ach["id"])
        is_unlocked = unlocked_at is not None
        entry = {
            "id": ach["id"],
            "unlocked": is_unlocked,
            "unlocked_at": unlocked_at,
            "xp_reward": ach["xp_reward"],
            "secret": bool(ach["secret"]),
        }
        if is_unlocked or not ach["secret"]:
            entry["name"] = ach["name"]
            entry["description"] = ach["description"]
        else:
            entry["name"] = "???"
            entry["description"] = None
        overview.append(entry)
    return overview



# --- Server-Statistik-Tracking / erweiterte Community-Reaktionen (Phase 11) ---

def parse_csv_ints(value: str) -> list:
    """Parst eine kommagetrennte Zahlenliste aus einem Setting (z.B.
    "5,10,20,50") in eine sortierte Liste eindeutiger Ganzzahlen. Ungueltige/
    leere Teile werden stillschweigend uebersprungen statt einen Fehler zu
    werfen (robust gegenueber Tippfehlern in der Admin-Eingabe)."""
    result = []
    for part in (value or "").split(","):
        part = part.strip()
        if part.isdigit():
            result.append(int(part))
    return sorted(set(result))


_VALID_SERVER_STATS = ("messages_count", "quiz_answered_count", "games_count")


def increment_server_stat(stat: str, by: int = 1) -> int:
    """Erhoeht einen Tages-Zaehler in server_stats_daily fuer HEUTE und gibt
    den neuen Gesamtwert zurueck. Analog zu increment_user_stat() aus Phase 5,
    nur serverweit statt pro User."""
    if stat not in _VALID_SERVER_STATS:
        raise ValueError(f"Unbekannter server_stats_daily-Schluessel: {stat}")
    today = time.strftime("%Y-%m-%d")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO server_stats_daily (date, {0}) VALUES (?, ?) "
            "ON CONFLICT(date) DO UPDATE SET {0} = {0} + excluded.{0}".format(stat),
            (today, by),
        )
        conn.commit()
        row = conn.execute(f"SELECT {stat} FROM server_stats_daily WHERE date = ?", (today,)).fetchone()
        return row[stat]


def track_daily_active_user(discord_user_id) -> int:
    """Merkt den User als heute aktiv (ueber daily_active_users) und
    aktualisiert server_stats_daily.active_users entsprechend. Gibt die
    aktuelle Anzahl eindeutiger aktiver User heute zurueck."""
    today = time.strftime("%Y-%m-%d")
    uid = str(discord_user_id)
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO daily_active_users (date, discord_user_id) VALUES (?, ?)",
            (today, uid),
        )
        count = conn.execute(
            "SELECT COUNT(*) c FROM daily_active_users WHERE date = ?", (today,)
        ).fetchone()["c"]
        conn.execute(
            "INSERT INTO server_stats_daily (date, active_users) VALUES (?, ?) "
            "ON CONFLICT(date) DO UPDATE SET active_users = excluded.active_users",
            (today, count),
        )
        conn.commit()
    return count


def get_user_overview(discord_user_id) -> dict:
    """Buendelt die Statistik EINER Person fuer die Mitglieder-Uebersicht.

    Die Werte kommen von den MODULEN, nicht von hier. Vorher fragte diese
    Funktion user_stats, user_xp und players direkt ab - drei Tabellen aus zwei
    Modulen, die es ohne diese Module gar nicht gibt.

    Fehlt ein Modul, fehlen seine Zahlen; die Standardwerte der uebrigen bleiben,
    damit die Seite nicht jeden Wert einzeln absichern muss.
    """
    return module.REGISTRATUR.uebersicht(discord_user_id)


def get_server_stat_today(stat: str) -> int:
    if stat not in _VALID_SERVER_STATS + ("active_users",):
        raise ValueError(f"Unbekannter server_stats_daily-Schluessel: {stat}")
    today = time.strftime("%Y-%m-%d")
    with get_connection() as conn:
        row = conn.execute(f"SELECT {stat} FROM server_stats_daily WHERE date = ?", (today,)).fetchone()
        return row[stat] if row else 0


def check_and_update_server_record(stat_key: str, current_value: int):
    """Vergleicht current_value gegen den bisherigen Rekord fuer stat_key
    (0, falls noch keiner existiert). Bei einem NEUEN Rekord: aktualisiert
    server_records und gibt {"old_value", "new_value"} zurueck - sonst None
    (keine Ankuendigung noetig)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT record_value FROM server_records WHERE stat_key = ?", (stat_key,)
        ).fetchone()
        old_value = row["record_value"] if row else 0
        if current_value <= old_value:
            return None
        today = time.strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO server_records (stat_key, record_value, record_date) VALUES (?, ?, ?) "
            "ON CONFLICT(stat_key) DO UPDATE SET record_value = excluded.record_value, "
            "record_date = excluded.record_date",
            (stat_key, current_value, today),
        )
        conn.commit()
    return {"old_value": old_value, "new_value": current_value}


def check_and_update_milestone(thresholds: list):
    """Prueft, ob die serverweite Gesamtzahl (get_total_quiz_correct_count())
    einen neuen, noch nicht angekuendigten Meilenstein aus `thresholds`
    erreicht/ueberschritten hat. bot_state.milestone_last_announced merkt sich
    den hoechsten bereits angekuendigten Wert, damit derselbe Meilenstein nicht
    bei jedem Heartbeat-Tick erneut angekuendigt wird. Gibt bei einem neuen
    Meilenstein {"threshold", "total"} zurueck, sonst None."""
    # Meilensteine zaehlen richtige Quizantworten - ohne das Quizmodul gibt es
    # sie schlicht nicht. Kein Fehler, sondern die richtige Antwort auf eine
    # Teilinstallation: nichts zu melden.
    try:
        from modul_quiz import get_total_quiz_correct_count
    except ImportError:
        return None

    total = get_total_quiz_correct_count()
    # Falsy-Check VOR dem int()-Parsing, wie bei den anderen bot_state-Lesern in
    # diesem Modul (_apply_community_bonus(), personality.py last_spontaneous_message)
    # - get_bot_state() liefert den Default nur, wenn die Zeile fehlt, NICHT wenn sie
    # mit einem leeren String existiert. Ohne diesen Guard crasht ein bereits in der
    # DB stehender Leerstring (z.B. ein Altlast-Wert aus einer frueheren Code-Version)
    # hier mit einem ungefangenen int('')-ValueError bei jedem Heartbeat-Tick.
    last_announced_raw = get_bot_state("milestone_last_announced", "0")
    last_announced = int(last_announced_raw) if last_announced_raw and last_announced_raw.strip() else 0
    newly_crossed = [t for t in thresholds if last_announced < t <= total]
    if not newly_crossed:
        return None
    highest = max(newly_crossed)
    set_bot_state("milestone_last_announced", str(highest))
    return {"threshold": highest, "total": total}

