"""Verstoesse zuruecksetzen und loeschen.

Hintergrund (gemeldet von Patrick, 04.09.2026): "wenn man bei verstoss zaehlung
zuruecksetzen verstoesse zuruecksetzen moechte passiert nichts wenn man auf
zuruecksetzen klickt und die liste darunter wird nicht geleert."

Die Ursache war zweigeteilt und beide Haelften sind lehrreich:

1. Das Zuruecksetzen funktionierte, sagte es aber nicht. Es setzt bewusst nur
   die ZAEHLUNG fuer die Timeout-Schwelle zurueck und laesst die Historie
   stehen - nur gab die Seite darueber keinerlei Rueckmeldung. Wer klickte, sah
   eine unveraenderte Seite und schloss daraus, der Knopf sei kaputt. Eine
   Funktion, die stillschweigend arbeitet, ist von einer kaputten nicht zu
   unterscheiden.

2. Was Patrick eigentlich brauchte, gab es gar nicht: ein echtes Loeschen. Fuer
   einen Fehlalarm des Filters - ein harmloses Wort im falschen Zusammenhang -
   soll kein dauerhafter Eintrag in der Akte einer Person bleiben.

Getestet wird der echte Quelltext aus bot/database.py gegen eine
Wegwerf-Datenbank (siehe discord_source.load_from_database), nicht ein Nachbau.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from discord_source import load_from_database, tabellen_definition  # noqa: E402


@pytest.fixture
def db():
    """Eine Wegwerf-Datenbank mit dem ECHTEN Schema aus database.py."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(tabellen_definition("badword_violations"))
    conn.execute(tabellen_definition("settings"))
    conn.commit()
    return conn


@pytest.fixture
def funktionen(db):
    """Die echten Funktionen, nur mit ausgetauschter Verbindung.

    get_connection() liefert hier immer dieselbe Verbindung. Der
    Kontextmanager-Gebrauch (`with get_connection() as conn`) darf sie deshalb
    nicht schliessen - sqlite3-Verbindungen als Kontextmanager schliessen
    ohnehin nicht, sie schliessen nur die Transaktion ab.
    """
    return load_from_database(
        "delete_violation", "delete_user_violations", "count_user_violations_by_type",
        "count_user_violations", "reset_user_violations", "_violation_reset_key",
        umgebung={"get_connection": lambda: db},
    )


def eintragen(db, user, typ="badword", wort="wort"):
    cur = db.execute(
        "INSERT INTO badword_violations (discord_user_id, message_excerpt, matched_word, violation_type) "
        "VALUES (?, ?, ?, ?)", (str(user), "ausschnitt", wort, typ))
    db.commit()
    return cur.lastrowid


# --------------------------------------------------------------------------
# Zuruecksetzen: die Zaehlung faellt, die Historie bleibt
# --------------------------------------------------------------------------

def test_zuruecksetzen_laesst_die_historie_stehen(db, funktionen):
    """Das ist Absicht - deshalb musste die Oberflaeche es sagen, statt es zu aendern."""
    for _ in range(5):
        eintragen(db, 42)
    assert funktionen["count_user_violations"](42, "badword") == 5

    funktionen["reset_user_violations"](42, "badword")

    assert funktionen["count_user_violations"](42, "badword") == 0, "Zaehlung muss fallen"
    uebrig = db.execute("SELECT COUNT(*) c FROM badword_violations").fetchone()["c"]
    assert uebrig == 5, "Die Historie darf beim Zuruecksetzen nicht verschwinden"


def test_zuruecksetzen_trifft_nur_den_gewaehlten_typ(db, funktionen):
    eintragen(db, 42, "badword")
    eintragen(db, 42, "link")
    funktionen["reset_user_violations"](42, "badword")
    assert funktionen["count_user_violations"](42, "badword") == 0
    assert funktionen["count_user_violations"](42, "link") == 1


def test_spaetere_verstoesse_zaehlen_nach_dem_zuruecksetzen_wieder(db, funktionen):
    """Sonst waere ein Reset eine dauerhafte Amnestie statt eines Neuanfangs."""
    eintragen(db, 42)
    funktionen["reset_user_violations"](42, "badword")
    eintragen(db, 42)
    assert funktionen["count_user_violations"](42, "badword") == 1


# --------------------------------------------------------------------------
# Loeschen: jetzt verschwindet wirklich etwas
# --------------------------------------------------------------------------

def test_einzelnen_verstoss_loeschen(db, funktionen):
    vid = eintragen(db, 42)
    eintragen(db, 42)
    assert funktionen["delete_violation"](vid) is True
    assert db.execute("SELECT COUNT(*) c FROM badword_violations").fetchone()["c"] == 1


def test_zweimal_loeschen_meldet_beim_zweiten_mal_nichts_getan(db, funktionen):
    """Damit die Oberflaeche "geloescht" von "gab es nicht mehr" unterscheiden
    kann - zwei Mods, die gleichzeitig aufraeumen, sollen keine falsche
    Erfolgsmeldung bekommen."""
    vid = eintragen(db, 42)
    assert funktionen["delete_violation"](vid) is True
    assert funktionen["delete_violation"](vid) is False


def test_alle_verstoesse_einer_person_loeschen(db, funktionen):
    for _ in range(3):
        eintragen(db, 42)
    eintragen(db, 42, "link")
    eintragen(db, 99)

    assert funktionen["delete_user_violations"](42) == 4
    assert funktionen["count_user_violations_by_type"](42) == {}
    assert funktionen["count_user_violations_by_type"](99) == {"badword": 1}, \
        "Andere Personen duerfen nicht mitgeloescht werden"


def test_loeschen_nur_eines_typs(db, funktionen):
    eintragen(db, 42, "badword")
    eintragen(db, 42, "link")
    eintragen(db, 42, "spam")
    assert funktionen["delete_user_violations"](42, "link") == 1
    assert funktionen["count_user_violations_by_type"](42) == {"badword": 1, "spam": 1}


def test_loeschen_ohne_treffer_meldet_null(db, funktionen):
    assert funktionen["delete_user_violations"](12345) == 0


# --------------------------------------------------------------------------
# Der Fallstrick: die zurueckgelassene Grenze
# --------------------------------------------------------------------------

def test_loeschen_raeumt_die_reset_grenze_mit_auf(db, funktionen):
    """Der Fehler, der sonst erst Wochen spaeter auffiele.

    reset_user_violations() merkt sich die hoechste vorhandene id als Grenze;
    gezaehlt wird nur darueber. Loescht man die Verstoesse danach, ohne die
    Grenze zu entfernen, zeigt sie auf eine id, die es nicht mehr gibt - und
    die naechsten echten Verstoesse liegen womoeglich darunter und werden
    stillschweigend nicht gezaehlt. Die Person bekaeme trotz Verstoessen nie
    einen Timeout, und niemand wuesste warum.
    """
    for _ in range(5):
        eintragen(db, 42)
    funktionen["reset_user_violations"](42, "badword")
    schluessel = funktionen["_violation_reset_key"](42, "badword")
    assert db.execute("SELECT COUNT(*) c FROM settings WHERE key = ?",
                      (schluessel,)).fetchone()["c"] == 1, "Grenze muss gesetzt sein"

    funktionen["delete_user_violations"](42, "badword")

    assert db.execute("SELECT COUNT(*) c FROM settings WHERE key = ?",
                      (schluessel,)).fetchone()["c"] == 0, \
        "Die Grenze muss beim Loeschen mit verschwinden"


def test_nach_dem_loeschen_zaehlt_ein_neuer_verstoss_wieder(db, funktionen):
    """Die Probe aufs Exempel zum Test darueber - hier wuerde der Fehler
    tatsaechlich sichtbar."""
    for _ in range(5):
        eintragen(db, 42)
    funktionen["reset_user_violations"](42, "badword")
    funktionen["delete_user_violations"](42, "badword")

    # Eine frische Wegwerf-Datenbank vergibt ids erneut ab 1 - genau die
    # Situation, in der eine stehengebliebene Grenze von 5 alles verschluckt.
    db.execute("DELETE FROM sqlite_sequence WHERE name = 'badword_violations'")
    db.commit()
    eintragen(db, 42)
    assert funktionen["count_user_violations"](42, "badword") == 1


def test_loeschen_ohne_typ_raeumt_alle_grenzen_auf(db, funktionen):
    eintragen(db, 42, "badword")
    eintragen(db, 42, "link")
    funktionen["reset_user_violations"](42, "badword")
    funktionen["reset_user_violations"](42, "link")
    funktionen["delete_user_violations"](42)
    uebrig = db.execute("SELECT COUNT(*) c FROM settings WHERE key LIKE '%42%'").fetchone()["c"]
    assert uebrig == 0


# --------------------------------------------------------------------------
# Die Oberflaeche muss sagen, was passiert ist
# --------------------------------------------------------------------------

APP = (REPO / "backend" / "app.py").read_text()


def test_zuruecksetzen_gibt_rueckmeldung():
    """Der eigentliche gemeldete Fehler: Es passierte etwas, nur sagte es
    niemand. Ohne diese Meldung ist der Knopf von einem kaputten Knopf nicht
    zu unterscheiden."""
    anfang = APP.index("def moderation_reset_violations")
    ende = APP.index("def moderation_delete_violation")
    ausschnitt = APP[anfang:ende]
    assert "flash(" in ausschnitt, "Zuruecksetzen muss dem Nutzer etwas sagen"
    assert ausschnitt.count("flash(") >= 2, "Auch der Fall 'ungueltige Eingabe' braucht eine Meldung"


def test_es_gibt_ueberhaupt_loeschrouten():
    assert "def moderation_delete_violation" in APP
    assert "def moderation_delete_user_violations" in APP


def test_loeschen_darf_admin_oder_moderator():
    """Vorgabe Patrick: "als admin oder Moderator". Ein Moderator sieht die
    Verstoesse auf der Mitgliederseite und muss dort auch aufraeumen koennen,
    ohne zugleich den Wortfilter verwalten zu duerfen."""
    for route in ("moderation_delete_violation", "moderation_delete_user_violations"):
        anfang = APP.index(f"def {route}")
        kopf = APP[max(0, anfang - 400):anfang]
        assert 'require_any_permission("manage_badword_filter", "moderate_members")' in kopf, \
            f"{route} muss fuer Admin UND Moderator erreichbar sein"


@pytest.mark.parametrize("vorlage", ["moderation.html", "member_detail.html"])
def test_loeschknopf_steht_in_der_oberflaeche(vorlage):
    """Eine Route ohne Knopf hilft niemandem - genau die Sorte halbe Arbeit,
    die den urspruenglichen Fehler ausmachte."""
    text = (REPO / "backend" / "templates" / vorlage).read_text()
    assert "moderation_delete_violation" in text, f"{vorlage} braucht den Loeschknopf je Zeile"
    assert "csrf_token" in text


@pytest.mark.parametrize("vorlage", ["moderation.html", "member_detail.html"])
def test_loeschen_fragt_vorher_nach(vorlage):
    """Endgueltiges Loeschen ohne Rueckfrage waere fahrlaessig - es gibt kein
    Rueckgaengig."""
    text = (REPO / "backend" / "templates" / vorlage).read_text()
    anfang = text.index("moderation_delete_violation")
    umgebung = text[max(0, anfang - 300):anfang + 300]
    assert "confirm(" in umgebung
