"""Wohin der Bot postet, ist überall einstellbar.

Patrick am 07.09.2026, als Regel für alle künftigen Funktionen:

> „man soll immer überall einstellen können wohin der bot postet wenn er etwas
> Postet"

Anlass war der Rekord-Melder: Er lief bei jedem Herzschlag, also jede Minute,
und meldete jeden neuen Hoechststand sofort - an einem regen Tag eine
Nachrichtenflut. Ausserdem teilten sich fuenf verschiedene Ankuendigungsarten
einen einzigen Kanal, der ausgerechnet unter "Pflanzenquiz" eingestellt wurde.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

import scheduling  # noqa: E402

APP = (REPO / "backend" / "app.py").read_text(encoding="utf-8")
CONFIG = (REPO / "bot" / "config.py").read_text(encoding="utf-8")
ANSAGEN = (REPO / "bot" / "announcements.py").read_text(encoding="utf-8")
PERSONALITY = (REPO / "bot" / "cogs" / "personality.py").read_text(encoding="utf-8")
SEITE = (REPO / "backend" / "templates" / "kanaele.html").read_text(encoding="utf-8")

#: Jede Stelle, an der der Bot in einen Kanal schreibt.
KANAELE = (
    "announcement_channel_id", "levelup_channel_id", "achievement_channel_id",
    "record_channel_id", "milestone_channel_id", "streak_channel_id",
    "welcome_channel_id", "morning_greeting_channel_id",
    "personality_spontaneous_channel_id", "event_channel_id",
    "moderation_log_channel_id",
)


@pytest.mark.parametrize("schluessel", KANAELE)
def test_jeder_zielkanal_ist_einstellbar(schluessel):
    assert f'"{schluessel}"' in CONFIG
    assert schluessel in APP


@pytest.mark.parametrize("schluessel", KANAELE)
def test_jeder_zielkanal_steht_auf_der_uebersicht(schluessel):
    """Sie lagen ueber mehrere Seiten verteilt - der Ankuendigungskanal
    ausgerechnet unter Pflanzenquiz. Jetzt sieht man auf einen Blick, wo etwas
    landet."""
    assert schluessel in APP[APP.index("KANAL_EINSTELLUNGEN = ["):APP.index('@app.route("/kanaele")')]


def test_leer_heisst_nicht_posten():
    assert "— nicht posten —" in SEITE


def test_die_ankuendigungsarten_haben_eigene_kanaele():
    """Vorher teilten sich Level-Up, Achievement, Rekord, Meilenstein und
    Quiz-Serie einen einzigen Kanal."""
    for schluessel in ("levelup_channel_id", "achievement_channel_id",
                       "record_channel_id", "milestone_channel_id", "streak_channel_id"):
        assert schluessel in ANSAGEN


def test_ohne_eigenen_kanal_greift_der_allgemeine():
    """Sonst muesste man fuenf Felder ausfuellen, nur um den Zustand von vorher
    zu bekommen."""
    stelle = ANSAGEN[ANSAGEN.index("def _kanal_fuer"):][:1400]
    assert 'database.get_setting(\n            "announcement_channel_id"' in stelle


def test_level_up_verschluckt_die_achievements_nicht():
    """Beim ersten Anlauf stand hinter dem Level-Up-Kanal ein `return` - ohne
    Level-Up-Kanal waeren damit auch die Achievements ausgefallen."""
    stelle = ANSAGEN[ANSAGEN.index("async def announce_progress"):]
    stelle = stelle[:stelle.index("# --- Erweiterte Community")]
    assert "erfolgskanal" in stelle
    assert stelle.count("return") <= 2   # nur die beiden Vorpruefungen oben


# --- Die Rekordpruefung laeuft taeglich --------------------------------------


def test_die_rekordpruefung_laeuft_nicht_mehr_bei_jedem_herzschlag():
    stelle = PERSONALITY[PERSONALITY.index("async def _maybe_check_server_records"):]
    stelle = stelle[:stelle.index("async def _maybe_check_milestones")]
    assert "taeglich_faellig" in stelle
    assert "get_server_stat_today" not in stelle


def test_bewertet_wird_der_vortag():
    """Um null Uhr steht der heutige Zaehler noch auf null - der gestrige ist
    endgueltig."""
    stelle = PERSONALITY[PERSONALITY.index("async def _maybe_check_server_records"):]
    stelle = stelle[:stelle.index("async def _maybe_check_milestones")]
    assert "timedelta(days=1)" in stelle
    assert "get_server_stat_am" in stelle


def test_die_uhrzeit_und_das_nachlauffenster_sind_einstellbar():
    assert '"server_record_check_time": "00:00"' in CONFIG
    assert '"server_record_grace_minutes"' in CONFIG
    assert 'name="server_record_check_time"' in SEITE


def test_ein_verpasstes_fenster_wird_abgehakt():
    """Sonst laeuft die Pruefung spaeter fuer einen laengst vergangenen Tag."""
    stelle = PERSONALITY[PERSONALITY.index("async def _maybe_check_server_records"):]
    stelle = stelle[:stelle.index("async def _maybe_check_milestones")]
    assert "if tagesmerker:" in stelle


def test_der_tageshelfer_ist_nicht_mehr_nur_fuer_den_morgengruss():
    assert hasattr(scheduling, "taeglich_faellig")
    assert scheduling.morgengruss_faellig is scheduling.taeglich_faellig


def test_die_meldung_spricht_vom_vortag():
    """"Heute wurden 300 Nachrichten geschrieben" waere um null Uhr falsch."""
    assert "Gestern wurden" in ANSAGEN
