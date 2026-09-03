"""Bedingungen für verdiente Rollen, die zum Modul XP & Level gehören.

XP und Level entstehen aus drei Quellen - Chat, Quizantworten und
Zufallsereignisse -, gehören aber dem Modul XP & Level und nicht dem Quiz. Wer
nur im Chat schreibt, kann diese Bedingungen genauso erfüllen.
"""
import database
from verdiente_rollen import REGISTRATUR, Bedingungsart

MODUL = "xp_level"


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


for _art in (
    Bedingungsart(
        schluessel="level", label="Erreichtes Level", einheit="Level",
        hinweis="Level ergibt sich aus den gesammelten XP (Chat, Quiz, Zufallsereignisse).",
        modul=MODUL, wert=_level,
    ),
    Bedingungsart(
        schluessel="xp", label="Gesammelte XP", einheit="XP",
        hinweis="Alle XP zusammen, unabhängig davon, woher sie stammen.",
        modul=MODUL, wert=_xp,
    ),
):
    REGISTRATUR.eintragen(_art)
