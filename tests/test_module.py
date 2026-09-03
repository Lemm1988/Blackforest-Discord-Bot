"""Module bringen ihr eigenes Schema mit und bauen die Datenbank selbst auf.

Vorgabe Patrick (03.09.2026): „so einrichten das sich die datenbank selbst
aufbaut wenn man das modul später nachträglich installiert."

Vorher standen alle 28 Tabellen in einem zentralen Schema, und `init_db()`
enthielt Migrationen wie `_ensure_column(conn, "questions", ...)`. Ohne das
Quizmodul zeigten die auf Tabellen, die es gar nicht gibt — genau daran ist der
erste Probelauf einer Installation „nur Kern" gescheitert.

Diese Tests prüfen die drei Zusagen:

1. Ohne ein Modul entstehen seine Tabellen nicht.
2. Kommt es später dazu, legt der nächste Start sie an — samt Migrationen.
3. Bestehende Daten bleiben dabei unangetastet.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

import module  # noqa: E402

MODUL_DATEIEN = sorted((REPO / "bot").glob("modul_*.py"))


# --------------------------------------------------------------------------
# Die Registratur
# --------------------------------------------------------------------------

def test_jede_modul_datei_traegt_sich_ein():
    """Eine Datei, die sich nicht einträgt, wäre unsichtbar - ihr Schema würde
    nie angelegt, und niemand fände heraus warum."""
    module.laden()
    erwartet = {p.stem.removeprefix("modul_") for p in MODUL_DATEIEN}
    assert set(module.REGISTRATUR.namen()) == erwartet


def test_installierte_liste_steht_an_einer_stelle():
    """Sobald Module einzeln verkauft werden, kommt sie aus der Konfiguration.
    Bis dahin muss auf einen Blick erkennbar sein, was aktiv ist."""
    assert module.INSTALLIERT
    assert "kern" in module.INSTALLIERT, "Der Kern gehört immer dazu."


def test_doppeltes_modul_faellt_sofort_auf():
    r = module.Modulregistratur()
    r.eintragen(module.Modul(name="x", label="X"))
    with pytest.raises(ValueError, match="doppelt"):
        r.eintragen(module.Modul(name="x", label="X2"))


def test_jedes_schema_ist_wiederholbar():
    """Ohne IF NOT EXISTS würde der zweite Start scheitern - und jeder Start
    nach dem ersten ist ein zweiter."""
    module.laden()
    for modul in module.REGISTRATUR.alle():
        for anweisung in modul.schema:
            assert "IF NOT EXISTS" in anweisung.upper(), (
                f"Modul {modul.name}: Schema-Anweisung ohne IF NOT EXISTS."
            )


# --------------------------------------------------------------------------
# Die eigentliche Zusage: die Datenbank baut sich selbst auf
# --------------------------------------------------------------------------

def _frische_registratur(*namen):
    """Eine Registratur mit nur den genannten Modulen - so, wie es nach einer
    Teilinstallation aussähe."""
    module.laden()
    voll = {m.name: m for m in module.REGISTRATUR.alle()}
    r = module.Modulregistratur()
    for n in namen:
        r.eintragen(voll[n])
    return r


def _verbindung(pfad):
    """Wie database.get_connection() - mit Row-Fabrik. Die Modul-Migrationen
    nutzen _ensure_column(), das auf benannte Spalten zugreift."""
    conn = sqlite3.connect(pfad)
    conn.row_factory = sqlite3.Row
    return conn


def _tabellen(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_ohne_modul_keine_modultabellen(tmp_path):
    conn = _verbindung(tmp_path / "nur_kern.db")
    _frische_registratur("kern").schema_anwenden(conn)
    tabellen = _tabellen(conn)
    for verboten in ("questions", "players", "user_stats", "user_xp", "categories"):
        assert verboten not in tabellen, (
            f"{verboten} entsteht, obwohl das zugehörige Modul nicht installiert ist."
        )


def test_mit_modul_entstehen_seine_tabellen(tmp_path):
    conn = _verbindung(tmp_path / "mit_quiz.db")
    _frische_registratur("kern", "quiz").schema_anwenden(conn)
    tabellen = _tabellen(conn)
    for erwartet in ("questions", "players", "answered_questions", "categories", "user_stats"):
        assert erwartet in tabellen, f"{erwartet} fehlt trotz installiertem Quizmodul."
    assert "user_xp" not in tabellen, "XP-Tabelle gehört zum anderen Modul."


def test_nachtraegliche_installation_legt_die_tabellen_an(tmp_path):
    """Der Kern der Sache: erst ohne, dann mit - und beim nächsten Start ist
    alles da."""
    pfad = tmp_path / "nachinstalliert.db"
    conn = _verbindung(pfad)
    _frische_registratur("kern").schema_anwenden(conn)
    conn.commit()
    vorher = _tabellen(conn)

    _frische_registratur("kern", "quiz").schema_anwenden(conn)
    conn.commit()
    dazugekommen = _tabellen(conn) - vorher
    assert "questions" in dazugekommen and "user_stats" in dazugekommen


def test_nachtraegliche_installation_laesst_bestehende_daten_in_ruhe(tmp_path):
    """Das Gefährlichste an einem Selbstaufbau: dass er etwas überschreibt."""
    pfad = tmp_path / "mit_daten.db"
    conn = _verbindung(pfad)
    _frische_registratur("kern", "quiz").schema_anwenden(conn)
    conn.execute("INSERT INTO questions (question, option_a, option_b, option_c, option_d, "
                 "correct_option) VALUES ('Testfrage', 'a', 'b', 'c', 'd', 'A')")
    conn.commit()

    # Nochmal anwenden, so wie es jeder weitere Start tut
    _frische_registratur("kern", "quiz").schema_anwenden(conn)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1
    assert conn.execute("SELECT question FROM questions").fetchone()[0] == "Testfrage"


def test_migrationen_laufen_mit(tmp_path):
    """Nachträglich hinzugekommene Spalten gehören zum Modul. Stünden sie
    zentral, würden sie ohne das Modul auf fehlende Tabellen zugreifen."""
    conn = _verbindung(tmp_path / "mit_migration.db")
    _frische_registratur("kern", "quiz").schema_anwenden(conn)
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
    assert "category_id" in spalten, "Die Modul-Migration ist nicht gelaufen."
    indizes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_questions_category" in indizes


def test_schema_anwenden_ist_wiederholbar(tmp_path):
    conn = _verbindung(tmp_path / "mehrfach.db")
    r = _frische_registratur("kern", "quiz", "xp_level")
    for _ in range(3):
        r.schema_anwenden(conn)
    assert "questions" in _tabellen(conn)


# --------------------------------------------------------------------------
# Der Ereignis-Kontext
# --------------------------------------------------------------------------

def test_kontext_kommt_aus_den_modulen():
    """database.py kannte bis zum 03.09.2026 die Quiz-Interna: In
    check_and_unlock_achievements() stand
    `increment_user_stat(uid, "quiz_correct_count")` mitten in einer
    if/elif-Kette. Ohne das Quizmodul hätte diese Zeile auf eine Tabelle
    gezeigt, die es gar nicht gibt.

    Geprüft wird genau diese Funktion - nicht die ganze Datei. Die
    Abfragefunktionen der Module (get_leaderboard, award_xp, get_user_overview)
    stehen bewusst weiterhin zentral; siehe die Einschränkung im Kopf von
    module.py und den Test darunter.
    """
    import ast

    quelle = (REPO / "bot" / "database.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(quelle))
              if isinstance(n, ast.FunctionDef) and n.name == "check_and_unlock_achievements")
    text = ast.get_source_segment(quelle, fn) or ""
    # Kommentare und Docstring ausblenden: Dort DARF die Historie stehen - dass
    # dort frueher increment_user_stat(uid, "quiz_correct_count") stand, ist
    # gerade die nuetzliche Erklaerung. Geprueft wird der ausgefuehrte Code.
    text = "\n".join(
        z for z in text.splitlines() if not z.lstrip().startswith("#")
    )
    if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
        docstring = fn.body[0].value.value
        text = text.replace(docstring, "")

    for quiz_intern in ("quiz_correct_count", "user_stats", "increment_user_stat"):
        assert quiz_intern not in text, (
            f"check_and_unlock_achievements() kennt wieder {quiz_intern!r} - "
            "die Trennung ist durchbrochen."
        )
    assert "REGISTRATUR.kontext(event" in text


def test_die_verbliebene_kopplung_ist_bekannt_und_begrenzt():
    """Ehrlichkeit über den Stand: Die Abfragefunktionen der Module stehen noch
    in database.py. Für das Ziel „Datenbank baut sich selbst auf" reicht das
    Schema, aber ein entferntes Modul liesse seine Abfragen zurück.

    Der Test hält fest, dass diese Einschränkung DOKUMENTIERT ist - damit sie
    nicht in Vergessenheit gerät und irgendwann jemand annimmt, die Trennung
    sei vollständig."""
    kopf = (REPO / "bot" / "module.py").read_text()[:2000]
    assert "Einschränkung" in kopf or "Einschraenkung" in kopf
    assert "database.py" in kopf


def test_kaputtes_modul_reisst_die_anderen_nicht_mit():
    """Fehlt die Tabelle eines halb entfernten Moduls, sollen die übrigen
    weiterarbeiten."""
    r = module.Modulregistratur()
    r.eintragen(module.Modul(
        name="gut", label="Gut",
        ereignis_kontext={"e": lambda uid: {"a": 1}}))
    r.eintragen(module.Modul(
        name="kaputt", label="Kaputt",
        ereignis_kontext={"e": lambda uid: (_ for _ in ()).throw(RuntimeError("keine Tabelle"))}))
    assert r.kontext("e", "123") == {"a": 1}


def test_unbekanntes_ereignis_liefert_leeren_kontext():
    module.laden()
    assert module.REGISTRATUR.kontext("gibtsnicht", "123") == {}
