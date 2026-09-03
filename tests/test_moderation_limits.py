"""Grenzwerte der Moderations- und Nachrichten-Endpunkte gegen die Discord-Doku.

Ergaenzt test_discord_limits.py (Embeds) um die Regeln, die an anderen Stellen
der API gelten. Alle Zahlen am 03.09.2026 nachgeschlagen unter
https://docs.discord.com/developers/resources/guild (Modify Guild Member) und
https://docs.discord.com/developers/resources/audit-log (X-Audit-Log-Reason).

Zusaetzlich abgesichert: die einzige Stelle im Projekt, an der Spaltennamen per
String in SQL eingesetzt werden. Das ist dort korrekt geloest (feste Whitelist,
Pruefung vor dem Einsetzen), aber es ist die Art Konstruktion, die beim naechsten
Erweitern kippt - deshalb ein Test, der das Muster erzwingt statt es nur zu
beschreiben.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
APP_PY = REPO / "backend" / "app.py"
DATABASE_PY = REPO / "bot" / "database.py"

# Discord: communication_disabled_until darf hoechstens 28 Tage in der Zukunft
# liegen. 28 * 24 * 60 = 40320 Minuten.
TIMEOUT_MAX_MINUTEN = 28 * 24 * 60


def _funktion(quelle: str, name: str) -> ast.AST:
    """Findet eine Funktion beliebiger Verschachtelungstiefe. Die Routen liegen
    innerhalb von create_app(), ein Blick nur auf die oberste Ebene fande sie
    nicht."""
    for node in ast.walk(ast.parse(quelle)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Funktion {name!r} nicht gefunden - umbenannt oder entfernt?")


def _zahlen(node: ast.AST) -> set[int]:
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, int)}


# --------------------------------------------------------------------------
# Timeout
# --------------------------------------------------------------------------

def test_28_tage_sind_40320_minuten():
    """Absicherung der Umrechnung selbst - die Zahl 40320 sagt beim Lesen nicht,
    woher sie kommt, und ein Vertipper faellt sonst niemandem auf."""
    assert TIMEOUT_MAX_MINUTEN == 40320


def test_timeout_wird_auf_28_tage_begrenzt():
    """Discord lehnt laengere Timeouts ab. Ohne Deckelung kaeme die Ablehnung erst
    aus der API und die Oberflaeche zeigte nur einen generischen Fehler."""
    fn = _funktion(APP_PY.read_text(), "member_timeout")
    assert TIMEOUT_MAX_MINUTEN in _zahlen(fn), (
        f"member_timeout() begrenzt die Dauer nicht auf {TIMEOUT_MAX_MINUTEN} Minuten "
        "(28 Tage). Discord weist laengere Timeouts zurueck."
    )


def test_timeout_hat_eine_untergrenze():
    """0 Minuten wuerde einen bestehenden Timeout aufheben statt einen zu setzen -
    ein stiller Fehlgriff, wenn jemand das Feld leer laesst."""
    fn = _funktion(APP_PY.read_text(), "member_timeout")
    assert 1 in _zahlen(fn), "member_timeout() hat keine Untergrenze von 1 Minute."


# --------------------------------------------------------------------------
# Direktnachrichten
# --------------------------------------------------------------------------

def test_dm_begrenzt_auf_2000_zeichen():
    """Discords Nachrichtenlimit fuer Konten ohne Nitro. Laengeres wird von der
    API mit Invalid Form Body abgelehnt."""
    fn = _funktion(APP_PY.read_text(), "send_dm")
    assert 2000 in _zahlen(fn), "send_dm() prueft die Nachrichtenlaenge nicht gegen 2000."


def test_dm_meldet_geschlossene_direktnachrichten_verstaendlich():
    """Discord antwortet mit 403 Forbidden, wenn der Empfaenger keine DMs von
    Servermitgliedern annimmt. Das ist kein Fehler des Absenders und muss als
    solcher erklaert werden, sonst sucht man den Fehler bei sich."""
    quelle = ast.get_source_segment(APP_PY.read_text(), _funktion(APP_PY.read_text(), "send_dm"))
    assert "discord.Forbidden" in quelle, "send_dm() faengt discord.Forbidden nicht ab."
    assert "NotFound" in quelle, "send_dm() unterscheidet nicht zwischen 'kein Nutzer' und 'DMs zu'."


# --------------------------------------------------------------------------
# Spaltennamen in SQL
# --------------------------------------------------------------------------

WHITELIST_FUNKTIONEN = [
    ("increment_user_stat", "_VALID_USER_STATS"),
    ("increment_server_stat", "_VALID_SERVER_STATS"),
    ("get_server_stat_today", "_VALID_SERVER_STATS"),
]


@pytest.mark.parametrize("funktion, whitelist", WHITELIST_FUNKTIONEN)
def test_spaltenname_wird_vor_dem_einsetzen_geprueft(funktion, whitelist):
    """Diese drei Funktionen setzen einen Spaltennamen per String in SQL ein -
    parametrisieren geht bei Spaltennamen nicht. Das ist genau dann sicher, wenn
    der Name vorher gegen eine feste Liste geprueft wird. Faellt die Pruefung
    weg, entsteht eine SQL-Injection, sobald der Wert je aus einer Anfrage
    stammt.
    """
    quelle = DATABASE_PY.read_text()
    fn = _funktion(quelle, funktion)
    text = ast.get_source_segment(quelle, fn) or ""
    assert whitelist in text, (
        f"{funktion}() prueft den Spaltennamen nicht gegen {whitelist}."
    )
    assert "raise" in text, (
        f"{funktion}() lehnt unbekannte Spaltennamen nicht ab, sondern setzt sie ein."
    )


@pytest.mark.parametrize("funktion, whitelist", WHITELIST_FUNKTIONEN)
def test_pruefung_steht_vor_dem_ersten_sql(funktion, whitelist):
    """Reihenfolge zaehlt: Eine Whitelist, die erst nach dem execute() greift,
    schuetzt nichts."""
    quelle = DATABASE_PY.read_text()
    text = ast.get_source_segment(quelle, _funktion(quelle, funktion)) or ""
    assert text.index(whitelist) < text.index("execute"), (
        f"In {funktion}() steht die Whitelist-Pruefung nach dem ersten SQL-Aufruf."
    )


def test_whitelists_sind_nicht_leer():
    """Eine leere Whitelist wuerde jeden Aufruf ablehnen - die Zaehler liefen
    still nicht mehr mit, ohne dass etwas auffaellt."""
    baum = ast.parse(DATABASE_PY.read_text())
    gefunden = {}
    for node in baum.body:
        if isinstance(node, ast.Assign):
            for ziel in node.targets:
                if isinstance(ziel, ast.Name) and ziel.id.startswith("_VALID_"):
                    gefunden[ziel.id] = ast.literal_eval(node.value)
    assert gefunden, "Keine _VALID_-Whitelists in database.py gefunden."
    for name, werte in gefunden.items():
        assert werte, f"{name} ist leer."
        assert all(isinstance(w, str) and w.isidentifier() for w in werte), (
            f"{name} enthaelt einen Wert, der kein gueltiger Spaltenname ist."
        )
