"""Achievements des Moduls XP & Level.

Gehoeren NICHT zum Quiz, auch wenn sie frueher in derselben Liste standen: Sie
haengen am Levelaufstieg, und XP entsteht aus drei Quellen - Chatnachrichten
(cogs/moderation.py), Quizantworten (cogs/quiz.py) und Zufallsereignissen
(cogs/personality.py). Wer nur im Chat schreibt und nie eine Quizfrage
beantwortet, kann diese Auszeichnungen trotzdem erreichen.
"""
from achievements import REGISTRATUR, Achievement

MODUL = "xp_level"


def _mindestens_level(stufe: int):
    return lambda kontext: kontext.get("neues_level", 0) >= stufe


for _achievement in (
    Achievement(
        id="LEVEL_10", name="Aufsteiger", beschreibung="Level 10 erreicht.",
        xp_belohnung=50, geheim=False, modul=MODUL,
        ereignis="level_up", bedingung=_mindestens_level(10),
    ),
    Achievement(
        id="LEVEL_25", name="Veteran", beschreibung="Level 25 erreicht.",
        xp_belohnung=150, geheim=False, modul=MODUL,
        ereignis="level_up", bedingung=_mindestens_level(25),
    ),
):
    REGISTRATUR.eintragen(_achievement)
