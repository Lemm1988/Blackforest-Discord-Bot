"""Modul XP & Level — Schema, Achievements und Bedingungen an einer Stelle.

XP entsteht aus drei Quellen (Chat, Quizantworten, Zufallsereignisse), gehoert
aber diesem Modul und nicht dem Quiz: Wer nur im Chat schreibt, sammelt genauso.
"""
import achievements
import database
import module
import verdiente_rollen
from achievements import Achievement
from verdiente_rollen import Bedingungsart

NAME = "xp_level"

SCHEMA = (
    """-- XP/Level-System (Spec 1), bewusst getrennt von players.score (Quiz-Highscore).
-- Kein fester Levelaufstieg pro Zeile - level wird bei jedem award_xp()-Aufruf
-- aus xp neu berechnet und hier nur zur schnellen Anzeige mitgespeichert.
CREATE TABLE IF NOT EXISTS user_xp (
    discord_user_id TEXT PRIMARY KEY,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    updated_at TEXT
);""",
)


def _migrationen(conn):
    """Tageszaehler fuer das XP-Limit, nachtraeglich hinzugekommen."""
    database._ensure_column(conn, "user_xp", "daily_message_xp", "INTEGER DEFAULT 0")
    database._ensure_column(conn, "user_xp", "daily_xp_date", "TEXT")


def _xp(discord_user_id) -> int:
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT xp FROM user_xp WHERE discord_user_id = ?", (str(discord_user_id),)
        ).fetchone()
    return row["xp"] if row else 0


def _level(discord_user_id) -> int:
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT level FROM user_xp WHERE discord_user_id = ?", (str(discord_user_id),)
        ).fetchone()
    return row["level"] if row else 0


def _mindestens_level(stufe: int):
    return lambda kontext: kontext.get("neues_level", 0) >= stufe


for _a in (
    Achievement("LEVEL_10", "Aufsteiger", "Level 10 erreicht.",
                50, False, NAME, "level_up", _mindestens_level(10)),
    Achievement("LEVEL_25", "Veteran", "Level 25 erreicht.",
                150, False, NAME, "level_up", _mindestens_level(25)),
):
    achievements.REGISTRATUR.eintragen(_a)


for _b in (
    Bedingungsart("level", "Erreichtes Level", "Level",
                  "Level ergibt sich aus den gesammelten XP (Chat, Quiz, Zufallsereignisse).",
                  NAME, _level),
    Bedingungsart("xp", "Gesammelte XP", "XP",
                  "Alle XP zusammen, unabh\u00e4ngig davon, woher sie stammen.",
                  NAME, _xp),
):
    verdiente_rollen.REGISTRATUR.eintragen(_b)


module.REGISTRATUR.eintragen(module.Modul(
    name=NAME,
    label="XP & Level",
    schema=SCHEMA,
    migrationen=(_migrationen,),
    # "level_up" liefert database.award_xp() selbst mit (new_level) - dieses
    # Modul muss dafuer nichts nachschlagen.
    ereignis_kontext={},
))
