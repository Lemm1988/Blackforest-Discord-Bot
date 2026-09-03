"""Achievements: Modultrennung und Ausloesebedingungen.

Hintergrund (Vorgabe Patrick, 03.09.2026): Die Funktionsbereiche des Bots sollen
sich einzeln installieren lassen, weil einzelne Module spaeter kostenpflichtig
angeboten werden sollen. Bis dahin standen alle acht Achievements samt ihren
Bedingungen zusammen in database.py - wer das Quiz-Modul entfernt haette, haette
dort sechs tote Definitionen und eine Bedingung zurueckgelassen, die auf eine
Quiz-Statistik zugreift, die es nicht mehr gibt.

Zwei Dinge werden hier abgesichert:

1. Die Trennung selbst - dass jedes Achievement genau einem Modul gehoert und
   database.py keinen einzigen Namen mehr kennt.
2. Dass die Bedingungen nach dem Umbau exakt dasselbe tun wie vorher. Ein
   verschobenes Achievement, das nicht mehr faellt, faellt niemandem auf - es
   fehlt einfach.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

import achievements  # noqa: E402

achievements.lade_module("quiz", "xp_level")
R = achievements.REGISTRATUR


# --------------------------------------------------------------------------
# Die Trennung
# --------------------------------------------------------------------------

def test_es_gibt_genau_zwei_module():
    assert R.module() == ["quiz", "xp_level"]


def test_kein_achievement_ohne_modul():
    for a in R.alle():
        assert a.modul, f"{a.id} gehoert keinem Modul an."


def test_quiz_achievements_haengen_nur_an_quiz_ereignissen():
    """Gegenprobe zur Zuordnung: Ein Achievement im Quiz-Modul, das auf
    level_up reagiert, waere falsch einsortiert - Level erreicht man auch
    ohne eine einzige Quizfrage."""
    for a in R.modul("quiz"):
        assert a.ereignis in ("quiz_correct", "question_submitted"), (
            f"{a.id} liegt im Quiz-Modul, reagiert aber auf {a.ereignis!r}."
        )


def test_xp_achievements_haengen_am_levelaufstieg():
    for a in R.modul("xp_level"):
        assert a.ereignis == "level_up", (
            f"{a.id} liegt im Modul XP & Level, reagiert aber auf {a.ereignis!r}."
        )


def test_database_kennt_keine_achievement_namen_mehr():
    """Der eigentliche Punkt des Umbaus. Steht hier wieder eine ID im Code, ist
    die Trennung durchbrochen und ein entferntes Modul liesse Reste zurueck."""
    quelle = (REPO / "bot" / "database.py").read_text()
    for a in R.alle():
        assert a.id not in quelle, (
            f"database.py nennt {a.id} beim Namen - die Definition gehoert ins Modul."
        )


def test_ids_sind_eindeutig_ueber_module_hinweg():
    """Die IDs stehen so in der Datenbank. Zwei Module mit derselben ID wuerden
    sich gegenseitig ueberschreiben - deshalb faellt das beim Eintragen sofort auf."""
    ids = [a.id for a in R.alle()]
    assert len(ids) == len(set(ids))

    doppelt = achievements.Achievement(
        id="FIRST_QUIZ", name="Kopie", beschreibung="", xp_belohnung=0,
        geheim=False, modul="fremdmodul", ereignis="quiz_correct",
        bedingung=lambda k: True,
    )
    with pytest.raises(ValueError, match="doppelt"):
        R.eintragen(doppelt)


# --------------------------------------------------------------------------
# Die Bedingungen - muessen sich exakt wie vor dem Umbau verhalten
# --------------------------------------------------------------------------

@pytest.mark.parametrize("anzahl, erwartet", [
    (0, []),
    (1, ["FIRST_QUIZ"]),
    (9, ["FIRST_QUIZ"]),
    (10, ["FIRST_QUIZ", "QUIZ_10"]),
    (99, ["FIRST_QUIZ", "QUIZ_10"]),
    (100, ["FIRST_QUIZ", "QUIZ_10", "QUIZ_100"]),
])
def test_quizschwellen(anzahl, erwartet):
    """Die Schwellen greifen kumulativ - wer die 100. Frage richtig hat, erfuellt
    auch die Bedingungen fuer 1 und 10. Doppelte Freischaltung verhindert die
    Datenbank ueber ihren UNIQUE-Constraint, nicht diese Bedingungen."""
    assert R.kandidaten("quiz_correct", {"richtige_antworten": anzahl}) == erwartet


@pytest.mark.parametrize("stunde, erwartet", [
    (0, "NIGHT_OWL"), (3, "NIGHT_OWL"), (4, None),
    (5, "EARLY_BIRD"), (6, "EARLY_BIRD"), (7, None),
    (12, None), (23, None),
])
def test_uhrzeit_achievements(stunde, erwartet):
    """Die Grenzen sind genau die der alten Implementierung: 0<=h<4 und 5<=h<7.
    4 Uhr und 7 Uhr gehoeren bewusst zu keinem von beiden."""
    treffer = R.kandidaten("quiz_correct", {"richtige_antworten": 5, "stunde": stunde})
    zeit = [t for t in treffer if t in ("NIGHT_OWL", "EARLY_BIRD")]
    assert zeit == ([erwartet] if erwartet else [])


def test_ohne_uhrzeit_kein_zeit_achievement():
    """Fehlt die Stunde, darf nichts fallen. Ein None wuerde bei einem Vergleich
    sonst zur Ausnahme fuehren oder - schlimmer - versehentlich zutreffen."""
    treffer = R.kandidaten("quiz_correct", {"richtige_antworten": 5})
    assert "NIGHT_OWL" not in treffer and "EARLY_BIRD" not in treffer


@pytest.mark.parametrize("level, erwartet", [
    (1, []), (9, []), (10, ["LEVEL_10"]),
    (24, ["LEVEL_10"]), (25, ["LEVEL_10", "LEVEL_25"]), (99, ["LEVEL_10", "LEVEL_25"]),
])
def test_levelschwellen(level, erwartet):
    assert R.kandidaten("level_up", {"neues_level": level}) == erwartet


@pytest.mark.parametrize("anzahl, erwartet", [(0, []), (1, ["COMMUNITY_MEMBER"]), (7, ["COMMUNITY_MEMBER"])])
def test_eingereichte_frage(anzahl, erwartet):
    assert R.kandidaten("question_submitted", {"eingereichte_fragen": anzahl}) == erwartet


def test_ereignisse_greifen_nicht_ineinander():
    """Ein Levelaufstieg darf kein Quiz-Achievement ausloesen und umgekehrt -
    sonst waere die Modultrennung nur auf dem Papier vorhanden."""
    assert R.kandidaten("level_up", {"richtige_antworten": 100, "neues_level": 0}) == []
    assert R.kandidaten("quiz_correct", {"neues_level": 25, "richtige_antworten": 0}) == []


def test_unbekanntes_ereignis_loest_nichts_aus():
    assert R.kandidaten("gibtsnicht", {"richtige_antworten": 100, "neues_level": 99}) == []


# --------------------------------------------------------------------------
# Die Bedingungen duerfen nichts veraendern
# --------------------------------------------------------------------------

def test_bedingungen_sind_reine_funktionen():
    """Vorher zaehlte die Quiz-Bedingung als Nebenwirkung einen Zaehler in der
    Datenbank hoch - eine Pruefung veraenderte also Daten. Jetzt bekommt sie die
    Zahl mitgegeben. Der Kontext darf dabei unangetastet bleiben, sonst wirken
    sich mehrere Aufrufe gegenseitig aus."""
    kontext = {"richtige_antworten": 10, "stunde": 3}
    kopie = dict(kontext)
    R.kandidaten("quiz_correct", kontext)
    R.kandidaten("quiz_correct", kontext)
    assert kontext == kopie, "Eine Bedingung hat den Kontext veraendert."


def test_mehrfache_auswertung_liefert_dasselbe():
    kontext = {"neues_level": 25}
    assert R.kandidaten("level_up", kontext) == R.kandidaten("level_up", kontext)


# --------------------------------------------------------------------------
# Vollstaendigkeit gegenueber dem Stand vor dem Umbau
# --------------------------------------------------------------------------

VORHER = {
    "FIRST_QUIZ": ("Erste Schritte", 20, False),
    "QUIZ_10": ("Wissbegierig", 50, False),
    "QUIZ_100": ("Pflanzenexperte", 100, False),
    "LEVEL_10": ("Aufsteiger", 50, False),
    "LEVEL_25": ("Veteran", 150, False),
    "NIGHT_OWL": ("Nachteule", 30, True),
    "EARLY_BIRD": ("Frühaufsteher", 30, True),
    "COMMUNITY_MEMBER": ("Community-Mitglied", 20, False),
}


def test_kein_achievement_ist_beim_umbau_verlorengegangen():
    assert {a.id for a in R.alle()} == set(VORHER)


@pytest.mark.parametrize("ach_id", sorted(VORHER))
def test_name_belohnung_und_geheimhaltung_unveraendert(ach_id):
    """Ein stillschweigend geaenderter XP-Wert waere eine Regression, die
    niemand bemerkt - ausser dem, der ploetzlich weniger bekommt."""
    a = next(x for x in R.alle() if x.id == ach_id)
    name, xp, geheim = VORHER[ach_id]
    assert (a.name, a.xp_belohnung, a.geheim) == (name, xp, geheim)
