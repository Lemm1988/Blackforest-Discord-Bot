"""Die Automod-Seite ist in Reiter geteilt.

Anlass (Plan 14, Teil 3): Die Seite trug vier Filter, ihre Einstellungen, die
Wortliste, das Zuruecksetzen UND die Verstosshistorie auf einer einzigen Seite.
Dass Patrick eine vorhandene Einstellung uebersehen hat - "wie lange zaehlt
jemand als neuer User" gab es laengst -, war dafuer ein gutes Indiz.

Diese Datei sichert, dass beim Umbau nichts verlorengegangen ist und dass jeder
Bereich genau einem Reiter zugeordnet bleibt.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SEITE = (REPO / "backend" / "templates" / "moderation.html").read_text(encoding="utf-8")

REITER = ("woerter", "links", "spam", "verstoesse")

#: Jede Karte, die es vor dem Umbau gab. Verschwindet eine, faellt es hier auf.
KARTEN = (
    "🤬 Schimpfwortfilter",
    "🚨 Spam-/Flutschutz",
    "🔗 Link-Filter",
    "Schimpfwortliste",
    "Verstöße zurücksetzen oder löschen",
    "Letzte Verstöße (alle Automod-Filter)",
)


@pytest.mark.parametrize("schluessel", REITER)
def test_jeder_reiter_hat_knopf_und_inhalt(schluessel):
    assert f'id="tab-{schluessel}"' in SEITE
    assert f'id="pane-{schluessel}"' in SEITE


@pytest.mark.parametrize("karte", KARTEN)
def test_keine_karte_ist_beim_umbau_verlorengegangen(karte):
    assert karte in SEITE


def test_genau_ein_reiter_ist_beim_oeffnen_aktiv():
    assert SEITE.count('class="nav-link active"') == 1
    assert SEITE.count("tab-pane fade show active") == 1


def test_die_verstoesse_liegen_zusammen():
    """Zuruecksetzen und Historie gehoeren nebeneinander - wer eine Zaehlung
    zurueckgesetzt hat, will danach sehen, was noch dasteht."""
    pane = SEITE[SEITE.index('id="pane-verstoesse"'):]
    pane = pane[:pane.index("</div>\n\n</div>")] if "</div>\n\n</div>" in pane else pane
    assert "Verstöße zurücksetzen" in pane and "Letzte Verstöße" in pane


def test_wortliste_und_wortfilter_liegen_zusammen():
    pane = SEITE[SEITE.index('id="pane-woerter"'):SEITE.index('id="pane-links"')]
    assert "🤬 Schimpfwortfilter" in pane and "Schimpfwortliste" in pane


def test_sperrliste_und_erlaubtliste_liegen_beim_linkfilter():
    pane = SEITE[SEITE.index('id="pane-links"'):SEITE.index('id="pane-spam"')]
    assert "Erlaubte Domains" in pane and "Gesperrte Domains" in pane


def test_der_zuletzt_geoeffnete_reiter_wird_gemerkt():
    """Jede Aenderung leitet auf diese Seite zurueck. Ohne das landet man nach
    dem Sperren einer Domain wieder beim Schimpfwortfilter."""
    assert "localStorage" in SEITE and "shown.bs.tab" in SEITE


def test_der_zugriff_auf_den_speicher_ist_abgesichert():
    """localStorage wirft in privaten Fenstern und bei blockierten Site-Daten.
    Ohne try/catch bliebe die ganze Seite ohne Javascript."""
    stelle = SEITE[SEITE.index("automod-reiter"):][:400]
    assert "try {" in stelle and "catch" in stelle


def test_jede_karte_liegt_in_genau_einem_reiter():
    """Sonst steht etwas doppelt oder ausserhalb der Reiter."""
    grenzen = [SEITE.index(f'id="pane-{s}"') for s in REITER] + [len(SEITE)]
    for karte in KARTEN:
        treffer = [i for i, (a, b) in enumerate(zip(grenzen, grenzen[1:]))
                   if karte in SEITE[a:b]]
        assert len(treffer) == 1, f"{karte} in {len(treffer)} Reitern"
