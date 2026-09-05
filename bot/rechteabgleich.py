"""Leitet aus den Discord-Rollen einer Person ihre Backend-Rechte ab.

Das Gegenstück zu `rollenabgleich.py`. Beide beantworten dieselbe Frage aus
entgegengesetzter Richtung:

    rollenabgleich.py   Recht im Backend  ->  Rolle auf dem Server
    rechteabgleich.py   Rolle auf dem Server  ->  Recht im Backend

Warum es die zweite Richtung überhaupt braucht: Bis zum 05.09.2026 gab es sie
nicht — `grep -rn "on_member_update" bot/` fand keinen einzigen Treffer. Wer in
Discord von Hand die Moderator-Rolle bekam, hatte im Backend trotzdem keine
Rechte. Auf dem Server standen dadurch zwei Leute mit Moderator-Rolle und ohne
jede Berechtigung, während umgekehrt jemand alle Moderationsrechte hatte, aber
die Rolle nicht trug. Patrick am 05.09.2026: „wenn ein user berechtigungen
erhält in beide richtungen setzen."

Wie in rollenabgleich.py steht hier nur die Logik — kein discord.py, keine
Datenbank. Das Ausführen ist ein eigener Schritt und bewusst getrennt: Erst
rechnen, dann handeln, und beides einzeln prüfbar.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Rechte, die niemals über eine Rolle vergeben oder entzogen werden.
#:
#: `is_admin` ist der Generalschlüssel — has_permission() lässt damit alles
#: durch. Würde er an einer Rolle hängen, könnte ein versehentlich vergebener
#: Rollenklick jemandem die volle Kontrolle über den Bot geben, und ein
#: versehentlich entfernter könnte den letzten Admin aussperren. Diese
#: Entscheidung bleibt Handarbeit in der Benutzerverwaltung.
NUR_VON_HAND = frozenset({"is_admin"})


@dataclass
class Rechtemassnahme:
    """Was sich an den Backend-Rechten einer Person ändern müsste."""

    vergeben: list[str] = field(default_factory=list)
    entziehen: list[str] = field(default_factory=list)

    @property
    def leer(self) -> bool:
        return not (self.vergeben or self.entziehen)


def abgeleitete_rechte(rollen: set[str], rollenrechte: dict[str, set[str]]) -> set[str]:
    """Alle Rechte, die sich aus diesen Rollen zusammen ergeben.

    Mehrere Rollen addieren sich: Wer Manager UND Moderator ist, hat die
    Vereinigung beider. Ein Recht ist damit abgedeckt, sobald EINE Rolle es
    mitbringt — sonst würde eine zweite Rolle ein Recht wieder streitig machen,
    das die erste gerade vergeben hat.
    """
    ergebnis: set[str] = set()
    for rolle in rollen:
        ergebnis |= set(rollenrechte.get(rolle, ()))
    return ergebnis - NUR_VON_HAND


def massnahmen(
    *,
    rollen: set[str],
    rollenrechte: dict[str, set[str]],
    eigene_rechte: set[str],
    vom_bot_gesetzt: set[str],
    geschuetzt: bool = False,
) -> Rechtemassnahme:
    """Was am Backend zu tun wäre, damit es zu den Rollen passt.

    Drei Regeln entscheiden hier über brauchbar oder gefährlich:

    **Der Bot nimmt nur weg, was er selbst gegeben hat.** `vom_bot_gesetzt` ist
    der Merker dafür. Ohne ihn würde die erste Rollenänderung jedem alle von
    Hand vergebenen Rechte entziehen, nur weil gerade keine Rolle sie hergibt.
    Dieselbe Vorsichtsmaßnahme wie in rollenabgleich.massnahmen().

    **`is_admin` bleibt außen vor** — siehe NUR_VON_HAND.

    **Der Besitzer wird nie beschnitten.** `geschuetzt` deckt den Fall ab, dass
    jemand ihm versehentlich eine Rolle wegnimmt; er soll sich dadurch nicht
    selbst aus seinem eigenen Backend aussperren können.
    """
    zustehend = abgeleitete_rechte(rollen, rollenrechte)

    massnahme = Rechtemassnahme()
    massnahme.vergeben = sorted(zustehend - eigene_rechte)
    if not geschuetzt:
        massnahme.entziehen = sorted(
            (eigene_rechte & vom_bot_gesetzt) - zustehend - NUR_VON_HAND
        )
    return massnahme


def begruendung(massnahme: Rechtemassnahme, rollennamen: dict[str, str] | None = None) -> str:
    """Ein Satz fürs Audit-Log und für die Übersicht.

    Wichtig, dass automatische Änderungen nachvollziehbar bleiben: Wer später
    fragt, warum jemand ein Recht verloren hat, soll die Antwort im Log finden
    und nicht raten müssen.
    """
    if massnahme.leer:
        return "keine Änderung nötig"
    teile = []
    if massnahme.vergeben:
        teile.append("erhält: " + ", ".join(massnahme.vergeben))
    if massnahme.entziehen:
        teile.append("verliert: " + ", ".join(massnahme.entziehen))
    return " · ".join(teile)
