"""Modul Pflanzenquiz — Schema, Achievements und Bedingungen an einer Stelle.

Alles, was nur das Quiz betrifft, steht hier. Wird das Modul nicht geladen,
fehlen seine Tabellen nicht nur in der Auswahl, sie werden gar nicht erst
angelegt; und keine Achievement-Prüfung greift mehr auf sie zu.

Umgekehrt gilt: Kommt das Modul später dazu, legt es seine Tabellen beim
nächsten Start selbst an (module.Modulregistratur.schema_anwenden).
"""
import achievements
import database
import module
import verdiente_rollen
from achievements import Achievement
from verdiente_rollen import Bedingungsart

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
    database._ensure_column(
        conn, "questions", "category_id",
        "INTEGER REFERENCES categories(id) ON DELETE SET NULL")
    database._ensure_column(conn, "user_stats", "current_streak", "INTEGER DEFAULT 0")
    database._ensure_column(conn, "user_stats", "best_streak", "INTEGER DEFAULT 0")
    database._ensure_column(conn, "user_stats", "message_count", "INTEGER DEFAULT 0")
    database._ensure_column(conn, "user_stats", "last_message_at", "TEXT")
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
    if spalte not in _ERLAUBTE_STATS:
        raise ValueError(f"Unbekannte user_stats-Spalte: {spalte}")
    uid = str(discord_user_id)
    with database.get_connection() as conn:
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
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT quiz_correct_count FROM user_stats WHERE discord_user_id = ?",
            (str(discord_user_id),),
        ).fetchone()
    return row["quiz_correct_count"] if row else 0


def _eingereichte_fragen(discord_user_id) -> int:
    with database.get_connection() as conn:
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
    Bedingungsart("fragen_eingereicht", "Eingereichte Quizfragen", "Fragen",
                  "Z\u00e4hlt eingereichte Fragen, auch noch nicht freigegebene.",
                  NAME, _eingereichte_fragen),
):
    verdiente_rollen.REGISTRATUR.eintragen(_b)


module.REGISTRATUR.eintragen(module.Modul(
    name=NAME,
    label="Pflanzenquiz",
    schema=SCHEMA,
    migrationen=(_migrationen,),
    ereignis_kontext={
        "quiz_correct": _kontext_quiz_richtig,
        "question_submitted": _kontext_frage_eingereicht,
    },
))
