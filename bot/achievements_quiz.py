"""Achievements des Moduls Pflanzenquiz.

Gehoeren fachlich zum Quiz: Sie werden ausschliesslich durch Quizfragen
ausgeloest - richtig beantwortete Fragen, die Uhrzeit einer richtigen Antwort,
und das Einreichen einer eigenen Frage.

Faellt das Quiz-Modul weg, faellt diese Datei mit weg, und weder die
Definitionen noch die Bedingungen bleiben irgendwo zurueck.

Die Bedingungen sind reine Funktionen ueber den Kontext, den der Aufrufer
mitgibt (siehe achievements.py). Die noetigen Zahlen besorgt
database.check_and_unlock_achievements() - hier wird nichts nachgeschlagen und
nichts veraendert.
"""
from achievements import REGISTRATUR, Achievement

MODUL = "quiz"


def _mindestens_richtig(anzahl: int):
    """Bedingung: mindestens so viele Quizfragen insgesamt richtig beantwortet."""
    return lambda kontext: kontext.get("richtige_antworten", 0) >= anzahl


def _in_stunden(von: int, bis: int):
    """Bedingung: richtige Antwort zu einer Uhrzeit im Bereich [von, bis).

    Fehlt die Stunde im Kontext, trifft die Bedingung NICHT zu - lieber ein
    Achievement zu spaet als eines, das jemand ohne Zutun bekommt.
    """
    def pruefen(kontext):
        stunde = kontext.get("stunde")
        return stunde is not None and von <= stunde < bis
    return pruefen


for _achievement in (
    Achievement(
        id="FIRST_QUIZ", name="Erste Schritte",
        beschreibung="Deine erste Quizfrage richtig beantwortet.",
        xp_belohnung=20, geheim=False, modul=MODUL,
        ereignis="quiz_correct", bedingung=_mindestens_richtig(1),
    ),
    Achievement(
        id="QUIZ_10", name="Wissbegierig",
        beschreibung="10 Quizfragen insgesamt richtig beantwortet.",
        xp_belohnung=50, geheim=False, modul=MODUL,
        ereignis="quiz_correct", bedingung=_mindestens_richtig(10),
    ),
    Achievement(
        id="QUIZ_100", name="Pflanzenexperte",
        beschreibung="100 Quizfragen insgesamt richtig beantwortet.",
        xp_belohnung=100, geheim=False, modul=MODUL,
        ereignis="quiz_correct", bedingung=_mindestens_richtig(100),
    ),
    Achievement(
        id="NIGHT_OWL", name="Nachteule",
        beschreibung="Eine Quizfrage zwischen 0 und 4 Uhr nachts richtig beantwortet.",
        xp_belohnung=30, geheim=True, modul=MODUL,
        ereignis="quiz_correct", bedingung=_in_stunden(0, 4),
    ),
    Achievement(
        id="EARLY_BIRD", name="Frühaufsteher",
        beschreibung="Eine Quizfrage zwischen 5 und 7 Uhr morgens richtig beantwortet.",
        xp_belohnung=30, geheim=True, modul=MODUL,
        ereignis="quiz_correct", bedingung=_in_stunden(5, 7),
    ),
    Achievement(
        id="COMMUNITY_MEMBER", name="Community-Mitglied",
        beschreibung="Deine erste eigene Quizfrage eingereicht.",
        xp_belohnung=20, geheim=False, modul=MODUL,
        ereignis="question_submitted",
        bedingung=lambda kontext: kontext.get("eingereichte_fragen", 0) >= 1,
    ),
):
    REGISTRATUR.eintragen(_achievement)
