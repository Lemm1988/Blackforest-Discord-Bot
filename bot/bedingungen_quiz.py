"""Bedingungen für verdiente Rollen, die zum Modul Pflanzenquiz gehören.

Diese beiden Bedingungen ergeben nur einen Sinn, wenn das Quiz installiert ist -
sie stützen sich auf Zahlen, die ausschliesslich dort entstehen. Ohne das Modul
stehen sie im Backend gar nicht erst zur Auswahl, statt eine Regel zu erlauben,
die nie greift.

Die Abfragen stehen hier und nicht in database.py: Sie greifen auf Tabellen zu
(user_stats, questions), die es ohne das Quiz womöglich gar nicht gäbe.
"""
import database
from verdiente_rollen import REGISTRATUR, Bedingungsart

MODUL = "quiz"


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


for _art in (
    Bedingungsart(
        schluessel="quiz_richtig",
        label="Richtig beantwortete Quizfragen",
        einheit="Fragen",
        hinweis="Zählt jede richtig beantwortete Quizfrage seit Beginn.",
        modul=MODUL,
        wert=_richtige_antworten,
    ),
    Bedingungsart(
        schluessel="fragen_eingereicht",
        label="Eingereichte Quizfragen",
        einheit="Fragen",
        hinweis="Zählt eingereichte Fragen, auch noch nicht freigegebene.",
        modul=MODUL,
        wert=_eingereichte_fragen,
    ),
):
    REGISTRATUR.eintragen(_art)
