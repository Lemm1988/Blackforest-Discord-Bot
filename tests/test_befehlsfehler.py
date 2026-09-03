"""Fehlermeldungen für Befehle (Plan 04, umgesetzt 03.09.2026).

Vorher passierte bei einem falsch benutzten Befehl **gar nichts**. Wer
`/grant_access` ohne Argumente abschickte, bekam keine Antwort, keine Meldung,
nichts - discord.py schrieb es auf stderr, und das war seinerseits unsichtbar,
weil dem Dienst PYTHONUNBUFFERED fehlte.

Diese Fehlerklasse ist in diesem Projekt teuer geworden: Kick, Ban und Timeout
taten monatelang nichts, weil die WebView der App Bestätigungsdialoge
verschluckte. Für den Menschen davor sieht beides identisch aus - es passiert
nichts, und nichts sagt warum. Deshalb ist hier abgesichert, dass jeder
absehbare Fehlerfall auch tatsächlich etwas sagt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

import fehler  # noqa: E402

MAIN_PY = REPO / "bot" / "main.py"
ADMIN_PY = REPO / "bot" / "cogs" / "admin.py"


# --------------------------------------------------------------------------
# Die Texte
# --------------------------------------------------------------------------

def test_unbekannter_befehl_bleibt_still():
    """Der einzige Fall, in dem geschwiegen wird - und das mit Absicht. Sonst
    antwortet der Bot auf jede Nachricht, die zufällig mit dem Präfix beginnt:
    ein zitierter Text, ein Pfeil, ein Smiley."""
    assert fehler.meldung("unbekannter_befehl") is None


@pytest.mark.parametrize("art, kontext", [
    ("fehlende_berechtigung", {}),
    ("fehlendes_argument", {"parameter": "user"}),
    ("falsches_argument", {}),
    ("cooldown", {"sekunden": 5}),
    ("nur_auf_server", {}),
    ("intern", {}),
])
def test_jeder_andere_fall_sagt_etwas(art, kontext):
    """Das ist der Kern: kein absehbarer Fehler darf stumm bleiben."""
    text = fehler.meldung(art, **kontext)
    assert text, f"Fehlerart {art!r} liefert keinen Text."
    assert len(text) > 10, f"Text zu {art!r} ist zu knapp, um zu helfen: {text!r}"


def test_unbekannte_fehlerart_faellt_auf_den_generischen_text_zurueck():
    """Wird später eine neue Ausnahme zugeordnet und die Textart vergessen, soll
    der Nutzer trotzdem etwas sehen statt gar nichts."""
    assert fehler.meldung("gibtsnicht") == fehler.GENERISCH


def test_berechtigungsmeldung_uebernimmt_den_grund():
    """Die Prüfungen in admin.py geben mit, WELCHE Berechtigung fehlt. Das ist
    hilfreicher als ein pauschales 'keine Berechtigung'."""
    text = fehler.meldung("fehlende_berechtigung", grund="⛔ Dir fehlt die Berechtigung **send_dm**.")
    assert "send_dm" in text


def test_berechtigungsmeldung_ohne_grund_bleibt_verstaendlich():
    text = fehler.meldung("fehlende_berechtigung", grund=None)
    assert text and "Berechtigung" in text


def test_fehlendes_argument_nennt_parameter_und_verwendung():
    """Ohne beides muss der Nutzer raten oder die Hilfe suchen."""
    text = fehler.meldung(
        "fehlendes_argument",
        parameter="permission",
        verwendung="/grant_access <user> <permission>",
    )
    assert "permission" in text
    assert "/grant_access <user> <permission>" in text


def test_cooldown_nennt_die_wartezeit():
    assert "5s" in fehler.meldung("cooldown", sekunden=5.4)


def test_cooldown_rundet_nie_auf_null():
    """0.3 Sekunden Restzeit als '0s' anzuzeigen wäre verwirrend - es sieht aus,
    als müsste man nicht warten, obwohl der nächste Versuch scheitert."""
    assert "1s" in fehler.meldung("cooldown", sekunden=0.3)


def test_interner_fehler_verraet_keine_details():
    """Ein Python-Traceback im Kanal ist unschön und verrät Interna. Der
    vollständige Fehler gehört ins Log."""
    text = fehler.meldung("intern")
    for verraeterisch in ("Traceback", "File \"", "Error:", ".py", "raise"):
        assert verraeterisch not in text, f"Interne Meldung enthält {verraeterisch!r}."


# --------------------------------------------------------------------------
# Der Verwendungshinweis
# --------------------------------------------------------------------------

def test_verwendung_zeigt_auf_slash_nicht_auf_prefix():
    """Die Prefix-Befehle sind ein Auslaufmodell. Ein Hinweis, der auf '>' zeigt,
    wäre schon jetzt der falsche Weg."""
    hinweis = fehler.verwendung("grant_access", "<user> <permission>")
    assert hinweis.startswith("/")
    # Nicht auf ">" im ganzen Text pruefen: Die Signatur klammert Parameter in
    # spitze Klammern, "<user>" enthaelt also legitimerweise eins. Gemeint ist
    # das Befehlspraefix am Anfang.
    assert not hinweis.startswith(">")


def test_verwendung_ohne_argumente():
    assert fehler.verwendung("xp", "") == "/xp"
    assert fehler.verwendung("xp") == "/xp"


# --------------------------------------------------------------------------
# Die Verdrahtung - ohne sie wären die Texte oben wirkungslos
# --------------------------------------------------------------------------

def test_es_gibt_einen_zentralen_handler():
    quelle = MAIN_PY.read_text()
    assert "async def on_command_error" in quelle, (
        "Kein on_command_error-Handler - dann bleiben fehlerhafte Befehle wieder stumm."
    )
    assert "bot.tree.error" in quelle, (
        "Kein Handler für reine Slash-Befehle - dort greift on_command_error nicht."
    )


@pytest.mark.parametrize("ausnahme", [
    "CommandNotFound", "CheckFailure", "MissingRequiredArgument",
    "BadArgument", "CommandOnCooldown", "NoPrivateMessage",
])
def test_handler_behandelt_die_haeufigen_ausnahmen(ausnahme):
    assert ausnahme in MAIN_PY.read_text(), (
        f"{ausnahme} wird nicht behandelt und fiele in den generischen Zweig - "
        "der Nutzer bekäme dann keine brauchbare Hilfe."
    )


def test_meldungen_gehen_nur_an_den_aufrufer():
    """Vorgabe Patrick, 03.09.2026: Eine Absage muss nicht öffentlich im Kanal
    stehen."""
    quelle = MAIN_PY.read_text()
    block = quelle[quelle.index("async def on_command_error"):quelle.index("async def on_app_command_error")]
    assert "ephemeral=True" in block


def test_handler_faellt_nicht_selbst_um():
    """Ein Fehler in der Fehlerbehandlung verschluckt die eigentliche Meldung
    oder erzeugt eine Schleife."""
    quelle = MAIN_PY.read_text()
    block = quelle[quelle.index("async def on_command_error"):quelle.index("async def on_app_command_error")]
    assert "except discord.HTTPException" in block, (
        "Das Senden der Fehlermeldung ist nicht abgesichert - es kann selbst "
        "scheitern (abgelaufene Interaktion, fehlende Schreibrechte)."
    )


def test_pruefungen_antworten_nicht_mehr_selbst():
    """Der Grund für den Umbau: Discord erlaubt auf eine Slash-Anfrage GENAU EINE
    Antwort. Antwortet die Prüfung selbst und danach die Fehlerbehandlung, gibt
    es 'Interaction has already been responded to' - und der Nutzer sieht 'Die
    Anwendung reagiert nicht'."""
    quelle = ADMIN_PY.read_text()
    block = quelle[quelle.index("def is_admin"):quelle.index("class Admin")]
    assert "ctx.send" not in block, (
        "Eine Berechtigungsprüfung antwortet wieder selbst - das kollidiert bei "
        "Slash-Befehlen mit der zentralen Fehlerbehandlung."
    )
    assert block.count("raise commands.CheckFailure") == 2, (
        "Beide Prüfungen müssen eine CheckFailure mit Begründung auslösen."
    )


def test_pruefungen_geben_eine_begruendung_mit():
    """Ein nacktes CheckFailure() ohne Text würde im Handler zu einer
    nichtssagenden Standardmeldung führen."""
    quelle = ADMIN_PY.read_text()
    block = quelle[quelle.index("def is_admin"):quelle.index("class Admin")]
    assert 'CheckFailure("⛔' in block or "CheckFailure(\n" in block
    assert "Admins" in block and "Berechtigung" in block
