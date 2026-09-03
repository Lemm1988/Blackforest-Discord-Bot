"""Vergleicht Backend-Rechte mit den tatsächlichen Discord-Rollen.

Reine Logik ohne discord.py und ohne Datenbank — gleiche Trennung wie bei
scheduling.py, events.py und fehler.py, damit sie ohne laufenden Bot testbar
ist.

Hintergrund: Bis zum 03.09.2026 waren beides getrennte Welten. Wer im Backend
`moderate_members` bekam, hatte deshalb noch lange keine Moderator-Rolle auf dem
Server — und umgekehrt. Dass etwas auseinanderläuft, fiel erst auf, wenn jemand
sich wunderte, warum er etwas nicht darf.

Dieses Modul beantwortet die Frage „passt das zusammen?", ohne selbst etwas zu
ändern. Das Vergeben ist ein eigener Schritt und bewusst davon getrennt: Erst
sehen, dann handeln.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Abgleich:
    """Das Ergebnis für eine Person."""

    fehlend: list[str] = field(default_factory=list)
    """Rollen, die ihr laut Zuordnung zustehen, die sie aber nicht hat."""

    ueberzaehlig: list[str] = field(default_factory=list)
    """Rollen aus der Zuordnung, die sie hat, ohne das passende Recht zu haben.

    BEWUSST nicht "falsch" genannt: Eine solche Rolle kann völlig in Ordnung
    sein - jemand kann sie von Hand bekommen haben, aus Gründen, die mit dem
    Backend nichts zu tun haben. Die Übersicht weist darauf hin, urteilt aber
    nicht.
    """

    unverwaltbar: list[str] = field(default_factory=list)
    """Fehlende Rollen, die der Bot gar nicht vergeben könnte, weil sie über
    seiner eigenen Position stehen. Getrennt ausgewiesen, weil hier kein
    Knopfdruck hilft, sondern nur Handarbeit in Discord."""

    @property
    def passt(self) -> bool:
        return not (self.fehlend or self.ueberzaehlig or self.unverwaltbar)


def abgleichen(
    *,
    rechte: set[str],
    rollen: set[str],
    zuordnung: dict[str, str],
    verwaltbare_rollen: set[str],
) -> Abgleich:
    """Vergleicht die Rechte einer Person mit ihren Rollen.

    rechte            - Backend-Flags dieser Person ("is_admin", ...)
    rollen            - IDs der Discord-Rollen, die sie trägt
    zuordnung         - {Recht: Rollen-ID}, im Backend gepflegt
    verwaltbare_rollen- IDs, die unter der Bot-Rolle liegen

    Eine Rolle kann mehreren Rechten zugeordnet sein. Sie gilt als zustehend,
    sobald die Person EINES dieser Rechte hat - sonst würde ein zweites
    zugeordnetes Recht die Rolle unerwartet wieder streitig machen.
    """
    zustehend = {rolle for recht, rolle in zuordnung.items() if recht in rechte}
    zugeordnet = set(zuordnung.values())

    ergebnis = Abgleich()
    for rolle in sorted(zustehend - rollen):
        if rolle in verwaltbare_rollen:
            ergebnis.fehlend.append(rolle)
        else:
            ergebnis.unverwaltbar.append(rolle)

    # Nur Rollen betrachten, die ueberhaupt in der Zuordnung vorkommen. Alle
    # anderen (Member, Server Booster, was auch immer) gehen das Backend nichts
    # an und duerfen nicht als "ueberzaehlig" erscheinen.
    ergebnis.ueberzaehlig = sorted((rollen & zugeordnet) - zustehend)
    return ergebnis


def zusammenfassung(abgleich: Abgleich, rollennamen: dict[str, str]) -> str:
    """Ein kurzer Satz für die Übersichtstabelle."""
    if abgleich.passt:
        return "passt"

    teile = []
    if abgleich.fehlend:
        teile.append("fehlt: " + ", ".join(rollennamen.get(r, r) for r in abgleich.fehlend))
    if abgleich.unverwaltbar:
        teile.append(
            "nur von Hand: " + ", ".join(rollennamen.get(r, r) for r in abgleich.unverwaltbar)
        )
    if abgleich.ueberzaehlig:
        teile.append(
            "ohne passendes Recht: "
            + ", ".join(rollennamen.get(r, r) for r in abgleich.ueberzaehlig)
        )
    return " · ".join(teile)
