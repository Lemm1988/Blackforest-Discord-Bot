"""Modul Pflanzenquiz — Schema, Achievements und Bedingungen an einer Stelle.

Alles, was nur das Quiz betrifft, steht hier. Wird das Modul nicht geladen,
fehlen seine Tabellen nicht nur in der Auswahl, sie werden gar nicht erst
angelegt; und keine Achievement-Prüfung greift mehr auf sie zu.

Umgekehrt gilt: Kommt das Modul später dazu, legt es seine Tabellen beim
nächsten Start selbst an (module.Modulregistratur.schema_anwenden).
"""
import time
from datetime import datetime, timezone
import achievements
import module
import schema_helfer
import verdiente_rollen
from achievements import Achievement
from verdiente_rollen import Bedingungsart



def _db():
    """Zugriff auf die Kern-Funktionen (get_setting, Achievement-Freischaltung).

    Erst beim Aufruf geholt, aus demselben Grund wie bei _conn(): Das Laden des
    Moduls darf database nicht voraussetzen.
    """
    import database

    return database

def _conn():
    """Datenbankverbindung, erst beim Aufruf geholt.

    database wird BEWUSST nicht auf oberster Ebene importiert: Beim blossen
    Laden dieses Moduls (Schema, Achievements, Bedingungen) darf es nicht
    gebraucht werden, sonst zieht es config und dotenv nach sich - daran ist die
    CI schon einmal gescheitert. Wenn diese Funktionen laufen, laeuft ohnehin
    der Bot.
    """
    from database import get_connection

    return get_connection()


NAME = "quiz"

# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    option_a TEXT NOT NULL,
    option_b TEXT NOT NULL,
    option_c TEXT NOT NULL,
    option_d TEXT NOT NULL,
    correct_option TEXT NOT NULL,
    submitted_by TEXT,
    timestamp TEXT DEFAULT (datetime('now')),
    verified INTEGER DEFAULT 0
);""",
    """CREATE TABLE IF NOT EXISTS players (
    user_id TEXT PRIMARY KEY,
    score INTEGER DEFAULT 0
);""",
    """CREATE TABLE IF NOT EXISTS answered_questions (
    user_id TEXT NOT NULL,
    question_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, question_id)
);""",
    """-- Quiz-Fragen-Kategorien, frei verwaltbar über das Backend.
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    emoji TEXT
);""",
    """-- Zaehler, die ausschliesslich fuer Achievement-Bedingungen gebraucht werden
-- (z.B. Anzahl richtig beantworteter Quizfragen insgesamt) - bewusst getrennt
-- von players.score (Punktestand) und user_xp (XP/Level), da keines der beiden
-- zuverlaessig als Anzahl-richtiger-Antworten interpretierbar ist (Punkte pro
-- Frage sind konfigurierbar und koennen sich rueckwirkend nicht rekonstruieren
-- lassen; answered_questions zaehlt auch falsche Antworten mit).
CREATE TABLE IF NOT EXISTS user_stats (
    discord_user_id TEXT PRIMARY KEY,
    quiz_correct_count INTEGER DEFAULT 0
);""",
)


# --------------------------------------------------------------------------
# Migrationen
#
# Nachtraeglich hinzugekommene Spalten und Indizes. Sie stehen HIER und nicht in
# database.init_db(), weil sie sonst ohne dieses Modul auf Tabellen zugreifen
# wuerden, die es gar nicht gibt - genau daran ist der erste Probelauf einer
# Installation "nur Kern" gescheitert.
# --------------------------------------------------------------------------

def _migrationen(conn):
    schema_helfer.ensure_column(
        conn, "questions", "category_id",
        "INTEGER REFERENCES categories(id) ON DELETE SET NULL")
    schema_helfer.ensure_column(conn, "user_stats", "current_streak", "INTEGER DEFAULT 0")
    schema_helfer.ensure_column(conn, "user_stats", "best_streak", "INTEGER DEFAULT 0")
    schema_helfer.ensure_column(conn, "user_stats", "message_count", "INTEGER DEFAULT 0")
    schema_helfer.ensure_column(conn, "user_stats", "last_message_at", "TEXT")
    # Muss NACH der Spalte stehen, der Index setzt sie voraus.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_questions_category ON questions (category_id)")


# --------------------------------------------------------------------------
# Zahlen fuer Bedingungen und Achievements
# --------------------------------------------------------------------------

# Erlaubte Spalten in user_stats. Die Pruefung MUSS vor dem Einsetzen in SQL
# stehen - Spaltennamen lassen sich nicht parametrisieren.
_ERLAUBTE_STATS = ("quiz_correct_count",)


def _stat_erhoehen(discord_user_id, spalte: str, um: int = 1) -> int:
    """Erhoeht einen Zaehler in user_stats und gibt den NEUEN Stand zurueck."""
    import database
    if spalte not in _ERLAUBTE_STATS:
        raise ValueError(f"Unbekannte user_stats-Spalte: {spalte}")
    uid = str(discord_user_id)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO user_stats (discord_user_id, {0}) VALUES (?, ?) "
            "ON CONFLICT(discord_user_id) DO UPDATE SET {0} = {0} + excluded.{0}".format(spalte),
            (uid, um),
        )
        conn.commit()
        row = conn.execute(
            f"SELECT {spalte} FROM user_stats WHERE discord_user_id = ?", (uid,)
        ).fetchone()
        return row[spalte]


def _richtige_antworten(discord_user_id) -> int:
    import database
    with _conn() as conn:
        row = conn.execute(
            "SELECT quiz_correct_count FROM user_stats WHERE discord_user_id = ?",
            (str(discord_user_id),),
        ).fetchone()
    return row["quiz_correct_count"] if row else 0


def _beantwortete_fragen(discord_user_id) -> int:
    """Wie viele Quizfragen jemand insgesamt beantwortet hat - richtig UND falsch.

    Unterschied zu _richtige_antworten(): Hier zaehlt die Teilnahme, nicht das
    Ergebnis. Wer sich durch die Fragen arbeitet und dabei auch mal danebenliegt,
    soll ebenso vorankommen.

    Gezaehlt wird ueber answered_questions - dieselbe Tabelle, die verhindert,
    dass jemand dieselbe Frage zweimal bekommt. Der Hoechstwert entspricht also
    der Zahl der verifizierten Fragen.
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) c FROM answered_questions WHERE user_id = ?",
            (str(discord_user_id),),
        ).fetchone()
    return row["c"] if row else 0


def _eingereichte_fragen(discord_user_id) -> int:
    import database
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) c FROM questions WHERE submitted_by = ?",
            (str(discord_user_id),),
        ).fetchone()
    return row["c"] if row else 0


# --------------------------------------------------------------------------
# Ereignis-Kontext fuer die Achievements
#
# Stand bis zum 03.09.2026 als if/elif in database.py - mit
# increment_user_stat(uid, "quiz_correct_count") mitten drin. Ohne dieses Modul
# haette diese Zeile auf eine Tabelle gezeigt, die es gar nicht gibt.
# --------------------------------------------------------------------------

def _kontext_quiz_richtig(discord_user_id) -> dict:
    """Zaehlt hoch UND liefert den neuen Stand - der Zaehler gehoert zum
    Ereignis, nicht zur Bedingung. Deshalb hier und nicht in der reinen
    Bedingungslogik, die nichts veraendern darf."""
    return {"richtige_antworten": _stat_erhoehen(discord_user_id, "quiz_correct_count")}


def _kontext_frage_eingereicht(discord_user_id) -> dict:
    return {"eingereichte_fragen": _eingereichte_fragen(discord_user_id)}


# --------------------------------------------------------------------------
# Achievements
# --------------------------------------------------------------------------

def _mindestens_richtig(anzahl: int):
    return lambda kontext: kontext.get("richtige_antworten", 0) >= anzahl


def _in_stunden(von: int, bis: int):
    """Fehlt die Stunde im Kontext, trifft die Bedingung NICHT zu - lieber ein
    Achievement zu spaet als eines, das jemand ohne Zutun bekommt."""
    def pruefen(kontext):
        stunde = kontext.get("stunde")
        return stunde is not None and von <= stunde < bis
    return pruefen


for _a in (
    Achievement("FIRST_QUIZ", "Erste Schritte", "Deine erste Quizfrage richtig beantwortet.",
                20, False, NAME, "quiz_correct", _mindestens_richtig(1)),
    Achievement("QUIZ_10", "Wissbegierig", "10 Quizfragen insgesamt richtig beantwortet.",
                50, False, NAME, "quiz_correct", _mindestens_richtig(10)),
    Achievement("QUIZ_100", "Pflanzenexperte", "100 Quizfragen insgesamt richtig beantwortet.",
                100, False, NAME, "quiz_correct", _mindestens_richtig(100)),
    Achievement("NIGHT_OWL", "Nachteule",
                "Eine Quizfrage zwischen 0 und 4 Uhr nachts richtig beantwortet.",
                30, True, NAME, "quiz_correct", _in_stunden(0, 4)),
    Achievement("EARLY_BIRD", "Fr\u00fchaufsteher",
                "Eine Quizfrage zwischen 5 und 7 Uhr morgens richtig beantwortet.",
                30, True, NAME, "quiz_correct", _in_stunden(5, 7)),
    Achievement("COMMUNITY_MEMBER", "Community-Mitglied",
                "Deine erste eigene Quizfrage eingereicht.",
                20, False, NAME, "question_submitted",
                lambda kontext: kontext.get("eingereichte_fragen", 0) >= 1),
):
    achievements.REGISTRATUR.eintragen(_a)


# --------------------------------------------------------------------------
# Bedingungen fuer verdiente Rollen
# --------------------------------------------------------------------------

for _b in (
    Bedingungsart("quiz_richtig", "Richtig beantwortete Quizfragen", "Fragen",
                  "Z\u00e4hlt jede richtig beantwortete Quizfrage seit Beginn.",
                  NAME, _richtige_antworten),
    Bedingungsart("quiz_beantwortet", "Beantwortete Quizfragen", "Fragen",
                  "Z\u00e4hlt jede beantwortete Frage - richtig wie falsch. Belohnt "
                  "Teilnahme statt Treffsicherheit.",
                  NAME, _beantwortete_fragen),
    Bedingungsart("fragen_eingereicht", "Eingereichte Quizfragen", "Fragen",
                  "Z\u00e4hlt eingereichte Fragen, auch noch nicht freigegebene.",
                  NAME, _eingereichte_fragen),
):
    verdiente_rollen.REGISTRATUR.eintragen(_b)


def _uebersicht(discord_user_id) -> dict:
    """Der Beitrag dieses Moduls zur Mitglieder-Uebersicht.

    Fehlende Zeilen (jemand hat noch nie gespielt) ergeben saubere Nullwerte
    statt None - die Seite soll nicht jeden Wert einzeln absichern muessen.
    """
    uid = str(discord_user_id)
    with _conn() as conn:
        stats = conn.execute(
            "SELECT message_count, last_message_at, quiz_correct_count, "
            "current_streak, best_streak FROM user_stats WHERE discord_user_id = ?",
            (uid,),
        ).fetchone()
        score = conn.execute(
            "SELECT score FROM players WHERE user_id = ?", (uid,)
        ).fetchone()
    return {
        "message_count": stats["message_count"] if stats else 0,
        "last_message_at": stats["last_message_at"] if stats else None,
        "quiz_correct_count": stats["quiz_correct_count"] if stats else 0,
        "current_streak": stats["current_streak"] if stats else 0,
        "best_streak": stats["best_streak"] if stats else 0,
        "score": score["score"] if score else 0,
    }


module.REGISTRATUR.eintragen(module.Modul(
    name=NAME,
    label="Pflanzenquiz",
    schema=SCHEMA,
    migrationen=(_migrationen,),
    ereignis_kontext={
        "quiz_correct": _kontext_quiz_richtig,
        "question_submitted": _kontext_frage_eingereicht,
    },
    berechtigungen={
        # Wer eigene Quizfragen einreichen darf. Bis zum 03.09.2026 durfte das
        # jedes Servermitglied; seit der Rolle "Ideenschmied" ist es etwas, das
        # man sich verdient.
        "submit_questions": "Eigene Quizfragen einreichen",
    },
    nutzer_uebersicht=_uebersicht,
    uebersicht_standard={
        "message_count": 0, "last_message_at": None, "quiz_correct_count": 0,
        "current_streak": 0, "best_streak": 0, "score": 0,
    },
))


# --------------------------------------------------------------------------
# Abfragen und Aenderungen an den eigenen Tabellen
#
# Am 03.09.2026 aus database.py hierher gezogen. Vorher standen sie zentral -
# ein entferntes Modul haette dort 25 Funktionen zurueckgelassen, die auf
# Tabellen zugreifen, die es nicht mehr gibt.
#
# get_connection wird INNERHALB der Funktionen importiert: Beim blossen Laden
# des Moduls (Schema, Achievements, Bedingungen) darf database nicht gebraucht
# werden, sonst zieht es config und dotenv nach sich - daran ist die CI schon
# einmal gescheitert.
# --------------------------------------------------------------------------

def get_unanswered_question(user_id, category_id=None):
    """category_id=None bedeutet "egal welche Kategorie" (Option "Zufällige Kategorie")."""
    with _conn() as conn:
        if category_id is not None:
            return conn.execute(
                """SELECT * FROM questions
                   WHERE verified = 1 AND category_id = ?
                     AND id NOT IN (SELECT question_id FROM answered_questions WHERE user_id = ?)
                   ORDER BY RANDOM() LIMIT 1""",
                (category_id, str(user_id)),
            ).fetchone()
        return conn.execute(
            """SELECT * FROM questions
               WHERE verified = 1
                 AND id NOT IN (SELECT question_id FROM answered_questions WHERE user_id = ?)
               ORDER BY RANDOM() LIMIT 1""",
            (str(user_id),),
        ).fetchone()



def count_unanswered_for_user(user_id, category_id=None) -> int:
    """Wie viele verifizierte Fragen dieser User noch nicht beantwortet hat (für die
    Fortschrittsanzeige im Quiz-Embed: "Frage X von Y"), optional auf eine Kategorie
    eingeschränkt."""
    with _conn() as conn:
        if category_id is not None:
            return conn.execute(
                """SELECT COUNT(*) c FROM questions
                   WHERE verified = 1 AND category_id = ?
                     AND id NOT IN (SELECT question_id FROM answered_questions WHERE user_id = ?)""",
                (category_id, str(user_id)),
            ).fetchone()["c"]
        return conn.execute(
            """SELECT COUNT(*) c FROM questions
               WHERE verified = 1
                 AND id NOT IN (SELECT question_id FROM answered_questions WHERE user_id = ?)""",
            (str(user_id),),
        ).fetchone()["c"]



def mark_question_answered(user_id, question_id):
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO answered_questions (user_id, question_id) VALUES (?, ?)",
            (str(user_id), question_id),
        )
        conn.commit()



def add_points(user_id, points):
    with _conn() as conn:
        conn.execute("INSERT OR IGNORE INTO players (user_id, score) VALUES (?, 0)", (str(user_id),))
        conn.execute("UPDATE players SET score = score + ? WHERE user_id = ?", (points, str(user_id)))
        conn.commit()



def get_score(user_id) -> int:
    with _conn() as conn:
        row = conn.execute("SELECT score FROM players WHERE user_id = ?", (str(user_id),)).fetchone()
        return row["score"] if row else 0



def get_leaderboard(limit: int = 15):
    with _conn() as conn:
        return conn.execute(
            "SELECT user_id, score FROM players ORDER BY score DESC LIMIT ?", (limit,)
        ).fetchall()



def get_leaderboard_page(limit: int, offset: int):
    """Für die Backend-Seite /game: vollständige, paginierte Rangliste (nicht nur Top 15)."""
    with _conn() as conn:
        return conn.execute(
            "SELECT user_id, score FROM players ORDER BY score DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()



def count_players() -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]



def submit_question(question, option_a, option_b, option_c, option_d, correct_option, submitted_by, category_id=None):
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO questions
               (question, option_a, option_b, option_c, option_d, correct_option, submitted_by, verified, category_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (question, option_a, option_b, option_c, option_d, correct_option, str(submitted_by), category_id),
        )
        conn.commit()
        question_id = cur.lastrowid
    # Achievement-Pruefung zentral hier statt bei jedem Aufrufer (Web-Formular
    # /qinput, kuenftig evtl. auch ein Bot-Command) - siehe _db().check_and_unlock_achievements().
    _db().check_and_unlock_achievements(submitted_by, "question_submitted")
    return question_id



def get_questions_by_user(discord_user_id):
    with _conn() as conn:
        return conn.execute(
            "SELECT id, question FROM questions WHERE submitted_by = ? ORDER BY id DESC",
            (str(discord_user_id),),
        ).fetchall()



def get_question_stats():
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"]
        verified = conn.execute("SELECT COUNT(*) c FROM questions WHERE verified = 1").fetchone()["c"]
        unverified = total - verified
        counts = {}
        for opt in ("A", "B", "C", "D"):
            counts[opt] = conn.execute(
                "SELECT COUNT(*) c FROM questions WHERE correct_option = ?", (opt,)
            ).fetchone()["c"]
        return {
            "total": total,
            "verified": verified,
            "unverified": unverified,
            "counts": counts,
            "percentages": {k: (v / total * 100 if total else 0) for k, v in counts.items()},
        }



def get_questions(verified: bool, limit: int, offset: int):
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM questions WHERE verified = ? ORDER BY id LIMIT ? OFFSET ?",
            (1 if verified else 0, limit, offset),
        ).fetchall()



def count_questions(verified: bool, category_id=None) -> int:
    with _conn() as conn:
        if category_id is not None:
            return conn.execute(
                "SELECT COUNT(*) c FROM questions WHERE verified = ? AND category_id = ?",
                (1 if verified else 0, category_id),
            ).fetchone()["c"]
        return conn.execute(
            "SELECT COUNT(*) c FROM questions WHERE verified = ?", (1 if verified else 0,)
        ).fetchone()["c"]



def update_question(question_id, question, option_a, option_b, option_c, option_d, correct_option, verified, category_id=None):
    with _conn() as conn:
        conn.execute(
            """UPDATE questions SET question=?, option_a=?, option_b=?, option_c=?, option_d=?,
               correct_option=?, verified=?, category_id=? WHERE id=?""",
            (question, option_a, option_b, option_c, option_d, correct_option, int(verified), category_id, question_id),
        )
        conn.commit()



def get_categories():
    with _conn() as conn:
        return conn.execute("SELECT * FROM categories ORDER BY name").fetchall()



def get_category(category_id):
    with _conn() as conn:
        return conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()



def add_category(name: str, emoji: str = None):
    name = (name or "").strip()
    if not name:
        return
    with _conn() as conn:
        conn.execute("INSERT OR IGNORE INTO categories (name, emoji) VALUES (?, ?)", (name, emoji))
        conn.commit()



def update_category(category_id, name: str, emoji: str = None):
    with _conn() as conn:
        conn.execute(
            "UPDATE categories SET name = ?, emoji = ? WHERE id = ?",
            (name, emoji, category_id),
        )
        conn.commit()



def delete_category(category_id):
    """Löscht eine Kategorie. Die zugehörigen Fragen werden NICHT gelöscht, nur ihre
    Kategorie-Zuordnung entfernt (explizit hier UND per ON DELETE SET NULL in der
    Spaltendefinition abgesichert)."""
    with _conn() as conn:
        conn.execute("UPDATE questions SET category_id = NULL WHERE category_id = ?", (category_id,))
        conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        conn.commit()



def count_questions_by_category():
    """Für die Kategorien-Verwaltungsseite: wie viele Fragen je Kategorie zugeordnet sind."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT category_id, COUNT(*) c FROM questions GROUP BY category_id"
        ).fetchall()
        return {row["category_id"]: row["c"] for row in rows}



def delete_question(question_id):
    with _conn() as conn:
        conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))
        conn.commit()



def count_questions_by_user(discord_user_id) -> int:
    with _conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM questions WHERE submitted_by = ?", (str(discord_user_id),)
        ).fetchone()["c"]



def track_user_message(discord_user_id):
    """Erhoeht user_stats.message_count und setzt last_message_at auf jetzt
    (Spec 1, Phase B4, Ueberwachungs-Uebersicht: "wer ist wann zuletzt aktiv
    gewesen"). Getrennt von track_daily_active_user() (das ist nur ein
    Tages-Zaehler fuer Server-Rekorde) und increment_server_stat("messages_count")
    (das ist nur ein serverweiter Zaehler) - hier steht der EINZELNE User im
    Mittelpunkt, nicht der Server als Ganzes."""
    uid = str(discord_user_id)
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO user_stats (discord_user_id, message_count, last_message_at) VALUES (?, 1, ?) "
            "ON CONFLICT(discord_user_id) DO UPDATE SET "
            "message_count = message_count + 1, last_message_at = excluded.last_message_at",
            (uid, now),
        )
        conn.commit()



def get_total_quiz_correct_count() -> int:
    """Serverweite Gesamtzahl je richtig beantworteter Quizfragen - fuer die
    Server-Meilenstein-Erkennung. Aggregiert die pro-User-Zaehler aus
    user_stats (Phase 5), statt einen weiteren, redundanten Zaehler zu fuehren."""
    with _conn() as conn:
        row = conn.execute("SELECT COALESCE(SUM(quiz_correct_count), 0) AS total FROM user_stats").fetchone()
        return row["total"]



def update_quiz_streak(discord_user_id, correct: bool) -> dict:
    """Aktualisiert current_streak/best_streak in user_stats nach einer Quiz-
    Antwort. Gibt den neuen Zustand zurueck, inkl. ob/wie eine laufende Serie
    gerade zu Ende gegangen ist (fuer die Streak-Ended-Ankuendigung in
    cogs/quiz.py) - bei einer richtigen Antwort ist streak_ended immer False."""
    uid = str(discord_user_id)
    with _conn() as conn:
        row = conn.execute(
            "SELECT current_streak, best_streak FROM user_stats WHERE discord_user_id = ?", (uid,)
        ).fetchone()
        old_streak = row["current_streak"] if row else 0
        old_best = row["best_streak"] if row else 0

        new_streak = old_streak + 1 if correct else 0
        new_best = max(old_best, new_streak)

        conn.execute(
            "INSERT INTO user_stats (discord_user_id, current_streak, best_streak) VALUES (?, ?, ?) "
            "ON CONFLICT(discord_user_id) DO UPDATE SET current_streak = excluded.current_streak, "
            "best_streak = excluded.best_streak",
            (uid, new_streak, new_best),
        )
        conn.commit()

    return {
        "current_streak": new_streak,
        "best_streak": new_best,
        "new_best_streak": new_best > old_best,
        "streak_ended": (not correct) and old_streak > 0,
        "ended_streak_length": old_streak if (not correct and old_streak > 0) else 0,
    }
