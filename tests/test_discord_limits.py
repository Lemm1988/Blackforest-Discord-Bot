"""Prueft, dass der Embed-Builder die von Discord dokumentierten Grenzen einhaelt.

Warum serverseitig getestet wird und nicht das Formular: create_embed.html und
send_dm.html zeigen die Zeichenzahlen live an, aber /api/embed und /api/dm sind
gewoehnliche JSON-Endpunkte. Wer eingeloggt ist und die Berechtigung hat, kann
sie direkt aufrufen und die Anzeige damit komplett umgehen. Die Anzeige ist
Komfort, die Grenze muss im Backend greifen.

Quelle aller Zahlen ist die offizielle Dokumentation, nachgeschlagen am
03.09.2026 unter https://docs.discord.com/developers/resources/message
(Abschnitt "Embed Limits"). Dort steht woertlich, dass die Summe aus title,
description, field.name, field.value, footer.text und author.name ueber alle
Embeds einer Nachricht 6000 Zeichen nicht ueberschreiten darf.

Diese Datei haelt die Zahlen fest, damit sie nicht "aufgerundet" oder aus dem
Gedaechtnis korrigiert werden. Faellt hier ein Test, ist entweder der Code falsch
oder Discord hat seine Grenzen geaendert - im zweiten Fall gehoert die neue Zahl
mit Datum und Quelle hierher.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from discord_source import load_from_app

REPO = Path(__file__).resolve().parent.parent

# Wortlaut der Dokumentation, Stand 03.09.2026.
DOKUMENTIERT = {
    "title": 256,
    "description": 4096,
    "field_name": 256,
    "field_value": 1024,
    "footer_text": 2048,
    "author_name": 256,
    "fields_max": 25,
    "total": 6000,
}


@pytest.fixture(scope="module")
def app_code():
    return load_from_app("EMBED_LIMITS", "_parse_embed_color", "_build_embed_from_payload")


@pytest.fixture
def bauen(app_code):
    return app_code["_build_embed_from_payload"]


@pytest.fixture
def farbe(app_code):
    return app_code["_parse_embed_color"]


# --------------------------------------------------------------------------
# Die Konstanten selbst
# --------------------------------------------------------------------------

@pytest.mark.parametrize("schluessel, erwartet", sorted(DOKUMENTIERT.items()))
def test_limit_entspricht_der_dokumentation(app_code, schluessel, erwartet):
    assert app_code["EMBED_LIMITS"][schluessel] == erwartet, (
        f"EMBED_LIMITS[{schluessel!r}] weicht von der Discord-Dokumentation ab."
    )


def test_keine_unbekannten_limits(app_code):
    """Gegenprobe: Kommt ein Eintrag dazu, muss er hier dokumentiert werden -
    sonst steht eine Zahl im Code, die niemand gegen die Quelle geprueft hat."""
    assert set(app_code["EMBED_LIMITS"]) == set(DOKUMENTIERT)


# --------------------------------------------------------------------------
# Einzelgrenzen: jeweils exakt am Limit erlaubt, ein Zeichen darueber nicht.
# Die Grenze selbst muss gueltig sein - ein Off-by-one waere hier sonst
# unsichtbar, und Discord lehnt genau 256 Zeichen eben NICHT ab.
# --------------------------------------------------------------------------

GRENZEN = [
    ("title", 256),
    ("description", 4096),
    ("author_name", 256),
    ("footer", 2048),
]


def _nutzlast(feld, laenge):
    """Footer allein ergibt kein gueltiges Embed (siehe Test darunter), deshalb
    bekommt jede Nutzlast einen Titel als Grundinhalt - ausser wenn genau der
    Titel geprueft wird."""
    daten = {feld: "x" * laenge}
    if feld != "title":
        daten["title"] = "Grundinhalt"
    return daten


@pytest.mark.parametrize("feld, laenge", GRENZEN)
def test_genau_am_limit_ist_erlaubt(bauen, feld, laenge):
    bauen(_nutzlast(feld, laenge))


@pytest.mark.parametrize("feld, laenge", GRENZEN)
def test_ein_zeichen_zu_viel_wird_abgelehnt(bauen, feld, laenge):
    with pytest.raises(ValueError):
        bauen(_nutzlast(feld, laenge + 1))


def test_footer_allein_ergibt_kein_embed(bauen):
    """Ein Embed nur aus Footer ist bei Discord kein sinnvoller Inhalt - der
    Builder verlangt mindestens Titel, Text, Feld oder Bild. Der Footer-Test
    oben funktioniert nur, weil er zusammen mit dieser Regel gelesen wird."""
    with pytest.raises(ValueError, match="mindestens"):
        bauen({"footer": "nur ein Footer"})


# --------------------------------------------------------------------------
# Felder
# --------------------------------------------------------------------------

def test_25_felder_sind_erlaubt(bauen):
    embed = bauen({"fields": [{"name": f"F{i}", "value": "v"} for i in range(25)]})
    assert len(embed.fields) == 25


def test_26_felder_werden_abgelehnt(bauen):
    with pytest.raises(ValueError, match="25"):
        bauen({"fields": [{"name": f"F{i}", "value": "v"} for i in range(26)]})


def test_leere_felder_werden_uebersprungen(bauen):
    """Discord lehnt ein Feld mit leerem name oder value ab (Invalid Form Body).
    Das Formular schickt aber leere Zeilen mit, wenn jemand ein Feld anlegt und
    nicht ausfuellt - die duerfen nicht durchgereicht werden."""
    embed = bauen({
        "title": "T",
        "fields": [
            {"name": "echt", "value": "wert"},
            {"name": "", "value": "ohne Namen"},
            {"name": "ohne Wert", "value": ""},
            {"name": "   ", "value": "   "},
        ],
    })
    assert [f["name"] for f in embed.fields] == ["echt"]


def test_feldname_und_feldwert_haben_eigene_grenzen(bauen):
    bauen({"fields": [{"name": "x" * 256, "value": "y" * 1024}]})
    with pytest.raises(ValueError, match="Feldname"):
        bauen({"fields": [{"name": "x" * 257, "value": "y"}]})
    with pytest.raises(ValueError, match="Feldwert"):
        bauen({"fields": [{"name": "x", "value": "y" * 1025}]})


def test_inline_wird_als_wahrheitswert_uebernommen(bauen):
    embed = bauen({"fields": [
        {"name": "a", "value": "1", "inline": True},
        {"name": "b", "value": "2"},
    ]})
    assert [f["inline"] for f in embed.fields] == [True, False]


# --------------------------------------------------------------------------
# Gesamtsumme - die Grenze, die man am leichtesten uebersieht, weil jedes
# Einzelfeld fuer sich in Ordnung ist.
# --------------------------------------------------------------------------

def test_summe_ueber_6000_wird_abgelehnt(bauen):
    """Jedes Feld einzeln erlaubt, zusammen zu gross: 4096 Beschreibung + 25
    Felder a 100 Zeichen = 6596."""
    with pytest.raises(ValueError, match="insgesamt"):
        bauen({
            "description": "x" * 4096,
            "fields": [{"name": "n" * 20, "value": "v" * 80} for _ in range(25)],
        })


def test_summe_zaehlt_dieselben_sechs_felder_wie_discord(bauen):
    """Discord zaehlt title, description, field.name, field.value, footer.text
    und author.name - und sonst nichts. Lange URLs zaehlen also nicht mit und
    duerfen ein sonst gueltiges Embed nicht kippen."""
    embed = bauen({
        "title": "t" * 100,
        "description": "d" * 200,
        "author_name": "a" * 50,
        "footer": "f" * 60,
        "fields": [{"name": "n" * 10, "value": "v" * 40}],
        "url": "https://example.invalid/" + "p" * 900,
        "banner": "https://example.invalid/" + "q" * 900,
    })
    assert len(embed) == 100 + 200 + 50 + 60 + 10 + 40


def test_genau_6000_ist_noch_erlaubt(bauen):
    embed = bauen({"description": "x" * 4096, "title": "y" * 256, "footer": "z" * 1648})
    assert len(embed) == 6000


# --------------------------------------------------------------------------
# Leeres Embed
# --------------------------------------------------------------------------

def test_voellig_leeres_embed_wird_abgelehnt(bauen):
    """Discord antwortet auf ein Embed ohne jeden Inhalt mit einem Fehler. Besser
    hier abfangen als eine 400 aus der API durchreichen."""
    with pytest.raises(ValueError, match="mindestens"):
        bauen({})


@pytest.mark.parametrize("feld", ["title", "description", "thumbnail", "banner", "author_name"])
def test_ein_einziges_ausgefuelltes_feld_genuegt(bauen, feld):
    bauen({feld: "x"})


def test_nur_leerraum_zaehlt_nicht_als_inhalt(bauen):
    with pytest.raises(ValueError, match="mindestens"):
        bauen({"title": "   ", "description": "\n\t "})


# --------------------------------------------------------------------------
# Farbe
# --------------------------------------------------------------------------

@pytest.mark.parametrize("eingabe, erwartet", [
    ("#2f7a4d", 0x2F7A4D),
    ("2f7a4d", 0x2F7A4D),
    ("  #FFFFFF  ", 0xFFFFFF),
    ("000000", 0),
])
def test_farbe_wird_zu_integer(farbe, eingabe, erwartet):
    assert farbe(eingabe) == erwartet


@pytest.mark.parametrize("leer", ["", "   ", None])
def test_leere_farbe_ergibt_keine_farbe(farbe, leer):
    """None ist wichtig und nicht dasselbe wie 0: 0 waere pechschwarz, None
    laesst Discord seinen dezenten Standard-Grauton zeichnen."""
    assert farbe(leer) is None


@pytest.mark.parametrize("kaputt", ["gg7a4d", "#xyz", "rot"])
def test_ungueltige_farbe_wird_abgelehnt(farbe, kaputt):
    with pytest.raises(ValueError, match="Hex"):
        farbe(kaputt)


def test_farbe_ausserhalb_von_24_bit_wird_abgelehnt(farbe):
    """Discord kennt nur 24-Bit-RGB. 0x1000000 waere syntaktisch gueltiges Hex,
    aber ausserhalb des Bereichs - discord.py wuerde es kommentarlos annehmen
    und die API antwortet dann mit Invalid Form Body."""
    assert farbe("FFFFFF") == 0xFFFFFF
    with pytest.raises(ValueError, match="Bereich"):
        farbe("1000000")


# --------------------------------------------------------------------------
# Zeitstempel
# --------------------------------------------------------------------------

def test_zeitstempel_ist_zeitzonenbewusst(bauen):
    """Ein naiver Zeitstempel wuerde von Discord als UTC gelesen und die Uhrzeit
    im Embed um den lokalen Offset verschieben - im Sommer zwei Stunden."""
    embed = bauen({"title": "T", "timestamp": True})
    assert embed.timestamp is not None
    assert embed.timestamp.utcoffset() is not None


def test_ohne_zeitstempel_bleibt_er_leer(bauen):
    assert bauen({"title": "T"}).timestamp is None


# --------------------------------------------------------------------------
# Frontend und Backend halten dieselben Zahlen doppelt vor. Das ist hier
# vertretbar (das Formular zeigt Restzeichen live an, ohne den Server zu
# fragen), aber es muss zusammenbleiben: Zeigt das Formular 4096 an, waehrend
# der Server bei 2000 abbricht, sieht der Nutzer seinen fertigen Text und
# bekommt beim Absenden eine Ablehnung, die er nicht nachvollziehen kann.
# --------------------------------------------------------------------------

JS_DATEI = REPO / "backend" / "static" / "js" / "embed-builder.js"

# Schreibweise im JavaScript -> Schluessel in EMBED_LIMITS
JS_ZUORDNUNG = {
    "title": "title",
    "description": "description",
    "fieldName": "field_name",
    "fieldValue": "field_value",
    "footer": "footer_text",
    "authorName": "author_name",
    "fieldsMax": "fields_max",
    "total": "total",
}


def _js_limits() -> dict[str, int]:
    text = JS_DATEI.read_text()
    gefunden = {}
    for js_name in JS_ZUORDNUNG:
        treffer = re.search(rf"\b{js_name}\s*:\s*(\d+)", text)
        if treffer:
            gefunden[js_name] = int(treffer.group(1))
    return gefunden


def test_frontend_kennt_alle_limits():
    fehlend = set(JS_ZUORDNUNG) - set(_js_limits())
    assert not fehlend, (
        f"In embed-builder.js fehlen Limits: {sorted(fehlend)}. Ohne sie zeigt das "
        "Formular keine Grenze an, der Server lehnt aber trotzdem ab."
    )


@pytest.mark.parametrize("js_name, py_name", sorted(JS_ZUORDNUNG.items()))
def test_frontend_und_backend_stimmen_ueberein(app_code, js_name, py_name):
    js = _js_limits()
    assert js[js_name] == app_code["EMBED_LIMITS"][py_name], (
        f"embed-builder.js sagt {js_name}={js[js_name]}, das Backend sagt "
        f"{py_name}={app_code['EMBED_LIMITS'][py_name]}. Das Formular wuerde etwas "
        "erlauben, das der Server ablehnt (oder umgekehrt zu frueh bremsen)."
    )
