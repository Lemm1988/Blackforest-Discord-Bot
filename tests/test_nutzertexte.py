"""Texte für Nutzer und Moderatoren enthalten keine Entwicklerhistorie.

Vorgabe Patrick (03.09.2026): Formulierungen wie „wurde durch `/` ersetzt" oder
„`>` gibt es nicht mehr" sind in Oberflächentexten fehl am Platz — „das sind
Infos die ich als Entwickler habe". Ein Text beschreibt den heutigen Zustand,
nicht den Weg dorthin.

Konkret hat das nach der Umstellung auf Slash-Befehle drei Stellen im Backend
betroffen, von denen eine auf `>hallo` verwies — einen Befehl, den es
überhaupt nicht mehr gibt. Wer die Seite las, suchte danach vergeblich.

Die Historie geht nicht verloren, sie gehört nur woandershin: Code-Kommentare,
Commit-Nachrichten und die Doku unter `docs/`. Deshalb prüft dieser Test
ausschließlich das, was Nutzer tatsächlich zu sehen bekommen — Kommentare in
Templates (`{# … #}`, `<!-- … -->`) sind ausgenommen.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = sorted((REPO / "backend" / "templates").glob("*.html"))


def _sichtbarer_text(quelle: str) -> str:
    """Entfernt Jinja- und HTML-Kommentare sowie script/style-Blöcke.

    Dort steht Entwicklerkontext zu Recht — er richtet sich an den, der den
    Code liest, nicht an den, der die Seite benutzt.
    """
    ohne = re.sub(r"\{#.*?#\}", "", quelle, flags=re.S)
    ohne = re.sub(r"<!--.*?-->", "", ohne, flags=re.S)
    ohne = re.sub(r"<script\b.*?</script>", "", ohne, flags=re.S | re.I)
    ohne = re.sub(r"<style\b.*?</style>", "", ohne, flags=re.S | re.I)
    return ohne


@pytest.mark.parametrize("datei", TEMPLATES, ids=lambda p: p.name)
def test_keine_verweise_auf_das_alte_befehlspraefix(datei):
    """Der Bot hört nur noch auf `/`. Ein Text, der `>frage` nennt, schickt den
    Leser zu etwas, das nicht mehr funktioniert."""
    text = _sichtbarer_text(datei.read_text())
    treffer = re.findall(r"&gt;[a-zA-Z_]{2,}|`>[a-zA-Z_]{2,}", text)
    assert not treffer, (
        f"{datei.name} verweist auf einen Befehl in alter Schreibweise: {treffer}. "
        "Der Bot hört nur noch auf '/'."
    )


@pytest.mark.parametrize("datei", TEMPLATES, ids=lambda p: p.name)
def test_kein_entwicklerjargon(datei):
    """Begriffe, die nur im Quelltext etwas bedeuten. Wer die Seite benutzt,
    kann damit nichts anfangen."""
    text = _sichtbarer_text(datei.read_text())
    verboten = ["Fallback", "Regression", "Legacy", "deprecated", "Spec 1", "Refactor"]
    gefunden = [w for w in verboten if w.lower() in text.lower()]
    assert not gefunden, (
        f"{datei.name} benutzt Entwicklerbegriffe in einem Text für Nutzer: {gefunden}."
    )


@pytest.mark.parametrize("datei", TEMPLATES, ids=lambda p: p.name)
def test_keine_umbauhistorie(datei):
    """„… gibt es nicht mehr", „wurde ersetzt durch", „seit Version X" — der
    Leser will wissen, wie es JETZT geht, nicht wie es früher war."""
    text = _sichtbarer_text(datei.read_text())
    muster = [
        r"gibt es (nicht mehr|nicht länger)",
        r"wurde .{0,30}(ersetzt|umbenannt|entfernt)",
        r"(hieß|hiess) (früher|vorher)",
        r"seit Version",
        r"nicht mehr unterstützt",
    ]
    for m in muster:
        treffer = re.search(m, text, re.I)
        assert not treffer, (
            f"{datei.name} erklärt einem Nutzer die Umbauhistorie: {treffer.group(0)!r}. "
            "Solche Hinweise gehören in Code-Kommentare oder docs/, nicht auf die Seite."
        )


def test_die_pruefung_findet_ueberhaupt_etwas():
    """Selbsttest: Fände der Scanner keine Templates mehr, wären die Tests oben
    still grün und wertlos."""
    assert len(TEMPLATES) >= 20, f"Nur {len(TEMPLATES)} Templates gefunden - Pfad falsch?"
