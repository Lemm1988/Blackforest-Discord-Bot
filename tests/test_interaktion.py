"""Die Drei-Sekunden-Frist von Discord und die Vorab-Bestaetigung.

Hintergrund: Am 04.09.2026 meldete ein Mitglied, /frage antworte mit "Die
Anwendung reagiert nicht". Im Log stand Fehler 10062, "Unknown interaction" -
Discord hatte den Sitzungsschluessel verworfen, weil der Bot das
Drei-Sekunden-Fenster gerissen hatte. Danach war auch keine Fehlermeldung mehr
zustellbar, weshalb der Nutzer ausschliesslich Discords eigenen Text sah.

Die Vorab-Bestaetigung ("deferred response") kam bis dahin im ganzen Projekt
kein einziges Mal vor. Alle fuenfzehn Befehle waren also anfaellig, sobald der
Pi kurz ausgelastet oder das Netz langsam war - /frage hat es nur als erstes
getroffen.

Neben der reinen Logik sichert die zweite Haelfte dieser Datei das ab, was
erfahrungsgemaess wieder auseinanderlaeuft: die Angabe am Befehl und das, was
der Befehl tatsaechlich sendet. Eine falsch angegebene Antwortart laesst sich
nachtraeglich nicht mehr korrigieren - die Bestaetigung legt endgueltig fest,
ob die Antwort oeffentlich ist.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

import interaktion  # noqa: E402


# --------------------------------------------------------------------------
# Die Logik
# --------------------------------------------------------------------------

@pytest.mark.parametrize("extras", [None, {}, {"hilfe": "text"}, "kein dict", 42, []])
def test_ohne_angabe_oeffentlich(extras):
    """Der Standard muss oeffentlich sein. Eine faelschlich vertrauliche
    Antwort faellt niemandem auf; eine faelschlich oeffentliche sofort."""
    assert interaktion.antwortart(extras) == interaktion.OEFFENTLICH
    assert not interaktion.ist_vertraulich(extras)


def test_tippfehler_macht_den_befehl_nicht_unbenutzbar():
    """Bewusst nachsichtig statt mit Ausnahme: Ein Vertipper im extras-Feld
    duerfte sonst einen ganzen Befehl lahmlegen."""
    assert interaktion.antwortart({"antwort": "vertraulisch"}) == interaktion.OEFFENTLICH
    assert interaktion.antwortart({"antwort": None}) == interaktion.OEFFENTLICH


def test_die_drei_arten_werden_erkannt():
    assert interaktion.ist_vertraulich({"antwort": "vertraulich"})
    assert not interaktion.ist_vertraulich({"antwort": "oeffentlich"})
    assert interaktion.soll_bestaetigen({"antwort": "vertraulich"})
    assert interaktion.soll_bestaetigen({"antwort": "oeffentlich"})
    assert not interaktion.soll_bestaetigen({"antwort": "manuell"})


def test_manuell_ist_nicht_vertraulich():
    """"manuell" heisst nur "der Befehl bestaetigt selbst" - es darf nicht
    nebenbei die Sichtbarkeit umstellen."""
    assert not interaktion.ist_vertraulich({"antwort": "manuell"})


@pytest.mark.parametrize("code, erwartet", [
    (10062, True),      # Unknown interaction - die Frist ist verstrichen
    (10008, False),     # Unknown message
    (50013, False),     # Missing permissions
    (50001, False),     # Missing access
    (None, False),      # gar kein Discord-Fehler
])
def test_nur_10062_gilt_als_abgelaufen(code, erwartet):
    """Wichtig, dass das eng bleibt: Wer hier zu viel einsammelt, verschluckt
    echte Programmfehler samt Traceback und macht den naechsten Fehler dieser
    Art unauffindbar."""
    assert interaktion.abgelaufen(code) is erwartet


# --------------------------------------------------------------------------
# Angabe und Wirklichkeit duerfen nicht auseinanderlaufen
# --------------------------------------------------------------------------

def _befehle():
    """Liest alle Hybrid-Befehle samt Antwortart und ctx.send-Aufrufen.

    Ueber den Syntaxbaum statt per Import, weil die CI bewusst nur pytest
    installiert - discord.py steht dort nicht zur Verfuegung.
    """
    gefunden = []
    for pfad in sorted((REPO / "bot" / "cogs").glob("*.py")):
        baum = ast.parse(pfad.read_text())
        for knoten in ast.walk(baum):
            if not isinstance(knoten, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            extras, ist_befehl = {}, False
            for dek in knoten.decorator_list:
                if not isinstance(dek, ast.Call):
                    continue
                if "hybrid_command" not in ast.dump(dek.func):
                    continue
                ist_befehl = True
                for kw in dek.keywords:
                    if kw.arg == "extras" and isinstance(kw.value, ast.Dict):
                        for s, w in zip(kw.value.keys, kw.value.values):
                            if isinstance(s, ast.Constant) and isinstance(w, ast.Constant):
                                extras[s.value] = w.value
            if not ist_befehl:
                continue

            # Nur ctx.send zaehlt. user.send() in /dm ist eine Direktnachricht
            # an eine andere Person und hat mit der Antwort nichts zu tun.
            vertraulich = oeffentlich = 0
            for k in ast.walk(knoten):
                if not (isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute)):
                    continue
                if k.func.attr != "send" or not isinstance(k.func.value, ast.Name):
                    continue
                if k.func.value.id != "ctx":
                    continue
                if any(kw.arg == "ephemeral" and getattr(kw.value, "value", None) is True
                       for kw in k.keywords):
                    vertraulich += 1
                else:
                    oeffentlich += 1
            gefunden.append((pfad.name, knoten.name, extras, vertraulich, oeffentlich))
    return gefunden


BEFEHLE = _befehle()


def test_es_gibt_ueberhaupt_befehle_zu_pruefen():
    """Sonst waeren alle folgenden Tests still gruen, ohne etwas zu pruefen."""
    assert len(BEFEHLE) >= 15


@pytest.mark.parametrize("datei, name, extras", [(d, n, e) for d, n, e, _, _ in BEFEHLE])
def test_antwortart_ist_ein_gueltiges_wort(datei, name, extras):
    """Ein Vertipper wuerde stillschweigend auf "oeffentlich" zurueckfallen -
    bei einem Befehl, der vertraulich antworten soll, waere das ein Leck."""
    if "antwort" in extras:
        assert extras["antwort"] in (interaktion.VERTRAULICH, interaktion.OEFFENTLICH,
                                     interaktion.MANUELL), (
            f"/{name} in {datei} gibt antwort={extras['antwort']!r} an - das kennt niemand."
        )


@pytest.mark.parametrize("datei, name, extras, vertraulich, oeffentlich",
                         [b for b in BEFEHLE if b[3] or b[4]])
def test_angabe_passt_zu_dem_was_der_befehl_sendet(datei, name, extras, vertraulich, oeffentlich):
    """Der eigentliche Punkt.

    Wer "vertraulich" angibt und dann oeffentlich sendet, bekommt eine
    Antwort, die keiner erwartet hat - und umgekehrt bleibt eine als
    oeffentlich bestaetigte Antwort mit einem leeren "denkt nach" im Kanal
    stehen. Beides faellt erst im Betrieb auf.
    """
    art = interaktion.antwortart(extras)
    if art == interaktion.MANUELL:
        return  # bestaetigt selbst, dort steht die Entscheidung im Code
    if art == interaktion.VERTRAULICH:
        assert oeffentlich == 0, (
            f"/{name} in {datei} ist als vertraulich angegeben, sendet aber "
            f"{oeffentlich}x oeffentlich."
        )
    else:
        assert vertraulich == 0, (
            f"/{name} in {datei} antwortet ausschliesslich vertraulich, ist aber "
            f'nicht so angegeben. Bitte extras={{"antwort": "vertraulich"}} setzen.'
        )


def test_gemischte_befehle_muessen_manuell_sein():
    """Ein Befehl, der mal so und mal so antwortet, kann nicht vorab
    bestaetigt werden - die Entscheidung faellt erst im Ablauf."""
    for datei, name, extras, vertraulich, oeffentlich in BEFEHLE:
        if vertraulich and oeffentlich:
            assert interaktion.antwortart(extras) == interaktion.MANUELL, (
                f"/{name} in {datei} antwortet mal oeffentlich, mal vertraulich - "
                f'das braucht extras={{"antwort": "manuell"}} und ein eigenes defer().'
            )


def test_manuelle_befehle_bestaetigen_auch_wirklich_selbst():
    """Sonst waere "manuell" nur ein Weg, die Pruefung oben zu umgehen, und
    der Befehl stuende voellig ohne Schutz da - genau der Zustand vom 04.09."""
    for pfad in sorted((REPO / "bot" / "cogs").glob("*.py")):
        quelle = pfad.read_text()
        for datei, name, extras, _, _ in BEFEHLE:
            if datei != pfad.name or interaktion.antwortart(extras) != interaktion.MANUELL:
                continue
            baum = ast.parse(quelle)
            for k in ast.walk(baum):
                if isinstance(k, (ast.AsyncFunctionDef, ast.FunctionDef)) and k.name == name:
                    assert "defer" in ast.dump(k), (
                        f"/{name} ist als manuell angegeben, ruft aber nie defer() auf."
                    )


def test_main_haengt_die_bestaetigung_ueberhaupt_ein():
    """Das Modul allein nuetzt nichts, wenn der Haken fehlt."""
    quelle = (REPO / "bot" / "main.py").read_text()
    assert "@bot.before_invoke" in quelle
    assert "interaktion.soll_bestaetigen" in quelle
    assert "interaktion.ist_vertraulich" in quelle


def test_abgelaufene_interaktion_erzeugt_keinen_traceback_mehr():
    """Fehler 10062 ist kein Programmfehler, sondern eine verstrichene Frist.
    Ein Traceback dazu hat den Blick auf die Ursache eher verstellt."""
    quelle = (REPO / "bot" / "main.py").read_text()
    assert "interaktion.abgelaufen" in quelle
