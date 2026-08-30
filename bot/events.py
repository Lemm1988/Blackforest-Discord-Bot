"""Zufallsereignisse (Spec 1, Phase 10), bauen auf dem Mood-System (Phase 9)
auf. Reine, deterministisch-zufällige Funktionen ohne I/O, getrennt von der
Discord-/DB-Anbindung (siehe cogs/personality.py), damit isoliert testbar.
"""
import random

EVENT_TYPES = ("xpdrop", "quiz", "community", "humor")

# Mood-Einfluss auf die Basis-Wahrscheinlichkeit (Multiplikator). Bewusst NICHT
# als Setting exponiert - siehe Begründung wie bei den Mood-Erkennungsschwellen
# in Phase 9: nur Häufigkeit/Ort/Ton der AUSGABE ist konfigurierbar, nicht
# jedes interne Detail.
#
# excited/bored erhöhen die Wahrscheinlichkeit (der Bot ist "in Stimmung" bzw.
# will bei Langeweile aktiv gegensteuern), annoyed senkt sie (kein Spaß-
# programm, während gerade viele Regelverstöße passieren), sleepy/neutral/
# happy bleiben unverändert.
MOOD_MULTIPLIERS = {
    "excited": 1.5,
    "bored": 1.3,
    "annoyed": 0.5,
    "sleepy": 1.0,
    "neutral": 1.0,
    "happy": 1.0,
}


def apply_mood_multiplier(base_probability_percent, mood_key: str) -> float:
    multiplier = MOOD_MULTIPLIERS.get(mood_key, 1.0)
    return max(0.0, min(100.0, float(base_probability_percent) * multiplier))


def roll_event(probability_percent) -> bool:
    """True mit Wahrscheinlichkeit probability_percent% (0-100), Grenzfälle
    exakt (0 nie, 100 immer) - analog zu mood.roll_personality_response()."""
    probability_percent = max(0.0, min(100.0, float(probability_percent)))
    if probability_percent <= 0:
        return False
    if probability_percent >= 100:
        return True
    return random.uniform(0, 100) < probability_percent


def pick_event_type(weights: dict) -> str:
    """Gewichtete Zufallsauswahl eines Event-Typs. `weights`: {event_type:
    relatives_gewicht}, muss sich nicht zu 100 aufsummieren. Fällt auf eine
    gleichverteilte Auswahl zurück, falls alle Gewichte <= 0 sind (z.B. durch
    eine Fehlkonfiguration aller vier Settings auf 0)."""
    types = [t for t in EVENT_TYPES if weights.get(t, 0) > 0]
    if not types:
        return random.choice(EVENT_TYPES)
    total = sum(weights[t] for t in types)
    roll = random.uniform(0, total)
    cumulative = 0.0
    for t in types:
        cumulative += weights[t]
        if roll <= cumulative:
            return t
    return types[-1]
