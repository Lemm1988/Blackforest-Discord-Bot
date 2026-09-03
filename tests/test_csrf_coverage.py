"""Stellt sicher, dass jede zustandsaendernde Anfrage ein CSRF-Token mitschickt.

Das Backend erzwingt in _enforce_csrf() ein Token fuer jedes POST/PUT/PATCH/
DELETE eines eingeloggten Nutzers. Fehlt es auf der Seite, passiert etwas
Unangenehmes: Die Anfrage wird abgewiesen, der Nutzer bekommt (bei Formularen)
eine Meldung oder (bei fetch) gar nichts, und die Aktion laeuft ins Leere.

Genau diese Fehlerklasse hat in diesem Projekt schon einmal Tage gekostet: Kick,
Ban und Timeout taten nichts, weil die WebView der App die confirm()-Dialoge
verschluckte. Ein fehlendes CSRF-Token sieht fuer den Nutzer identisch aus - ein
Klick ohne Wirkung, ohne Fehlermeldung.

Es gibt zwei zulaessige Wege, und der Test muss beide kennen:

1. Klassische Formulare und fetch(..., body: new FormData(form)) senden das
   versteckte Feld <input name="csrf_token"> mit. Der Server liest es ueber
   request.form.get("csrf_token").
2. fetch mit JSON-Koerper hat kein Formular und schickt stattdessen den Header
   X-CSRF-Token, den der Server ebenfalls akzeptiert.

Wer nur Weg 2 kennt, haelt faelschlich jedes FormData-fetch fuer ungeschuetzt
(so geschehen bei der Durchsicht von dbot.html - das Formular dort traegt das
Token, nur eben im Koerper statt im Header).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "backend" / "templates"
JS = REPO / "backend" / "static" / "js"
APP_PY = REPO / "backend" / "app.py"

SEITEN = sorted(TEMPLATES.glob("*.html")) + sorted(JS.glob("*.js"))


def _klassische_post_formulare(text: str):
    """Liefert (Zeile, Koerper) fuer jedes <form method="post">.

    Nur diese Bauart schickt der Browser selbst ab; sie MUSS das versteckte Feld
    tragen. Formulare ohne method-Attribut werden per JavaScript verschickt und
    unten bei den fetch-Aufrufen geprueft - dort kann das Token auch im Header
    stecken, dann waere ein verstecktes Feld ueberfluessig.
    """
    for m in re.finditer(r"<form\b[^>]*>", text, re.I):
        if not re.search(r'method\s*=\s*["\']?post', m.group(0), re.I):
            continue
        rest = text[m.end():]
        ende = rest.lower().find("</form>")
        yield text[:m.start()].count("\n") + 1, rest[: ende if ende != -1 else len(rest)]


def _post_fetches(text: str):
    """Liefert (Zeile, Block) fuer jeden fetch-Aufruf mit method: 'POST'."""
    for m in re.finditer(r"fetch\s*\(", text):
        block = text[m.start() : m.start() + 800]
        if re.search(r"method\s*:\s*['\"]POST", block, re.I):
            yield text[: m.start()].count("\n") + 1, block


@pytest.mark.parametrize("datei", SEITEN, ids=lambda p: p.name)
def test_jedes_klassische_post_formular_traegt_ein_csrf_token(datei):
    text = datei.read_text()
    for zeile, koerper in _klassische_post_formulare(text):
        assert "csrf_token" in koerper, (
            f"{datei.name}:{zeile}: <form method=\"post\"> ohne verstecktes "
            "csrf_token-Feld. Der Server weist die Anfrage ab, fuer den Nutzer "
            "bleibt der Klick wirkungslos. Ergaenze: "
            '<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">'
        )


@pytest.mark.parametrize("datei", SEITEN, ids=lambda p: p.name)
def test_jeder_post_fetch_hat_ein_token(datei):
    """Zwei zulaessige Wege, beide werden akzeptiert.

    Der FormData-Weg wird bewusst auf Dateiebene geprueft und nicht im
    fetch-Block: In dbot.html entsteht das FormData-Objekt einige Zeilen ueber
    dem fetch und wird als Variable uebergeben. Eine Pruefung, die nur den
    fetch-Aufruf ansieht, haelt das faelschlich fuer ungeschuetzt - genau dieser
    Fehlschluss ist bei der Durchsicht am 03.09.2026 passiert.
    """
    text = datei.read_text()
    seite_nutzt_formdata = "FormData" in text and "csrf_token" in text
    for zeile, block in _post_fetches(text):
        hat_header = "X-CSRF-Token" in block
        assert hat_header or seite_nutzt_formdata, (
            f"{datei.name}:{zeile}: fetch mit POST, aber kein X-CSRF-Token-Header "
            "und auch kein FormData mit csrf_token-Feld auf der Seite. Der Server "
            "antwortet mit 403, bei JSON-Aufrufen ohne sichtbare Meldung."
        )


def test_die_pruefung_findet_ueberhaupt_etwas():
    """Selbsttest der beiden Scanner: Faende das Muster nichts mehr - etwa weil
    Formulare kuenftig anders erzeugt werden - waeren die Tests oben still
    gruen und wertlos."""
    formulare = sum(len(list(_klassische_post_formulare(d.read_text()))) for d in SEITEN)
    fetches = sum(len(list(_post_fetches(d.read_text()))) for d in SEITEN)
    assert formulare >= 20, f"Nur {formulare} POST-Formulare gefunden - Scanner defekt?"
    assert fetches >= 3, f"Nur {fetches} POST-fetch-Aufrufe gefunden - Scanner defekt?"


# --------------------------------------------------------------------------
# Die Serverseite selbst - ohne sie waeren die Tests oben wertlos.
# --------------------------------------------------------------------------

def test_server_prueft_alle_zustandsaendernden_methoden():
    quelle = APP_PY.read_text()
    block = quelle[quelle.index("def _enforce_csrf"):]
    block = block[: block.index("# --- Auth ---")]
    for methode in ("POST", "PUT", "PATCH", "DELETE"):
        assert methode in block, (
            f"_enforce_csrf() prueft {methode} nicht - eine Route mit dieser "
            "Methode waere ungeschuetzt."
        )


def test_server_vergleicht_zeitkonstant():
    """Ein gewoehnlicher ==-Vergleich bricht beim ersten falschen Zeichen ab und
    verraet ueber die Antwortzeit, wie viele Zeichen stimmten. secrets.compare_digest
    laeuft unabhaengig vom Inhalt gleich lang."""
    quelle = APP_PY.read_text()
    block = quelle[quelle.index("def _enforce_csrf"):]
    block = block[: block.index("# --- Auth ---")]
    assert "compare_digest" in block, (
        "_enforce_csrf() vergleicht das Token nicht mit secrets.compare_digest."
    )


def test_server_akzeptiert_beide_wege():
    quelle = APP_PY.read_text()
    block = quelle[quelle.index("def _enforce_csrf"):]
    block = block[: block.index("# --- Auth ---")]
    assert "X-CSRF-Token" in block and 'request.form.get("csrf_token")' in block, (
        "Der Server muss Header UND Formularfeld akzeptieren - die Seiten nutzen beides."
    )


def test_leeres_token_gilt_nicht_als_gueltig():
    """Waere expected leer (z.B. Session ohne Token) und provided ebenfalls leer,
    duerfte das nicht als Uebereinstimmung durchgehen. Der Code faengt das ueber
    die Wahrheitspruefung beider Werte ab, bevor er vergleicht."""
    quelle = APP_PY.read_text()
    block = quelle[quelle.index("def _enforce_csrf"):]
    block = block[: block.index("# --- Auth ---")]
    assert "if expected and provided and" in block, (
        "Vor dem Vergleich muss geprueft werden, dass beide Werte ueberhaupt "
        "gesetzt sind - sonst wuerden zwei leere Werte als gueltig gelten."
    )
