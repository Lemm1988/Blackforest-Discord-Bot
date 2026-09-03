"""Verdiente Rollen (Plan 11, Teil C — umgesetzt 03.09.2026).

Patrick: „wenn der user der ideenschmied werden will 100 fragen beantwortet hat
sagt ihm der Bot bescheid und er kann die rolle vom bot erhalten aber man muss
das eben alles im backend einstellen können."

Zwei Dinge sind hier heikel, und beide fallen erst im Betrieb auf:

* **Zu früh vergeben.** Eine Rolle, die jemand nicht verdient hat, entwertet sie
  für alle anderen.
* **Zu oft ankündigen.** Der Auslöser feuert bei jeder Quizantwort. Ohne Merker
  bekäme jemand nach der 100. Frage bei jeder weiteren erneut dieselbe
  Ankündigung — derselbe Fehler, den der Tagesmerker beim Morgengruß verhindert.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

import module  # noqa: E402
import verdiente_rollen as vr  # noqa: E402

module.laden()

COG = REPO / "bot" / "cogs" / "verdiente_rollen.py"
DATABASE_PY = REPO / "bot" / "database.py"
APP_PY = REPO / "backend" / "app.py"


# --------------------------------------------------------------------------
# Die Bedingung
# --------------------------------------------------------------------------

@pytest.mark.parametrize("wert, erwartet", [(0, False), (99, False), (100, True), (101, True)])
def test_schwelle_greift_genau(wert, erwartet):
    """Genau 100 muss reichen - „ab 100" heißt einschließlich."""
    assert vr.erfuellt("quiz_richtig", 100, {"quiz_richtig": wert}) is erwartet


def test_fehlender_wert_gilt_nie_als_erfuellt():
    """Lieber eine Rolle zu spät anbieten als eine, die niemand verdient hat."""
    assert vr.erfuellt("quiz_richtig", 100, {}) is False
    assert vr.erfuellt("quiz_richtig", 100, {"quiz_richtig": None}) is False


def test_unbekannte_bedingungsart_gilt_nie_als_erfuellt():
    """Wird eine Art aus dem Code entfernt, bleiben ihre Regeln in der Datenbank
    stehen. Sie dürfen dann nichts mehr auslösen."""
    assert vr.erfuellt("gibtsnicht", 1, {"gibtsnicht": 999}) is False


def test_andere_werte_stoeren_nicht():
    """Eine Regel auf Quizfragen darf nicht durch hohe XP ausgelöst werden."""
    assert vr.erfuellt("quiz_richtig", 100, {"xp": 99999, "quiz_richtig": 3}) is False


@pytest.mark.parametrize("art", ["quiz_richtig", "level", "xp", "fragen_eingereicht", "tage_dabei"])
def test_alle_angebotenen_arten_funktionieren(art):
    """Was im Backend auswählbar ist, muss auch auswertbar sein - sonst legt man
    eine Regel an, die nie greift."""
    assert vr.ist_gueltige_art(art)
    assert vr.erfuellt(art, 5, {art: 5}) is True


def test_jede_art_hat_label_und_hinweis():
    """Im Backend steht nur der Schlüssel, wenn das Label fehlt - „quiz_richtig"
    sagt einem Admin wenig."""
    for schluessel, art in vr.BEDINGUNGSARTEN().items():
        assert art.label and art.einheit and art.hinweis, f"{schluessel} unvollständig"


# --------------------------------------------------------------------------
# Modultrennung (Vorgabe Patrick, 03.09.2026)
#
# „die einstellung quizfragen richtig beantwortet hat man nur wenn man auch das
# Quizmodul später installiert und muss mit dem Pflanzenquiz konform sein."
#
# Ohne diese Trennung könnte man ohne Quizmodul eine Regel auf Quizfragen
# anlegen - sie griffe nie, und niemand fände heraus warum.
# --------------------------------------------------------------------------

def test_jede_bedingung_gehoert_einem_modul():
    for schluessel, art in vr.BEDINGUNGSARTEN().items():
        assert art.modul, f"{schluessel} gehört keinem Modul an."


@pytest.mark.parametrize("schluessel, modul", [
    ("quiz_richtig", "quiz"),
    ("fragen_eingereicht", "quiz"),
    ("level", "xp_level"),
    ("xp", "xp_level"),
    ("tage_dabei", "kern"),
])
def test_bedingungen_sind_richtig_einsortiert(schluessel, modul):
    assert vr.BEDINGUNGSARTEN()[schluessel].modul == modul


def test_ohne_quizmodul_gibt_es_keine_quizbedingungen():
    """Der eigentliche Punkt: Eine frische Registratur, in der nur der Kern
    geladen ist, darf keine Quizbedingung kennen."""
    nur_kern = vr.Bedingungsregistratur()
    nur_kern.eintragen(vr.Bedingungsart(
        schluessel="tage_dabei", label="Tage", einheit="Tage", hinweis="x", modul="kern",
    ))
    assert "quiz_richtig" not in nur_kern.alle()
    assert nur_kern.holen("quiz_richtig") is None


def test_bedingungsabfragen_stehen_in_den_modulen():
    """Die Abfragen gehören in die Modul-Dateien. Stünden sie zentral in
    database.py, wäre user_stats dort fest verdrahtet - und ein entferntes
    Quizmodul liesse eine Abfrage auf eine Tabelle zurück, die es nicht gibt.

    Geprüft wird der Sammler: Es darf keine zentrale Funktion mehr geben, die
    die Werte ALLER Bedingungen kennt. Jede Bedingung bringt ihre eigene mit.
    """
    assert "werte_fuer_bedingungen" not in DATABASE_PY.read_text(), (
        "Der zentrale Werte-Sammler ist zurück - er kannte die Quiz-Tabellen."
    )
    quiz = (REPO / "bot" / "modul_quiz.py").read_text()
    assert "user_stats" in quiz and "questions" in quiz, (
        "Die Quiz-Abfragen stehen nicht im Quiz-Modul."
    )


def test_jede_bedingung_bringt_ihre_abfrage_selbst_mit():
    """Ausnahme ist tage_dabei - das weiss nur Discord, nicht die Datenbank."""
    for schluessel, art in vr.BEDINGUNGSARTEN().items():
        if schluessel == "tage_dabei":
            assert art.wert is None
        else:
            assert callable(art.wert), f"{schluessel} hat keine eigene Abfrage."


def test_doppelte_bedingung_faellt_sofort_auf():
    """Zwei Module mit demselben Schlüssel würden sich gegenseitig
    überschreiben - die Regeln in der Datenbank zeigten dann auf die falsche."""
    r = vr.Bedingungsregistratur()
    art = vr.Bedingungsart(schluessel="x", label="X", einheit="", hinweis="", modul="a")
    r.eintragen(art)
    with pytest.raises(ValueError, match="doppelt"):
        r.eintragen(vr.Bedingungsart(schluessel="x", label="X", einheit="", hinweis="", modul="b"))


def test_werte_sammeln_ueberlebt_eine_kaputte_abfrage():
    """Fehlt die Tabelle eines halb entfernten Moduls, darf das nicht alles
    mitreissen - die anderen Bedingungen sollen weiter funktionieren."""
    r = vr.Bedingungsregistratur()
    r.eintragen(vr.Bedingungsart("gut", "Gut", "", "", "a", wert=lambda uid: 42))
    r.eintragen(vr.Bedingungsart("kaputt", "Kaputt", "", "", "b",
                                 wert=lambda uid: (_ for _ in ()).throw(RuntimeError("keine Tabelle"))))
    werte = r.werte("123")
    assert werte == {"gut": 42}


# --------------------------------------------------------------------------
# Der Ankündigungstext
# --------------------------------------------------------------------------

def test_platzhalter_werden_ersetzt():
    text = vr.ankuendigung(
        "{mention}: {wert}/{schwelle} für {rolle}",
        mention="@Max", rolle="Ideenschmied", wert=100, schwelle=100,
    )
    assert text == "@Max: 100/100 für Ideenschmied"


@pytest.mark.parametrize("leer", ["", "   ", None])
def test_ohne_vorlage_gibt_es_einen_standardtext(leer):
    """Sonst stünde der Nutzer vor einer leeren Nachricht mit einem Knopf und
    wüsste nicht, worum es geht."""
    text = vr.ankuendigung(leer, mention="@Max", rolle="Ideenschmied", wert=100, schwelle=100)
    assert "Ideenschmied" in text and "@Max" in text


def test_unbekannte_platzhalter_bleiben_stehen():
    """Ein Tippfehler im Text soll sichtbar sein, nicht stillschweigend
    verschwinden."""
    text = vr.ankuendigung("{mention} {quatsch}", mention="@Max", rolle="R", wert=1, schwelle=1)
    assert "{quatsch}" in text


# --------------------------------------------------------------------------
# Der Abhol-Knopf
# --------------------------------------------------------------------------

def test_kennung_haelt_regel_und_nutzer_fest():
    kennung = vr.knopf_kennung(7, "807477850738393108")
    assert vr.knopf_kennung_lesen(kennung) == (7, "807477850738393108")


def test_kennung_bleibt_unter_discords_grenze():
    """Discord erlaubt für custom_id höchstens 100 Zeichen. Eine zu lange
    Kennung würde beim Senden abgelehnt - und zwar erst dann."""
    kennung = vr.knopf_kennung(999999, "18446744073709551615")
    assert len(kennung) <= 100


@pytest.mark.parametrize("fremd", [
    "", "irgendwas", "verdienterolle:abc:123", "verdienterolle:1",
    "anderesystem:1:2", "verdienterolle:1:2:3",
])
def test_fremde_kennungen_werden_nicht_beansprucht(fremd):
    """Andere Knöpfe im Bot dürfen nicht versehentlich hier landen."""
    assert vr.knopf_kennung_lesen(fremd) is None


def test_knopf_ist_an_eine_person_gebunden():
    """Die Ankündigung steht im Kanal - ohne diese Bindung könnte sich jeder,
    der sie sieht, die Rolle holen."""
    quelle = COG.read_text()
    assert "gemeinter_user" in quelle
    assert "str(interaction.user.id) != gemeinter_user" in quelle


def test_knopf_ueberlebt_einen_neustart():
    """Ohne feste Kennung und timeout=None wäre die Ankündigung von gestern nach
    einem Neustart tot: Der Knopf sähe aus wie immer und täte nichts."""
    quelle = COG.read_text()
    assert "custom_id=vr.knopf_kennung" in quelle
    assert "timeout=None" in quelle


def test_bedingung_wird_beim_klick_erneut_geprueft():
    """Zwischen Ankündigung und Klick können Stunden liegen, und die Regel kann
    inzwischen geändert worden sein."""
    quelle = COG.read_text()
    block = quelle[quelle.index("async def on_interaction"):]
    assert "vr.erfuellt(" in block


# --------------------------------------------------------------------------
# Nicht zweimal ankündigen
# --------------------------------------------------------------------------

def test_merker_verhindert_wiederholte_ankuendigung():
    """Der Auslöser feuert bei JEDER Quizantwort. Ohne Merker bekäme jemand nach
    der 100. Frage bei jeder weiteren erneut dieselbe Ankündigung."""
    quelle = COG.read_text()
    block = quelle[quelle.index("async def _regel_pruefen"):]
    block = block[: block.index("async def _senden")]
    assert "earned_role_status" in block
    assert 'status["vergeben_at"] or status["angekuendigt_at"]' in block


def test_gescheiterte_ankuendigung_wird_nicht_vermerkt():
    """Eine Ankündigung, die niemand gesehen hat, als erledigt zu buchen, wäre
    der schlechteste Ausgang - dann kommt sie nie wieder."""
    quelle = COG.read_text()
    block = quelle[quelle.index("async def _regel_pruefen"):]
    block = block[: block.index("async def _senden")]
    senden = block.index("await self._senden")
    vermerk = block.index("mark_earned_role_announced")
    assert senden < vermerk, "Erst senden, dann vermerken."


def test_bestehende_rolle_wird_still_vermerkt():
    """Wer die Rolle schon auf anderem Weg hat, soll nicht angeschrieben werden."""
    quelle = COG.read_text()
    assert "if rolle in member.roles:" in quelle


# --------------------------------------------------------------------------
# Schranken
# --------------------------------------------------------------------------

def test_hierarchie_wird_vor_dem_vergeben_geprueft():
    assert "top_role.position" in COG.read_text()


def test_pruefung_reisst_den_aufrufer_nicht_mit():
    """Sie hängt an Quizantwort und XP-Vergabe. Ein Fehler darin darf nie das
    Quiz mitreißen."""
    quelle = COG.read_text()
    block = quelle[quelle.index("async def verdiente_rollen_pruefen"):]
    assert "except Exception" in block
    assert 'get_cog("VerdienteRollen")' in block
    assert "if cog is None" in block, (
        "Ohne diese Prüfung würde ein nicht installiertes Modul den Aufrufer stören."
    )


def test_backend_lehnt_unsinnige_schwellen_ab():
    quelle = APP_PY.read_text()
    block = quelle[quelle.index("def earned_roles_add"):]
    block = block[: block.index("def earned_roles_update")]
    assert "schwelle < 1" in block
    assert "ist_gueltige_art" in block
    assert "not in verwaltbare" in block


def test_loeschen_raeumt_die_merker_mit_auf():
    """Sonst zeigten alte Merker ins Leere, sobald eine neue Regel eine neue id
    bekommt."""
    quelle = DATABASE_PY.read_text()
    block = quelle[quelle.index("def delete_earned_role"):]
    block = block[: block.index("def earned_role_status")]
    assert "DELETE FROM earned_role_grants" in block
