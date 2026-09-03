"""Eine Discord-Rolle kann eine Berechtigung im Bot mitbringen.

Die Gegenrichtung zur Rechte-Zuordnung: Dort heißt es „Recht X bedeutet Rolle Y"
(der Bot vergibt), hier „Rolle Y bedeutet Recht X" (der Bot leitet ab).

Anlass (Patrick, 03.09.2026): „ideenschmied berechtigt dann beim Bot fragen
einzureichen… man soll einstellen können das ein user automatisch die
berechtigung bekommt sobald er die rolle ideenschmied erhält."

Damit ändert sich etwas Grundsätzliches: `has_permission()` war bis dahin eine
reine Datenbankfrage. Sie kennt Discord nicht und soll es auch nicht — deshalb
werden die Rollen **hereingereicht**, statt dass sie sie sich holt.
"""
from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

DATABASE_PY = REPO / "bot" / "database.py"
APP_PY = REPO / "backend" / "app.py"
QUIZ_COG = REPO / "bot" / "cogs" / "quiz.py"


def _schema(tabelle: str) -> str:
    quelle = DATABASE_PY.read_text()
    start = quelle.index(f"CREATE TABLE IF NOT EXISTS {tabelle}")
    return quelle[start : quelle.index(");", start) + 2]


# --------------------------------------------------------------------------
# Das Schema
# --------------------------------------------------------------------------

def test_eine_rolle_kann_mehrere_rechte_mitbringen(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute(_schema("role_permissions"))
    conn.execute("INSERT INTO role_permissions VALUES ('1', 'submit_questions')")
    conn.execute("INSERT INTO role_permissions VALUES ('1', 'create_embed')")
    assert conn.execute("SELECT COUNT(*) FROM role_permissions").fetchone()[0] == 2


def test_dasselbe_recht_nicht_zweimal_pro_rolle(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute(_schema("role_permissions"))
    conn.execute("INSERT INTO role_permissions VALUES ('1', 'submit_questions')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO role_permissions VALUES ('1', 'submit_questions')")


def test_die_beiden_richtungen_sind_getrennte_tabellen():
    """`permission_roles` (Recht → Rolle) und `role_permissions` (Rolle → Recht)
    in eine Tabelle zu legen wäre kürzer, aber man könnte beim Lesen nicht mehr
    erkennen, welche Richtung gemeint ist — und ein Kreis wäre nicht auffindbar."""
    quelle = DATABASE_PY.read_text()
    assert "CREATE TABLE IF NOT EXISTS permission_roles" in quelle
    assert "CREATE TABLE IF NOT EXISTS role_permissions" in quelle


# --------------------------------------------------------------------------
# Die Rechteprüfung
# --------------------------------------------------------------------------

def test_has_permission_nimmt_rollen_entgegen():
    """Optionales Argument statt einer Abfrage im Innern: Die Funktion kennt
    Discord nicht und soll es nicht. Wer die Rollen kennt, reicht sie herein."""
    quelle = DATABASE_PY.read_text()
    fn = next(n for n in ast.walk(ast.parse(quelle))
              if isinstance(n, ast.FunctionDef) and n.name == "has_permission")
    argumente = [a.arg for a in fn.args.args]
    assert "rollen" in argumente
    assert fn.args.defaults, "rollen muss optional sein - alte Aufrufer sollen weiter gehen."


def test_ohne_rollen_verhaelt_es_sich_wie_vorher():
    quelle = DATABASE_PY.read_text()
    fn = next(n for n in ast.walk(ast.parse(quelle))
              if isinstance(n, ast.FunctionDef) and n.name == "has_permission")
    text = ast.get_source_segment(quelle, fn) or ""
    assert "if not rollen:" in text and "return False" in text


def test_eine_rolle_darf_kein_admin_verleihen():
    """Sonst könnte jeder, der die Rolle in Discord vergeben darf, volle
    Backend-Rechte verteilen — an der Benutzerverwaltung vorbei."""
    quelle = APP_PY.read_text()
    block = quelle[quelle.index("def roles_mapping_role_rights"):]
    block = block[: block.index("roles-mapping/save")]
    assert '"is_admin" in keys' in block
    pruefung = block.index('"is_admin" in keys')
    speichern = block.index("set_role_permissions")
    assert pruefung < speichern, "Die Prüfung steht nach dem Speichern."


def test_unbekannte_rechte_werden_verworfen():
    quelle = DATABASE_PY.read_text()
    block = quelle[quelle.index("def set_role_permissions"):]
    block = block[: block.index("def rechte_aus_rollen")]
    assert "alle_berechtigungen()" in block


# --------------------------------------------------------------------------
# Rechte aus Modulen
# --------------------------------------------------------------------------

def test_submit_questions_gehoert_zum_quizmodul():
    """Ohne Quizmodul gäbe es nichts, wofür das Recht gilt — dann soll es auch
    nicht zur Vergabe stehen."""
    import module

    module.laden()
    rechte = module.REGISTRATUR.berechtigungen()
    assert "submit_questions" in rechte
    quiz = next(m for m in module.REGISTRATUR.alle() if m.name == "quiz")
    assert "submit_questions" in quiz.berechtigungen


def _kernrechte() -> list:
    """ALL_PERMISSIONS aus database.py lesen, ohne das Modul zu importieren.

    Ein Import zöge config und damit dotenv nach sich - die CI installiert nur
    pytest. Genau daran sind diese beiden Tests beim ersten Anlauf gescheitert,
    während lokal alles grün war.
    """
    quelle = DATABASE_PY.read_text()
    for node in ast.parse(quelle).body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "ALL_PERMISSIONS" for t in node.targets
        ):
            return list(ast.literal_eval(node.value))
    raise AssertionError("ALL_PERMISSIONS nicht gefunden.")


def test_kernrechte_bleiben_im_kern():
    """Rechte, die es immer gibt, gehören nicht in ein Modul."""
    import module

    module.laden()
    aus_modulen = set(module.REGISTRATUR.berechtigungen())
    assert not (set(_kernrechte()) & aus_modulen), (
        "Ein Kernrecht wird zusätzlich von einem Modul beansprucht."
    )


def test_kern_und_module_zusammen_sind_ueberschneidungsfrei():
    import module

    module.laden()
    alle = _kernrechte() + sorted(module.REGISTRATUR.berechtigungen())
    assert "is_admin" in alle and "submit_questions" in alle
    assert len(alle) == len(set(alle)), "Ein Recht kommt doppelt vor."


# --------------------------------------------------------------------------
# Fragen einreichen ist jetzt ein Recht
# --------------------------------------------------------------------------

def test_der_befehl_verlangt_das_recht():
    quelle = QUIZ_COG.read_text()
    assert "_darf_fragen_einreichen" in quelle
    block = quelle[quelle.index("async def _darf_fragen_einreichen"):]
    block = block[: block.index("class Quiz")]
    assert "submit_questions" in block
    assert "rollen=" in block, "Die Rollen des Aufrufers müssen mitgegeben werden."


def test_die_absage_erklaert_den_weg():
    """„Keine Berechtigung" allein lässt jemanden ratlos zurück. Der Text sagt,
    wie man das Recht bekommt."""
    quelle = QUIZ_COG.read_text()
    block = quelle[quelle.index("async def _darf_fragen_einreichen"):]
    block = block[: block.index("class Quiz")]
    assert "Quizfragen" in block and "frei" in block


@pytest.mark.parametrize("route", ["/dbot", "/qinput"])
def test_auch_das_webformular_ist_abgesichert(route):
    """Sonst wäre der Befehl gesperrt und die Weboberfläche offen — die
    Sperre wäre wirkungslos."""
    quelle = APP_PY.read_text()
    i = quelle.index(f'@app.route("{route}"')
    block = quelle[i : i + 300]
    assert 'require_permission("submit_questions")' in block, (
        f"{route} prüft das Recht nicht."
    )


def test_die_hilfe_beruecksichtigt_rollenrechte():
    """Sonst stünde /frage_einreichen nicht in der Hilfe von jemandem, der ihn
    sehr wohl benutzen darf."""
    quelle = (REPO / "bot" / "cogs" / "hilfe.py").read_text()
    block = quelle[quelle.index("def _pruefer"):]
    block = block[: block.index("# ---")] if "# ---" in block else block[:800]
    assert "rollen=" in block
