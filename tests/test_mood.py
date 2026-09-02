"""Tests fuer die Stimmungs-Logik.

Schwerpunkt liegt auf dem Rueckfallverhalten: Die Texte sind ueber die
Backend-Seite /personality/texts frei editierbar, ein leerer Pool ist also ein
realistischer Betriebszustand und darf den Bot nicht aus dem Tritt bringen.
"""
from __future__ import annotations

import mood


def test_status_text_comes_from_the_matching_mood():
    pool = {
        "bored": [("", "🥱 Hier ist gerade wenig los")],
        "neutral": [("", "🧠 Ich denke nach...")],
    }
    assert mood.pick_status_text(pool, "bored") == "🥱 Hier ist gerade wenig los"


def test_status_text_falls_back_to_neutral_for_unknown_mood():
    """Fehlen fuer eine Stimmung Texte, greift neutral - besser ein passender
    Allgemeinplatz als gar keine Anzeige."""
    pool = {"neutral": [("", "🧠 Ich denke nach...")]}
    assert mood.pick_status_text(pool, "annoyed") == "🧠 Ich denke nach..."


def test_status_text_survives_a_completely_empty_pool():
    """Wenn jemand alle Texte loescht, darf der Bot nicht abstuerzen."""
    assert mood.pick_status_text({}, "bored") == "🌿 Blackforest Bot"


def test_response_frequency_bounds_are_exact():
    """0 heisst nie, 100 heisst immer - an den Raendern darf nichts 'ungefaehr' sein,
    sonst antwortet ein auf 0 gestellter Bot doch gelegentlich."""
    assert mood.roll_personality_response(0) is False
    assert mood.roll_personality_response(100) is True


def test_night_hours_wrap_around_midnight():
    """Die Nachtspanne laeuft ueber Mitternacht (z.B. 0-7 Uhr, aber auch 22-6):
    ein naiver Vergleich start <= h < ende waere dabei falsch."""
    assert mood.is_night_hour(23, night_start_hour=22, night_end_hour=6) is True
    assert mood.is_night_hour(3, night_start_hour=22, night_end_hour=6) is True
    assert mood.is_night_hour(12, night_start_hour=22, night_end_hour=6) is False
