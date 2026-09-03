"""Reaktionsrollen (Plan 11, Teil B — umgesetzt 03.09.2026).

Vorher gab es genau EINE solche Regel, und sie steckte fest im Code: vier
einzelne Einstellungen (`role_reaction_enabled`, `rules_message_id`,
`rules_channel_name`, `member_role_name`), also nicht vermehrbar.

Beim Umbau ist etwas nebenbei besser geworden, das hier abgesichert wird: Die
alte Fassung suchte den Kanal über seinen NAMEN und die Rolle ebenfalls. Wer den
Regelkanal umbenennt oder „Member" in „Mitglied" ändert, hätte die Rollenvergabe
stillschweigend abgeschaltet — ohne Fehler, ohne Hinweis. Die neuen Regeln
arbeiten mit IDs.
"""
from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DATABASE_PY = REPO / "bot" / "database.py"
REACTIONS_PY = REPO / "bot" / "cogs" / "reactions.py"
APP_PY = REPO / "backend" / "app.py"


def _schema() -> str:
    """Die CREATE-TABLE-Anweisung aus database.py."""
    quelle = DATABASE_PY.read_text()
    start = quelle.index("CREATE TABLE IF NOT EXISTS reaction_roles")
    return quelle[start : quelle.index(");", start) + 2]


@pytest.fixture
def db(tmp_path):
    """Eine leere Datenbank mit nur dieser Tabelle - reicht, um das Verhalten
    des Schemas zu prüfen, ohne die ganze Anwendung zu laden."""
    conn = sqlite3.connect(tmp_path / "test.db")
    conn.execute(_schema())
    return conn


# --------------------------------------------------------------------------
# Das Schema
# --------------------------------------------------------------------------

def test_ein_emoji_auf_einer_nachricht_nur_einmal(db):
    """Zwei Regeln für dasselbe Emoji auf derselben Nachricht wären nicht
    auflösbar - welche Rolle soll es dann sein?"""
    db.execute("INSERT INTO reaction_roles (channel_id, message_id, emoji, role_id) "
               "VALUES ('1', '2', '👍', '3')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO reaction_roles (channel_id, message_id, emoji, role_id) "
                   "VALUES ('1', '2', '👍', '4')")


def test_dasselbe_emoji_auf_verschiedenen_nachrichten_ist_erlaubt(db):
    db.execute("INSERT INTO reaction_roles (channel_id, message_id, emoji, role_id) "
               "VALUES ('1', '2', '👍', '3')")
    db.execute("INSERT INTO reaction_roles (channel_id, message_id, emoji, role_id) "
               "VALUES ('1', '99', '👍', '3')")


def test_mehrere_emojis_auf_einer_nachricht_sind_erlaubt(db):
    """Der übliche Fall: eine Nachricht, mehrere Rollen zur Auswahl."""
    for emoji, rolle in (("👍", "3"), ("🌱", "4"), ("🔥", "5")):
        db.execute("INSERT INTO reaction_roles (channel_id, message_id, emoji, role_id) "
                   "VALUES ('1', '2', ?, ?)", (emoji, rolle))
    assert db.execute("SELECT COUNT(*) FROM reaction_roles").fetchone()[0] == 3


def test_neue_regeln_sind_standardmaessig_aktiv_und_entziehen(db):
    """Das Verhalten der alten Regel-Reaktion - wer sein 👍 zurücknimmt, verliert
    die Rolle wieder. Bleibt die Vorgabe, damit nichts überrascht."""
    db.execute("INSERT INTO reaction_roles (channel_id, message_id, emoji, role_id) "
               "VALUES ('1', '2', '👍', '3')")
    row = db.execute("SELECT enabled, entzug_erlaubt FROM reaction_roles").fetchone()
    assert row == (1, 1)


# --------------------------------------------------------------------------
# IDs statt Namen
# --------------------------------------------------------------------------

def test_kanal_und_rolle_werden_als_id_gespeichert():
    """Der eigentliche Gewinn des Umbaus. Namen ändern sich, IDs nicht."""
    schema = _schema()
    assert "channel_id" in schema and "role_id" in schema
    assert "channel_name" not in schema and "role_name" not in schema


def test_der_cog_sucht_nicht_mehr_ueber_namen():
    """Gegenprobe im laufenden Ablauf: Ein `channel.name ==`-Vergleich oder ein
    `utils.get(name=...)` wäre der Rückfall in genau die stille Fehlerquelle."""
    quelle = REACTIONS_PY.read_text()
    ablauf = quelle[quelle.index("async def _handle"):]
    assert "channel.name" not in ablauf
    assert "utils.get" not in ablauf
    assert "get_role(int(" in ablauf, "Die Rolle muss über ihre ID geholt werden."


# --------------------------------------------------------------------------
# Der Umzug der alten Regel
# --------------------------------------------------------------------------

def test_umzug_laeuft_nur_einmal():
    """Ohne Merker käme die alte Regel nach jedem Löschen im Backend wieder
    zurück - und niemand verstünde warum."""
    quelle = REACTIONS_PY.read_text()
    block = quelle[quelle.index("async def _alte_regel_uebernehmen"):]
    block = block[: block.index("async def _handle")]
    assert 'get_bot_state("reaction_roles_migriert")' in block
    assert 'set_bot_state("reaction_roles_migriert", "1")' in block


def test_umzug_erfindet_nichts():
    """Findet sich Kanal oder Rolle nicht, darf der Umzug NICHT als erledigt
    gelten - vielleicht wurde nur etwas umbenannt und beim nächsten Start passt
    es wieder."""
    quelle = REACTIONS_PY.read_text()
    block = quelle[quelle.index("async def _alte_regel_uebernehmen"):]
    block = block[: block.index("async def _handle")]
    vorzeitiger_ausstieg = block.index("if not kanal or not rolle:")
    merker = block.index('set_bot_state("reaction_roles_migriert"')
    assert vorzeitiger_ausstieg < merker
    assert "return" in block[vorzeitiger_ausstieg:merker]


# --------------------------------------------------------------------------
# Sicherheitsschranken
# --------------------------------------------------------------------------

def test_cog_prueft_die_rollenhierarchie_vorher():
    """Ein Bot kann nur Rollen unterhalb seiner eigenen vergeben. Vorher prüfen
    statt an einer nichtssagenden Discord-Ablehnung zu scheitern."""
    quelle = REACTIONS_PY.read_text()
    assert "top_role.position" in quelle


def test_backend_laesst_keine_unvergebbare_rolle_zu():
    quelle = APP_PY.read_text()
    block = quelle[quelle.index("def reaction_roles_add"):]
    block = block[: block.index("def reaction_roles_update")]
    assert "not in verwaltbare" in block


def test_backend_prueft_dass_die_nachricht_existiert():
    """Sonst legt man eine Regel für eine Nachricht an, die es nicht gibt, und
    wundert sich, warum nie etwas passiert."""
    quelle = APP_PY.read_text()
    block = quelle[quelle.index("def reaction_roles_add"):]
    block = block[: block.index("def reaction_roles_update")]
    assert "fetch_message" in block
    assert "discord.NotFound" in block


def test_backend_setzt_die_reaktion_selbst():
    """Sonst müsste jemand als Erster von Hand reagieren, damit andere
    überhaupt draufklicken können."""
    quelle = APP_PY.read_text()
    block = quelle[quelle.index("def reaction_roles_add"):]
    block = block[: block.index("def reaction_roles_update")]
    assert "add_reaction" in block


def test_dm_scheitert_still():
    """Wer seine Direktnachrichten zu hat, soll die Rolle trotzdem bekommen -
    die Nachricht ist Beiwerk, die Rolle ist der Zweck."""
    quelle = REACTIONS_PY.read_text()
    block = quelle[quelle.index("async def _benachrichtigen"):]
    block = block[: block.index("@commands.Cog.listener")]
    assert "except discord.HTTPException" in block
    assert "pass" in block


def test_alle_datenbankfunktionen_vorhanden():
    baum = ast.parse(DATABASE_PY.read_text())
    namen = {n.name for n in ast.walk(baum) if isinstance(n, ast.FunctionDef)}
    for f in ("get_reaction_roles", "find_reaction_role", "add_reaction_role",
              "update_reaction_role", "delete_reaction_role"):
        assert f in namen, f"{f}() fehlt in database.py"


def test_find_reaction_role_liefert_nur_aktive():
    """Eine abgeschaltete Regel soll sich verhalten, als gäbe es sie nicht -
    sonst vergibt sie weiter Rollen, obwohl der Schalter aus ist."""
    quelle = DATABASE_PY.read_text()
    block = quelle[quelle.index("def find_reaction_role"):]
    block = block[: block.index("def add_reaction_role")]
    assert "enabled = 1" in block
