"""Zeitlogik der taeglichen Guten-Morgen-Nachricht.

Diese Logik hat eine unangenehme Eigenschaft: Ein Denkfehler zeigt sich erst am
naechsten Morgen, und dann entweder als ausgebliebener Gruss (faellt kaum auf)
oder als doppelter (faellt allen auf). Beides laesst sich nicht sinnvoll von Hand
testen, ohne einen Tag zu warten oder an der Systemuhr zu drehen - deshalb steht
die Entscheidung in bot/scheduling.py als reine Funktion und wird hier mit
gestellten Zeitpunkten durchgespielt.

Die Regeln stammen aus der Absprache mit Patrick vom 03.09.2026:
Uhrzeit einstellbar, Kanal einstellbar, unabhaengig von der Stimmung, und bei
einem verpassten Zeitfenster faellt der Gruss fuer den Tag aus statt nachmittags
herauszurutschen.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))

from scheduling import morgengruss_faellig, parse_uhrzeit  # noqa: E402


def pruefe(jetzt: str, *, uhrzeit="07:00", zuletzt=None, nachlauf=60):
    """Kleiner Aufrufhelfer - "2026-09-03 07:00" ist lesbarer als ein
    datetime-Konstruktor mitten im Test."""
    return morgengruss_faellig(
        jetzt=datetime.strptime(jetzt, "%Y-%m-%d %H:%M"),
        uhrzeit=uhrzeit,
        zuletzt_gegruesst=zuletzt,
        nachlauf_minuten=nachlauf,
    )


# --------------------------------------------------------------------------
# Der Normalfall
# --------------------------------------------------------------------------

def test_punkt_sieben_wird_gegruesst():
    senden, merker = pruefe("2026-09-03 07:00")
    assert senden is True
    assert merker == "2026-09-03"


def test_eine_minute_vorher_noch_nicht():
    senden, merker = pruefe("2026-09-03 06:59")
    assert senden is False
    assert merker == "", "Zu frueh darf den Tag nicht abhaken - sonst faellt der Gruss aus."


def test_mitten_im_nachlauffenster_wird_noch_gegruesst():
    """Der Herzschlag laeuft jede Minute, aber der Bot koennte um 07:00 gerade
    neu starten. Ohne Fenster waere der Gruss dann weg."""
    senden, _ = pruefe("2026-09-03 07:45")
    assert senden is True


def test_am_rand_des_fensters_noch_gegruesst():
    senden, _ = pruefe("2026-09-03 08:00", nachlauf=60)
    assert senden is True


def test_eine_minute_nach_dem_fenster_nicht_mehr():
    senden, merker = pruefe("2026-09-03 08:01", nachlauf=60)
    assert senden is False
    assert merker == "2026-09-03", (
        "Verpasstes Fenster muss den Tag abhaken, sonst koennte eine spaetere "
        "Aenderung der Uhrzeit denselben Tag nachtraeglich ausloesen."
    )


# --------------------------------------------------------------------------
# Kein zweiter Gruss am selben Tag - der Fall, der allen auffiele
# --------------------------------------------------------------------------

def test_nicht_zweimal_am_selben_tag():
    senden, _ = pruefe("2026-09-03 07:30", zuletzt="2026-09-03")
    assert senden is False


def test_neustart_um_0705_gruesst_nicht_erneut():
    """Der eigentliche Grund fuer den Tagesmerker: Restart=always startet den
    Bot nach einem Absturz sofort neu. Ohne Merker gruesst er dann wieder."""
    senden, _ = pruefe("2026-09-03 07:05", zuletzt="2026-09-03")
    assert senden is False


def test_am_naechsten_tag_wieder():
    senden, merker = pruefe("2026-09-04 07:00", zuletzt="2026-09-03")
    assert senden is True
    assert merker == "2026-09-04"


def test_nach_tagelanger_pause_wieder():
    senden, _ = pruefe("2026-09-20 07:00", zuletzt="2026-09-03")
    assert senden is True


# --------------------------------------------------------------------------
# Uhrzeiten am Rand
# --------------------------------------------------------------------------

@pytest.mark.parametrize("uhrzeit, jetzt, erwartet", [
    ("00:00", "2026-09-03 00:00", True),
    ("00:05", "2026-09-03 00:04", False),
    ("23:59", "2026-09-03 23:59", True),
    ("23:59", "2026-09-03 23:58", False),
])
def test_randuhrzeiten(uhrzeit, jetzt, erwartet):
    """Mitternacht und die letzte Minute des Tages - dort wuerde ein Vergleich
    ueber Zeitstempel statt ueber das Kalenderdatum stolpern."""
    senden, _ = pruefe(jetzt, uhrzeit=uhrzeit)
    assert senden is erwartet


def test_kurz_vor_mitternacht_haekt_den_richtigen_tag_ab():
    senden, merker = pruefe("2026-09-03 23:59", uhrzeit="23:59")
    assert senden is True
    assert merker == "2026-09-03", "Der Merker muss der lokale Kalendertag sein."


def test_nachlauf_null_erlaubt_nur_die_exakte_minute():
    assert pruefe("2026-09-03 07:00", nachlauf=0)[0] is True
    assert pruefe("2026-09-03 07:01", nachlauf=0)[0] is False


def test_negativer_nachlauf_wird_wie_null_behandelt():
    """Ein Vertipper im Backend darf nicht dazu fuehren, dass gar nichts mehr
    geht oder eine Ausnahme fliegt."""
    assert pruefe("2026-09-03 07:00", nachlauf=-30)[0] is True
    assert pruefe("2026-09-03 07:01", nachlauf=-30)[0] is False


# --------------------------------------------------------------------------
# Kaputte Eingaben aus dem Backend
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kaputt", ["", "   ", "7", "07-00", "25:00", "07:60", "-1:00", "abc", "07:xx", None])
def test_unbrauchbare_uhrzeit_laesst_den_gruss_ausfallen(kaputt):
    """Kein Absturz, kein Gruss, und der Tag wird NICHT abgehakt - sonst waere
    ein Tippfehler still und dauerhaft, statt beim naechsten Herzschlag nach
    einer Korrektur wieder zu greifen."""
    senden, merker = pruefe("2026-09-03 12:00", uhrzeit=kaputt)
    assert senden is False
    assert merker == ""


@pytest.mark.parametrize("gueltig, erwartet", [
    ("07:00", (7, 0)),
    ("7:5", (7, 5)),
    ("  08:30  ", (8, 30)),
    ("00:00", (0, 0)),
    ("23:59", (23, 59)),
])
def test_uhrzeit_einlesen(gueltig, erwartet):
    assert parse_uhrzeit(gueltig) == erwartet


@pytest.mark.parametrize("kaputt", ["24:00", "07:60", "-1:30", "07", "07:00:00", "", None])
def test_uhrzeit_einlesen_lehnt_unsinn_ab(kaputt):
    assert parse_uhrzeit(kaputt) is None
