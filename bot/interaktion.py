"""Sorgt dafür, dass ein Befehl Discords Drei-Sekunden-Fenster nicht reißt.

Am 04.09.2026 meldete ein Mitglied, `/frage` antworte mit „Die Anwendung
reagiert nicht". Im Log stand::

    NotFound: 404 Not Found (error code: 10062): Unknown interaction

Discord gibt einem Bot **drei Sekunden**, um einen Befehl überhaupt zu
bestätigen. Wer das verpasst, dessen Sitzungsschlüssel ist verfallen — danach
lässt sich nichts mehr senden, auch keine Fehlermeldung. Genau deshalb sah der
Nutzer nur Discords eigenen Text und nicht unseren.

Die Abhilfe, die Discord dafür vorsieht, heißt „deferred response": eine leere
Vorab-Bestätigung. Sie kostet nichts, verlängert das Fenster aber von drei
Sekunden auf fünfzehn Minuten. Bis zum 04.09.2026 kam sie im ganzen Projekt
kein einziges Mal vor — jeder der fünfzehn Befehle war also angreifbar, sobald
der Pi kurz ausgelastet oder das Netz langsam war.

Warum das hier zentral steht statt in jedem Befehl:

Die Vorab-Bestätigung muss wissen, ob die spätere Antwort öffentlich oder nur
für den Aufrufer sichtbar wird — das lässt sich nachträglich nicht mehr ändern.
Diese Eigenschaft steht deshalb beim Befehl selbst, im ohnehin vorhandenen
``extras``-Feld, und wird hier ausgewertet. So gibt es eine Stelle, an der die
Regel steht, und der einzelne Befehl trägt nur noch ein Wort.

Das Modul kommt bewusst ohne discord.py aus, damit es sich ohne laufenden Bot
testen lässt — dieselbe Trennung wie bei fehler.py und rollenabgleich.py.
"""
from __future__ import annotations

#: Antwort nur für den Aufrufer sichtbar.
VERTRAULICH = "vertraulich"

#: Antwort für alle im Kanal sichtbar. Der Normalfall.
OEFFENTLICH = "oeffentlich"

#: Der Befehl bestätigt selbst. Für Befehle, die erst nach einer schnellen
#: Prüfung wissen, ob ihre Antwort öffentlich wird (siehe /frage_einreichen).
MANUELL = "manuell"

#: Ohne Angabe: öffentlich. Das ist die Antwortart der klaren Mehrheit, und
#: eine falsch geratene Vertraulichkeit fiele niemandem auf — eine Antwort, die
#: unerwartet öffentlich steht, dagegen sofort.
STANDARD = OEFFENTLICH

_ERLAUBT = (VERTRAULICH, OEFFENTLICH, MANUELL)


def antwortart(extras) -> str:
    """Liest die Antwortart aus dem ``extras``-Feld eines Befehls.

    Unbekannte oder fehlende Angaben ergeben den Standard. Bewusst nachsichtig
    statt mit einer Ausnahme: Ein Tippfehler im ``extras`` eines Befehls darf
    diesen Befehl nicht komplett unbenutzbar machen.
    """
    if not isinstance(extras, dict):
        return STANDARD
    art = extras.get("antwort")
    return art if art in _ERLAUBT else STANDARD


def soll_bestaetigen(extras) -> bool:
    """Ob der Bot diesen Befehl vorab bestätigen soll."""
    return antwortart(extras) != MANUELL


def ist_vertraulich(extras) -> bool:
    """Ob die Vorab-Bestätigung als „nur für dich" gelten soll."""
    return antwortart(extras) == VERTRAULICH


def abgelaufen(fehlercode) -> bool:
    """Ob ein Discord-Fehler bedeutet, dass die Interaktion verfallen ist.

    10062 („Unknown interaction") heißt: Der Schlüssel ist weg. Weitere
    Sendeversuche sind zwecklos, und ein Traceback im Log wäre irreführend —
    es ist kein Programmfehler, sondern eine verstrichene Frist.
    """
    return fehlercode == 10062
