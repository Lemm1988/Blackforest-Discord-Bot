"""Der /help-Befehl (Plan 01, Schritt 2 — umgesetzt 03.09.2026).

Zwei Vorgänger zeigten **jedem alles**: der entfernte `>bothelp` und die
eingebaute Hilfe von discord.py. `grant_access` und `revoke_access` standen
damit in der Nachricht, die jedes Mitglied bekam.

Vorgabe Patrick: Wer einen Befehl nicht benutzen darf, sieht ihn gar nicht —
nicht ausgegraut, nicht mit Hinweis. Und die Gliederung folgt den Modulen, weil
die sich später einzeln installieren lassen sollen.

Genau das wird hier geprüft, mit gestellten Daten statt mit einem laufenden Bot.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

import hilfe  # noqa: E402

COGS = REPO / "bot" / "cogs"


# --------------------------------------------------------------------------
# Beispieldaten in der Form, die der Cog aus discord.py zusammenstellt
# --------------------------------------------------------------------------

def _modul(label, reihenfolge, befehle):
    return {"label": label, "reihenfolge": reihenfolge, "befehle": befehle}


def _b(name, kurz="tut etwas", recht=None, lang=None):
    eintrag = {"name": name, "kurz": kurz, "recht": recht, "verwendung": f"/{name}"}
    if lang:
        eintrag["lang"] = lang
    return eintrag


MODULE = [
    _modul("🌿 Pflanzenquiz", 1, [_b("frage"), _b("quiz_rangliste")]),
    _modul("📈 XP & Level", 2, [_b("xp"), _b("serverxp_rangliste")]),
    _modul("🔧 Administration", 9, [
        _b("my_access"),
        _b("grant_access", recht="is_admin"),
        _b("dm", recht="send_dm"),
    ]),
]

NIEMAND = lambda recht: False          # noqa: E731 - normales Mitglied
ADMIN = lambda recht: recht == "is_admin"   # noqa: E731
MODERATOR = lambda recht: recht == "send_dm"  # noqa: E731


# --------------------------------------------------------------------------
# Rechtefilterung — der eigentliche Punkt
# --------------------------------------------------------------------------

def test_normales_mitglied_sieht_keine_adminbefehle():
    text = hilfe.uebersicht(MODULE, NIEMAND)
    for verboten in ("grant_access", "dm"):
        assert f"/{verboten}`" not in text, (
            f"/{verboten} taucht bei einem Mitglied ohne Rechte auf."
        )


def test_normales_mitglied_sieht_die_offenen_befehle():
    text = hilfe.uebersicht(MODULE, NIEMAND)
    for erlaubt in ("frage", "xp", "quiz_rangliste", "serverxp_rangliste", "my_access"):
        assert f"`/{erlaubt}`" in text


def test_admin_sieht_seine_befehle():
    text = hilfe.uebersicht(MODULE, ADMIN)
    assert "`/grant_access`" in text
    assert "`/dm`" not in text, "Admin hat send_dm nicht - der Befehl gehoert nicht in seine Hilfe."


def test_moderator_sieht_nur_was_ihm_gehoert():
    text = hilfe.uebersicht(MODULE, MODERATOR)
    assert "`/dm`" in text
    assert "`/grant_access`" not in text


def test_leeres_modul_verschwindet_ganz():
    """Eine leere Überschrift „Administration" würde verraten, dass es dort
    etwas gibt — auch ohne die Befehle zu nennen."""
    nur_admin = [_modul("🔧 Administration", 9, [_b("grant_access", recht="is_admin")])]
    text = hilfe.uebersicht(nur_admin, NIEMAND)
    assert "Administration" not in text


def test_module_bleiben_in_ihrer_reihenfolge():
    text = hilfe.uebersicht(MODULE, ADMIN)
    assert text.index("Pflanzenquiz") < text.index("XP & Level") < text.index("Administration")


def test_ohne_jeden_sichtbaren_befehl_bleibt_die_hilfe_verstaendlich():
    text = hilfe.uebersicht([_modul("🔧 Administration", 9, [_b("x", recht="is_admin")])], NIEMAND)
    assert hilfe.LEER in text


# --------------------------------------------------------------------------
# Die Fehlermeldung darf nichts verraten
# --------------------------------------------------------------------------

def test_unbekannter_befehl_listet_nur_erlaubtes():
    """Sonst wäre `/help quatsch` ein Schleichweg, um die Adminbefehle zu
    erfahren."""
    text = hilfe.nicht_gefunden("quatsch", ["frage", "xp"])
    assert "quatsch" in text
    assert "`/frage`" in text and "`/xp`" in text
    assert "grant_access" not in text


def test_unbekannter_befehl_ohne_liste_bleibt_hoeflich():
    assert hilfe.nicht_gefunden("quatsch", []).startswith("Einen Befehl")


# --------------------------------------------------------------------------
# Einzelhilfe
# --------------------------------------------------------------------------

def test_einzelhilfe_nutzt_den_langen_text():
    text = hilfe.einzelhilfe(_b("frage", kurz="kurz", lang="Der ausführliche Text."))
    assert "Der ausführliche Text." in text
    assert "/frage" in text


def test_einzelhilfe_faellt_auf_die_kurzfassung_zurueck():
    """Ein neu hinzugekommener Befehl ohne langen Text darf nicht mit einer
    leeren Hilfe dastehen."""
    text = hilfe.einzelhilfe(_b("neu", kurz="Macht etwas Neues."))
    assert "Macht etwas Neues." in text


def test_einzelhilfe_nennt_die_noetige_berechtigung():
    text = hilfe.einzelhilfe(_b("dm", recht="send_dm"))
    assert "send_dm" in text


def test_einzelhilfe_zeigt_die_verwendung():
    eintrag = _b("grant_access")
    eintrag["verwendung"] = "/grant_access <user> <permission>"
    assert "/grant_access <user> <permission>" in hilfe.einzelhilfe(eintrag)


# --------------------------------------------------------------------------
# Gegenprobe im echten Code
# --------------------------------------------------------------------------

def _befehle_mit_extras():
    """Liest Name, Beschreibung und extras aller Befehle aus den Cogs."""
    gefunden = {}
    for datei in sorted(COGS.glob("*.py")):
        baum = ast.parse(datei.read_text())
        for node in ast.walk(baum):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for d in node.decorator_list:
                if not (isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "hybrid_command"):
                    continue
                kw = {k.arg: k.value for k in d.keywords}
                if "name" not in kw:
                    continue
                name = kw["name"].value
                extras = ast.literal_eval(kw["extras"]) if "extras" in kw else {}
                beschreibung = kw["description"].value if "description" in kw else ""
                gefunden[name] = {"datei": datei.name, "beschreibung": beschreibung, **extras}
    return gefunden


def test_jeder_befehl_hat_eine_kurzbeschreibung():
    """Sie steht sowohl in der Hilfe als auch im /-Menü von Discord — fehlt sie,
    steht der Befehl dort nackt da."""
    for name, daten in _befehle_mit_extras().items():
        assert len(daten["beschreibung"]) > 10, f"/{name} hat keine brauchbare Beschreibung."


def test_jeder_befehl_hat_einen_ausfuehrlichen_text():
    for name, daten in _befehle_mit_extras().items():
        assert daten.get("hilfe"), f"/{name} hat keinen Text für `/help {name}`."


def test_eingeschraenkte_befehle_deklarieren_ihr_recht():
    """Ohne extras['recht'] wüsste die Hilfe nicht, dass sie den Befehl
    verstecken muss — er stünde dann bei jedem."""
    befehle = _befehle_mit_extras()
    for name in ("grant_access", "revoke_access", "dm"):
        assert befehle[name].get("recht"), (
            f"/{name} ist eingeschränkt, deklariert aber kein Recht - er würde in "
            "der Hilfe bei jedem auftauchen."
        )


def test_offene_befehle_deklarieren_kein_recht():
    befehle = _befehle_mit_extras()
    for name in ("frage", "xp", "zitat", "pflanze", "my_access", "help"):
        assert not befehle[name].get("recht"), f"/{name} sollte für alle offen sein."


def test_eingebaute_hilfe_ist_abgeschaltet():
    """Sonst belegt discord.py den Namen "help" mit einem Prefix-Befehl und
    unser eigener /help liesse sich gar nicht registrieren."""
    quelle = (REPO / "bot" / "main.py").read_text()
    assert "help_command=None" in quelle


@pytest.mark.parametrize("cog, label", [
    ("quiz.py", "Pflanzenquiz"),
    ("xp_level.py", "XP & Level"),
    ("admin.py", "Administration"),
    ("hilfe.py", "Hilfe"),
])
def test_module_tragen_ihr_label_selbst(cog, label):
    """Das Label steht am Cog, nicht in einer zentralen Liste - damit ein
    entferntes Modul auch aus der Hilfe verschwindet, ohne dass jemand daran
    denken muss."""
    quelle = (COGS / cog).read_text()
    assert "MODUL_LABEL" in quelle and label in quelle
