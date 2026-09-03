"""Verständliche Meldungen für fehlgeschlagene Befehle.

Bis zum 03.09.2026 gab es keine Fehlerbehandlung für Befehle. Wer sich vertippte
oder ein Argument vergass, bekam **gar keine Antwort** - discord.py schrieb die
Meldung in die Fehlerausgabe des Prozesses, und die war ihrerseits unsichtbar,
weil dem Dienst PYTHONUNBUFFERED fehlte. Der Bot wirkte damit kaputt oder so,
als wuerde er den Nutzer ignorieren.

Das ist dieselbe Fehlerklasse, die in diesem Projekt schon einmal Tage gekostet
hat: Kick, Ban und Timeout taten nichts, weil die WebView der App die
Bestaetigungsdialoge verschluckte. Fuer den Menschen davor sieht beides gleich
aus - es passiert nichts, und nichts sagt warum.

Hier stehen nur die TEXTE, als reine Funktion ohne discord.py. Die Zuordnung
von Ausnahme zu Textart passiert in main.py. Zwei Gruende dafuer: Die Texte
lassen sich so ohne Discord und ohne laufenden Bot testen (die CI installiert
bewusst nur pytest), und die Formulierungen stehen an einer Stelle statt
verstreut zwischen den Ausnahmezweigen.
"""
from __future__ import annotations

# Wird angezeigt, wenn etwas Unerwartetes schiefging. Bewusst ohne technische
# Einzelheiten: Ein Python-Traceback im oeffentlichen Kanal ist unschoen und
# verraet Interna. Die vollstaendige Ausnahme gehoert ins Log, nicht in den Chat.
GENERISCH = (
    "Da ist etwas schiefgelaufen. Das wurde protokolliert - bitte versuch es "
    "gleich noch einmal."
)


def meldung(art: str, **kontext) -> str | None:
    """Liefert den Text zu einer Fehlerart, oder None, wenn geschwiegen werden soll.

    Bewusst ueber einen Zeichenketten-Schluessel statt ueber Ausnahmeklassen:
    Damit bleibt dieses Modul frei von discord.py und ist einzeln testbar.

    Bekannte Arten:

    unbekannter_befehl   -> None (siehe unten)
    fehlende_berechtigung(grund)
    fehlendes_argument(parameter, verwendung)
    falsches_argument(verwendung)
    cooldown(sekunden)
    nur_auf_server
    intern
    """
    if art == "unbekannter_befehl":
        # BEWUSST still. Sonst antwortet der Bot auf jede Nachricht, die
        # zufaellig mit dem Praefix beginnt - ein zitierter Text, ein Pfeil, ein
        # Smiley. Das waere schlimmer als das Schweigen.
        return None

    if art == "fehlende_berechtigung":
        return kontext.get("grund") or "⛔ Dafür fehlt dir die Berechtigung."

    if art == "fehlendes_argument":
        parameter = kontext.get("parameter", "etwas")
        verwendung = kontext.get("verwendung")
        text = f"Da fehlt noch etwas: **{parameter}**."
        if verwendung:
            text += f"\nSo geht's: `{verwendung}`"
        return text

    if art == "falsches_argument":
        verwendung = kontext.get("verwendung")
        text = "Damit kann ich nichts anfangen - bitte prüf deine Eingabe."
        if verwendung:
            text += f"\nSo geht's: `{verwendung}`"
        return text

    if art == "cooldown":
        sekunden = kontext.get("sekunden")
        if sekunden is None:
            return "Einen Moment noch, das ging zu schnell hintereinander."
        return f"Einen Moment noch - versuch es in **{max(1, int(sekunden))}s** noch einmal."

    if art == "nur_auf_server":
        return "Das funktioniert nur auf dem Server, nicht in einer Direktnachricht."

    return GENERISCH


def verwendung(befehl: str, signatur: str = "") -> str:
    """Baut den Verwendungshinweis, z.B. "/grant_access <user> <permission>".

    Immer mit Schrägstrich: Die Prefix-Variante ist ein Auslaufmodell, und ein
    Hinweis, der auf ">" zeigt, waere schon jetzt der falsche Weg.
    """
    signatur = (signatur or "").strip()
    return f"/{befehl} {signatur}".strip()
