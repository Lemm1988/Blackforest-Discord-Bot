"""Reine Zeitplan-Entscheidungen ohne I/O, damit sie isoliert testbar sind.

Gleiche Trennung wie bei events.py: Die Frage "soll jetzt etwas passieren?"
haengt an Uhrzeiten, Fenstern und einem Merker - lauter Dingen, bei denen ein
Denkfehler erst am naechsten Morgen auffaellt. Deshalb steht sie hier als reine
Funktion und nicht mitten im Cog zwischen Discord-Aufrufen.
"""
from __future__ import annotations

from datetime import datetime, timedelta


def parse_uhrzeit(text: str) -> tuple[int, int] | None:
    """Liest "HH:MM". Gibt None zurueck, wenn der Wert unbrauchbar ist.

    Bewusst None statt einer Ausnahme: Der Wert kommt aus einem Eingabefeld im
    Backend. Ein Vertipper darf den Bot nicht in einen Fehlerzustand bringen, er
    soll den Gruss nur ausfallen lassen - sichtbar im Log.
    """
    teile = (text or "").strip().split(":")
    if len(teile) != 2:
        return None
    try:
        stunde, minute = int(teile[0]), int(teile[1])
    except ValueError:
        return None
    if not (0 <= stunde <= 23 and 0 <= minute <= 59):
        return None
    return stunde, minute


def taeglich_faellig(
    *,
    jetzt: datetime,
    uhrzeit: str,
    zuletzt_gegruesst: str | None,
    nachlauf_minuten: int,
) -> tuple[bool, str]:
    """Entscheidet, ob eine taegliche Aufgabe jetzt dran ist.

    Urspruenglich nur fuer den Morgengruss gebaut, seit dem 07.09.2026 auch
    fuer die Rekordpruefung - die Frage ist beide Male dieselbe: "war heute
    schon?".

    Rueckgabe: (senden, tagesmerker_setzen_auf) - der zweite Wert ist das
    heutige Datum, wenn der Tag als erledigt vermerkt werden soll, sonst "".

    Der Merker ist ein DATUM in lokaler Zeit ("2026-09-03"), kein Zeitstempel.
    Das weicht bewusst von den uebrigen last_*-Werten im bot_state ab, die UTC-
    Zeitstempel sind: Hier lautet die Frage nicht "wie lange ist es her", sondern
    "war heute schon" - und "heute" ist eine lokale Kalenderfrage. Mit einem
    UTC-Zeitstempel waere der Gruss in der Sommerzeit um Mitternacht herum
    fehleranfaellig.

    Drei Faelle:

    * Noch zu frueh -> nichts tun, nichts vermerken.
    * Im Fenster -> senden und den Tag vermerken.
    * Fenster verpasst (Pi war aus) -> NICHT senden, aber den Tag trotzdem
      vermerken. Sonst wuerde eine spaetere Aenderung der Uhrzeit denselben Tag
      nachtraeglich noch ausloesen, und ein "Guten Morgen" um 15 Uhr ist
      schlimmer als gar keins.
    """
    heute = jetzt.strftime("%Y-%m-%d")
    if zuletzt_gegruesst == heute:
        return False, ""

    geparst = parse_uhrzeit(uhrzeit)
    if geparst is None:
        return False, ""
    stunde, minute = geparst

    geplant = jetzt.replace(hour=stunde, minute=minute, second=0, microsecond=0)
    if jetzt < geplant:
        return False, ""

    if jetzt > geplant + timedelta(minutes=max(0, nachlauf_minuten)):
        return False, heute

    return True, heute


#: Frueherer Name, als es die Funktion nur fuer den Morgengruss gab.
morgengruss_faellig = taeglich_faellig
