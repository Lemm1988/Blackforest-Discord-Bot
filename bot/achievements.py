"""Registratur der Achievements — Mechanik hier, Inhalte in den Modulen.

Warum das getrennt ist (Entscheidung Patrick, 03.09.2026): Die Funktionsbereiche
des Bots sollen sich einzeln installieren lassen, weil einzelne Module spaeter
kostenpflichtig angeboten werden sollen. Ein Modul muss deshalb seine eigenen
Achievements mitbringen, statt dass eine zentrale Liste alle kennt.

Vorher standen Definitionen UND Ausloesebedingungen zusammen in database.py, in
einer if/elif-Kette. Wer das Quiz-Modul entfernt haette, haette dort sechs tote
Achievements zurueckgelassen und eine Bedingung, die auf eine Quiz-Statistik
zugreift, die es nicht mehr gibt.

Aufteilung jetzt:

* Dieses Modul kennt die Mechanik und keinen einzigen Achievement-Namen.
* achievements_quiz.py und achievements_xp_level.py bringen die Inhalte mit.
* database.py verdrahtet beides mit der Datenbank und vergibt die XP-Belohnung.

Die Bedingungen sind REINE Funktionen ueber ein Kontext-Dictionary — kein
Datenbankzugriff, kein Discord. Wer eine Bedingung prueft, muss die noetigen
Zahlen vorher besorgen und mitgeben. Das haelt dieses Modul testbar und
verhindert, dass eine Bedingung heimlich etwas in der Datenbank veraendert
(genau das passierte vorher: Die Quiz-Bedingung zaehlte als Nebenwirkung den
Zaehler hoch).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Achievement:
    """Eine Auszeichnung samt der Bedingung, unter der sie faellt.

    modul  - zu welchem Funktionsbereich sie gehoert. Wird gebraucht, sobald
             Module einzeln installierbar sind, und macht schon jetzt beim Lesen
             klar, wem sie gehoert.
    ereignis - auf welches Ereignis sie ueberhaupt reagiert ("quiz_correct",
             "level_up", ...). Spart es, jede Bedingung bei jedem Ereignis
             aufzurufen.
    bedingung - reine Funktion (kontext) -> bool.
    """

    id: str
    name: str
    beschreibung: str
    xp_belohnung: int
    geheim: bool
    modul: str
    ereignis: str
    bedingung: Callable[[dict], bool]


class Registratur:
    def __init__(self) -> None:
        self._eintraege: dict[str, Achievement] = {}

    def eintragen(self, achievement: Achievement) -> None:
        if achievement.id in self._eintraege:
            raise ValueError(
                f"Achievement-ID {achievement.id!r} ist doppelt vergeben "
                f"(Modul {self._eintraege[achievement.id].modul} und {achievement.modul}). "
                "IDs muessen ueber alle Module hinweg eindeutig sein, sie stehen so "
                "in der Datenbank."
            )
        self._eintraege[achievement.id] = achievement

    def alle(self) -> list[Achievement]:
        return sorted(self._eintraege.values(), key=lambda a: a.id)

    def modul(self, name: str) -> list[Achievement]:
        return [a for a in self.alle() if a.modul == name]

    def module(self) -> list[str]:
        return sorted({a.modul for a in self._eintraege.values()})

    def kandidaten(self, ereignis: str, kontext: dict) -> list[str]:
        """IDs, deren Bedingung bei diesem Ereignis zutrifft.

        Ob sie schon freigeschaltet sind, spielt hier keine Rolle - das
        entscheidet die Datenbank ueber ihren UNIQUE-Constraint. Eine Bedingung
        darf also ruhig mehrfach zutreffen.
        """
        return [
            a.id for a in self.alle()
            if a.ereignis == ereignis and a.bedingung(kontext)
        ]


REGISTRATUR = Registratur()


def lade_module(*module: str) -> None:
    """Laedt die Achievement-Inhalte der genannten Module.

    Der Aufruf steht in database.init_db(), also VOR dem Laden der Cogs — die
    Definitionen muessen in der Datenbank stehen, bevor irgendetwas sie
    freischalten kann.

    Sobald Module tatsaechlich einzeln verkauft werden, kommt die Liste aus der
    Konfiguration statt aus dem Aufruf. Bis dahin macht dieser Parameter
    wenigstens an einer Stelle sichtbar, welche Module aktiv sind.
    """
    import importlib

    for name in module:
        importlib.import_module(f"achievements_{name}")
