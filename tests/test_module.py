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


# --------------------------------------------------------------------------
# Die Moduldateien müssen ohne die Laufzeitumgebung ladbar sein
#
# Am 03.09.2026 lief die Testsuite lokal grün und die CI rot:
# `ModuleNotFoundError: No module named 'dotenv'`. Die Kette war
# modul_quiz.py → database → config → dotenv.
#
# Die CI installiert absichtlich nur pytest. Das Laden der Modul-METADATEN
# (Schema, Achievements, Bedingungen) darf deshalb nichts voraussetzen, was nur
# der laufende Bot hat. Lokal fällt so etwas nie auf, weil dort alles
# installiert ist — also braucht es einen Test dafür.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("datei", MODUL_DATEIEN, ids=lambda p: p.name)
def test_moduldatei_laedt_ohne_laufzeitumgebung(datei):
    """Kein `import database` auf oberster Ebene: Das zieht config und damit
    dotenv sowie einen gesetzten DISCORD_TOKEN nach sich.

    Innerhalb einer Funktion ist es in Ordnung — dann wird es erst gebraucht,
    wenn der Bot ohnehin läuft.
    """
    import ast

    baum = ast.parse(datei.read_text())
    verboten = {"database", "config", "discord", "dotenv"}
    for node in baum.body:  # nur oberste Ebene
        namen = set()
        if isinstance(node, ast.Import):
            namen = {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            namen = {node.module.split(".")[0]}
        treffer = namen & verboten
        assert not treffer, (
            f"{datei.name} importiert {sorted(treffer)} auf oberster Ebene. "
            "Damit lässt sich das Modul nicht mehr laden, ohne die Laufzeitumgebung "
            "des Bots zu haben - die CI installiert nur pytest."
        )


def test_schema_helfer_bleibt_abhaengigkeitsfrei():
    """Er ist der Grund, warum die Migrationen in den Modulen ohne database
    auskommen. Zöge er selbst etwas nach, wäre nichts gewonnen."""
    import ast

    quelle = (REPO / "bot" / "schema_helfer.py").read_text()
    for node in ast.parse(quelle).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            namen = ({a.name.split(".")[0] for a in node.names}
                     if isinstance(node, ast.Import) else {(node.module or "").split(".")[0]})
            assert namen <= {"sqlite3", "__future__"}, (
                f"schema_helfer.py importiert {sorted(namen)} - er soll nur sqlite3 brauchen."
            )


# --------------------------------------------------------------------------
# Der Kern kennt die Tabellen der Module nicht mehr
#
# Am 03.09.2026 sind 29 Abfragefunktionen aus database.py in die Module
# gewandert - get_leaderboard, award_xp, get_user_overview und die übrigen.
# Vorher hätte ein entferntes Modul dort Funktionen zurückgelassen, die auf
# Tabellen zugreifen, die es nicht mehr gibt.
# --------------------------------------------------------------------------

MODULTABELLEN = ("questions", "answered_questions", "players", "categories",
                 "user_stats", "user_xp")


def _sql_aus(quelle: str) -> str:
    """Nur die Zeichenketten aus dem Code - Kommentare und Docstrings
    ausgenommen. Dort DARF eine Tabelle erwähnt werden, das ist Erklärung."""
    import ast
    import re

    baum = ast.parse(quelle)
    docstrings = set()
    for node in ast.walk(baum):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docstrings.add(d)
    ohne_kommentar = "\n".join(
        z for z in quelle.split("\n") if not z.lstrip().startswith("#")
    )
    texte = re.findall(r'"""(?:.|\n)*?"""|"[^"\n]*"|\'[^\'\n]*\'', ohne_kommentar)
    return " ".join(t for t in texte if t.strip('"\'' + "\n") not in docstrings)


@pytest.mark.parametrize("tabelle", MODULTABELLEN)
def test_kern_greift_nicht_mehr_auf_modultabellen_zu(tabelle):
    import re

    sql = _sql_aus((REPO / "bot" / "database.py").read_text())
    assert not re.search(rf"\b{tabelle}\b", sql), (
        f"database.py enthält wieder SQL auf {tabelle} - die Tabelle gehört einem "
        "Modul. Ohne dieses Modul liefe die Abfrage ins Leere."
    )


def test_die_abfragen_sind_wirklich_da():
    """Gegenprobe: Sie sollen verschoben sein, nicht verschwunden."""
    import ast

    for datei, erwartet in (
        ("modul_quiz.py", ["get_leaderboard", "add_points", "submit_question", "get_categories"]),
        ("modul_xp_level.py", ["award_xp", "get_user_xp", "get_xp_leaderboard"]),
    ):
        quelle = (REPO / "bot" / datei).read_text()
        namen = {n.name for n in ast.walk(ast.parse(quelle)) if isinstance(n, ast.FunctionDef)}
        for fn in erwartet:
            assert fn in namen, f"{fn}() fehlt in {datei}"


def test_uebersicht_kommt_aus_den_modulen():
    """get_user_overview() fragte drei Tabellen aus zwei Modulen direkt ab.
    Jetzt tragen die Module bei, und der Kern führt nur zusammen."""
    import ast

    quelle = (REPO / "bot" / "database.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(quelle))
              if isinstance(n, ast.FunctionDef) and n.name == "get_user_overview")
    text = ast.get_source_segment(quelle, fn) or ""
    assert "REGISTRATUR.uebersicht" in text
    assert "SELECT" not in text.upper(), "Der Zusammenführer fragt selbst ab."


def test_uebersicht_bleibt_vollstaendig_ohne_module():
    """Fehlt ein Modul, sollen seine Werte auf Standardwerten stehen statt zu
    fehlen - sonst müsste die Seite jeden einzeln absichern."""
    module.laden()
    voll = module.REGISTRATUR.uebersicht("999999999999999999")
    for schluessel in ("message_count", "quiz_correct_count", "score", "xp", "level"):
        assert schluessel in voll, f"{schluessel} fehlt in der Übersicht."
    assert voll["level"] == 1, "Ohne XP ist man auf Level 1, nicht 0."
