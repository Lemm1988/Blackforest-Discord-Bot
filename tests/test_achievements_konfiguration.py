"""Achievements sind einstellbar statt einprogrammiert.

Hintergrund (Patrick, 05.09.2026): "archievments sollen einstellbar werden.
wann man welches archievment bekommt" und "im adminbereich unter level und Xp
soll man archievents hinzufuegen und entfernen koennen."

Vorher steckte die Schwelle in einer Lambda-Funktion im Modulcode:
`_mindestens_richtig(10)`. Fuer "ab wie vielen Fragen gibt es das?" brauchte es
einen Codeeintrag und einen Neustart.

Jetzt ist die Bedingung zweigeteilt: Die ART steht im Code (sie braucht Wissen
darueber, welche Zahl ein Ereignis ueberhaupt mitliefert), der WERT steht in der
Datenbank. Diese Datei sichert beide Haelften ab - besonders die Faelle, in
denen eine im Backend eingetippte Zahl unsinnig ist. Ein Achievement, das nach
einem Umbau nicht mehr faellt, faellt niemandem auf: Es fehlt einfach.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

import achievements  # noqa: E402
import module  # noqa: E402

module.laden()


# --------------------------------------------------------------------------
# Die Bedingungsarten selbst
# --------------------------------------------------------------------------

SCHWELLE = achievements.Bedingungstyp(
    "test_schwelle", "Testschwelle", "test", "test_ereignis", "zahl", einheit="Stueck")
SPANNE = achievements.Bedingungstyp(
    "test_spanne", "Testspanne", "test", "test_ereignis", "stunde", spanne=True)


@pytest.mark.parametrize("vorhanden, schwelle, erwartet", [
    (0, 1, False), (1, 1, True), (9, 10, False), (10, 10, True), (100, 10, True),
])
def test_schwelle_greift_ab_dem_wert_einschliesslich(vorhanden, schwelle, erwartet):
    """">=" und nicht ">": Wer die zehnte Frage richtig hat, hat zehn - genau
    so verhielt sich die alte Lambda-Fassung auch."""
    assert SCHWELLE.trifft_zu({"zahl": vorhanden}, schwelle) is erwartet


@pytest.mark.parametrize("stunde, erwartet", [
    (0, True), (3, True), (4, False), (5, False), (23, False),
])
def test_spanne_ist_unten_einschliesslich_und_oben_ausschliesslich(stunde, erwartet):
    """0 bis 4 meint 0:00 bis 3:59. Genau die Grenzen der alten Fassung -
    4 Uhr gehoerte schon damals bewusst nicht mehr dazu."""
    assert SPANNE.trifft_zu({"stunde": stunde}, 0, 4) is erwartet


def test_fehlende_zahl_trifft_nie_zu():
    """Lieber ein Achievement zu spaet als eines, das jemand ohne Zutun
    bekommt. Ein None wuerde beim Vergleich sonst zur Ausnahme fuehren."""
    assert SCHWELLE.trifft_zu({}, 1) is False
    assert SPANNE.trifft_zu({}, 0, 4) is False


@pytest.mark.parametrize("wert, wert_bis", [
    ("keine zahl", None), (None, None), ("", None),
])
def test_unbrauchbarer_wert_stuerzt_nicht_ab(wert, wert_bis):
    """Ein kaputter Wert in der Datenbank darf nicht die gesamte
    Achievement-Pruefung mitreissen - sie laeuft bei JEDER richtigen
    Quizantwort."""
    assert SCHWELLE.trifft_zu({"zahl": 5}, wert, wert_bis) is False


def test_spanne_ohne_obergrenze_trifft_nicht_zu():
    assert SPANNE.trifft_zu({"stunde": 2}, 0, None) is False


# --------------------------------------------------------------------------
# Auswertung ueber Regeln aus der Datenbank
# --------------------------------------------------------------------------

REGELN = [
    {"id": "A", "ereignis": "quiz_correct", "bedingung_typ": "quiz_richtig_gesamt",
     "wert": 10, "wert_bis": None, "aktiv": 1},
    {"id": "B", "ereignis": "quiz_correct", "bedingung_typ": "quiz_uhrzeit",
     "wert": 0, "wert_bis": 4, "aktiv": 1},
    {"id": "AUS", "ereignis": "quiz_correct", "bedingung_typ": "quiz_richtig_gesamt",
     "wert": 1, "wert_bis": None, "aktiv": 0},
    {"id": "ANDERES", "ereignis": "level_up", "bedingung_typ": "level_erreicht",
     "wert": 10, "wert_bis": None, "aktiv": 1},
]


def test_regeln_werden_wie_die_moduldefinitionen_ausgewertet():
    treffer = achievements.kandidaten_aus_regeln(
        "quiz_correct", {"richtige_antworten": 10, "stunde": 2}, REGELN)
    assert treffer == ["A", "B"]


def test_deaktivierte_regeln_fallen_nicht():
    """Der Sinn des Schalters: Ein Achievement abschalten, ohne es zu loeschen -
    wer es hat, behaelt es."""
    treffer = achievements.kandidaten_aus_regeln(
        "quiz_correct", {"richtige_antworten": 500}, REGELN)
    assert "AUS" not in treffer


def test_fremde_ereignisse_bleiben_unberuehrt():
    treffer = achievements.kandidaten_aus_regeln(
        "quiz_correct", {"richtige_antworten": 500, "neues_level": 99}, REGELN)
    assert "ANDERES" not in treffer


def test_unbekannte_bedingungsart_faellt_nie():
    """Wichtig fuer die Modultrennung: Wer das Quiz-Modul deinstalliert, dessen
    Datenbank enthaelt weiterhin Quiz-Achievements. Die duerfen dann schlicht
    nie fallen, statt einen Fehler zu werfen."""
    regeln = [{"id": "X", "ereignis": "quiz_correct", "bedingung_typ": "gibtsnicht",
               "wert": 1, "wert_bis": None, "aktiv": 1}]
    assert achievements.kandidaten_aus_regeln("quiz_correct", {"richtige_antworten": 99}, regeln) == []


# --------------------------------------------------------------------------
# Die Arten, die die Module mitbringen
# --------------------------------------------------------------------------

def test_jede_bedingungsart_gehoert_einem_modul():
    for art in achievements.BEDINGUNGEN.alle():
        assert art.modul, f"{art.schluessel} gehoert keinem Modul an."
        assert art.ereignis, f"{art.schluessel} haengt an keinem Ereignis."
        assert art.kontextfeld, f"{art.schluessel} betrachtet keine Zahl."


def test_jedes_achievement_nutzt_eine_bekannte_art():
    """Sonst faellt es nie - und niemand koennte sehen, warum."""
    for a in achievements.REGISTRATUR.alle():
        art = achievements.BEDINGUNGEN.holen(a.bedingung_typ)
        assert art is not None, f"{a.id} nutzt die unbekannte Bedingung {a.bedingung_typ!r}."
        assert art.ereignis == a.ereignis, (
            f"{a.id} haengt an {a.ereignis!r}, seine Bedingung aber an {art.ereignis!r}."
        )


def test_art_und_achievement_gehoeren_zum_selben_modul():
    """Sonst waere die Modultrennung nur auf dem Papier vorhanden."""
    for a in achievements.REGISTRATUR.alle():
        art = achievements.BEDINGUNGEN.holen(a.bedingung_typ)
        assert art.modul == a.modul, (
            f"{a.id} liegt im Modul {a.modul}, seine Bedingung im Modul {art.modul}."
        )


def test_spannen_haben_eine_obergrenze():
    for a in achievements.REGISTRATUR.alle():
        art = achievements.BEDINGUNGEN.holen(a.bedingung_typ)
        if art.spanne:
            assert a.wert_bis is not None and a.wert_bis > a.wert, (
                f"{a.id} ist eine Spanne ohne brauchbare Obergrenze."
            )


# --------------------------------------------------------------------------
# Backend: anlegen, aendern, entfernen
# --------------------------------------------------------------------------

APP = (REPO / "backend" / "app.py").read_text()


@pytest.mark.parametrize("route", [
    "achievements_config_page", "achievements_config_save",
    "achievements_config_create", "achievements_config_delete",
])
def test_es_gibt_die_routen(route):
    assert f"def {route}(" in APP


@pytest.mark.parametrize("route", [
    "achievements_config_page", "achievements_config_save",
    "achievements_config_create", "achievements_config_delete",
])
def test_einstellen_verlangt_die_berechtigung(route):
    """Vorgabe Patrick: "einstellungen soll ein user nicht sehen koennen."""
    anfang = APP.index(f"def {route}(")
    kopf = APP[max(0, anfang - 300):anfang]
    assert 'require_permission("manage_game_settings")' in kopf


def test_nur_installierte_module_stehen_zur_auswahl():
    """Ohne Quiz-Modul soll keine Quiz-Bedingung im Formular stehen - sonst
    liesse sich ein Achievement anlegen, das nie faellt."""
    assert "module.INSTALLIERT" in APP[APP.index("def _bedingungsarten"):APP.index("def achievements_config_page")]


def test_meine_achievements_zeigt_nur_freigeschaltete():
    """Vorgabe Patrick: "Meine archievments sollte dem User nur seine
    archievments anzeigen."""
    anfang = APP.index("def achievements_page(")
    ausschnitt = APP[anfang:anfang + 1200]
    assert 'if e["unlocked"]' in ausschnitt


def test_die_mitgliederseite_zeigt_kein_admin_untermenue():
    vorlage = (REPO / "backend" / "templates" / "achievements.html").read_text()
    assert "xp_level_subnav" not in vorlage


DB = (REPO / "bot" / "database.py").read_text()


def test_nur_selbst_angelegte_lassen_sich_loeschen():
    """Ein Achievement aus dem Modulcode kaeme beim naechsten Start ohnehin
    wieder - das Loeschen saehe aus wie ein Fehler."""
    anfang = DB.index("def delete_achievement(")
    ausschnitt = DB[anfang:anfang + 900]
    assert 'row["eigenbau"]' in ausschnitt and "return False" in ausschnitt


def test_modul_ereignis_und_art_sind_nicht_aenderbar():
    """Wer sie aendern koennte, koennte ein Quiz-Achievement an ein
    Level-Ereignis haengen, wo es nie faellt."""
    anfang = DB.index("def save_achievement(")
    ausschnitt = DB[anfang:DB.index("def create_achievement(")]
    for feld in ("modul =", "ereignis =", "bedingung_typ ="):
        assert feld not in ausschnitt, f"save_achievement() aendert {feld.strip(' =')}"


def test_die_pruefung_liest_aus_der_datenbank():
    """Sonst waere alles Einstellen wirkungslos - der Bot wuerde weiter die
    einprogrammierten Schwellen benutzen."""
    anfang = DB.index("def check_and_unlock_achievements(")
    ausschnitt = DB[anfang:anfang + 3000]
    assert "get_achievement_rules()" in ausschnitt
    assert "REGISTRATUR.kandidaten(" not in ausschnitt


def test_saatgut_ueberschreibt_geaenderte_schwellen_nicht():
    """INSERT OR IGNORE plus ein UPDATE, das ausdruecklich nur leere
    Bedingungsfelder fuellt. Ohne die Einschraenkung spraenge eine im Backend
    geaenderte Schwelle bei jedem Neustart zurueck."""
    anfang = DB.index("def _seed_achievements") if "def _seed_achievements" in DB else DB.index("INSERT OR IGNORE INTO achievements")
    ausschnitt = DB[anfang:anfang + 2000]
    assert "bedingung_typ IS NULL" in ausschnitt
