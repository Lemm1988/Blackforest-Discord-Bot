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
* modul_quiz.py und modul_xp_level.py bringen die Inhalte mit - zusammen mit
  ihrem Schema und ihren Bedingungen, damit ein Modul aus EINER Datei besteht.
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


@dataclass(frozen=True)
class Bedingungstyp:
    """Eine Art von Bedingung, die ein Achievement auslösen kann.

    Bis zum 05.09.2026 war jede Bedingung eine eigene Lambda-Funktion im
    Modulcode: `_mindestens_richtig(10)`. Die Schwelle steckte damit IM
    Programm — für „ab wie vielen Fragen gibt es das?" brauchte es einen
    Codeeintrag und einen Neustart.

    Patrick am 05.09.2026: „archievments sollen einstellbar werden. wann man
    welches archievment bekommt." Deshalb ist die Bedingung jetzt in zwei Teile
    zerlegt: Die ART steht hier im Code (sie braucht Wissen darüber, was ein
    Ereignis überhaupt mitliefert), der WERT steht in der Datenbank und ist im
    Backend änderbar.

    Dasselbe Muster wie bei den verdienten Rollen (`Bedingungsart` in
    verdiente_rollen.py). Zwei verschiedene Denkweisen für dasselbe Problem
    wären der Anfang vom Auseinanderlaufen.

    kontextfeld - welche Zahl aus dem Ereignis-Kontext betrachtet wird
    spanne      - True: zwei Werte (von/bis), z.B. eine Uhrzeit. False: eine
                  Mindestschwelle.
    """

    schluessel: str
    label: str
    modul: str
    ereignis: str
    kontextfeld: str
    spanne: bool = False
    einheit: str = ""
    hinweis: str = ""

    def trifft_zu(self, kontext: dict, wert, wert_bis=None) -> bool:
        """Ob die Bedingung mit diesen Werten zutrifft.

        Fehlt die Zahl im Kontext, trifft NICHTS zu - lieber ein Achievement zu
        spät als eines, das jemand ohne Zutun bekommt. Das war schon bei den
        Uhrzeit-Achievements die bewusste Entscheidung und gilt jetzt für alle.
        """
        vorhanden = kontext.get(self.kontextfeld)
        if vorhanden is None:
            return False
        try:
            if self.spanne:
                return int(wert) <= vorhanden < int(wert_bis)
            return vorhanden >= int(wert)
        except (TypeError, ValueError):
            # Ein unbrauchbarer Wert in der Datenbank darf die gesamte
            # Achievement-Pruefung nicht zum Absturz bringen.
            return False


class Bedingungsregistratur:
    """Welche Bedingungsarten es gibt. Gefüllt von den Modulen."""

    def __init__(self) -> None:
        self._arten: dict[str, Bedingungstyp] = {}

    def eintragen(self, art: Bedingungstyp) -> None:
        if art.schluessel in self._arten:
            raise ValueError(f"Bedingungsart {art.schluessel!r} ist doppelt vergeben.")
        self._arten[art.schluessel] = art

    def holen(self, schluessel: str):
        return self._arten.get(schluessel)

    def alle(self) -> list[Bedingungstyp]:
        return sorted(self._arten.values(), key=lambda a: (a.modul, a.schluessel))

    def fuer_modul(self, modul: str) -> list[Bedingungstyp]:
        return [a for a in self.alle() if a.modul == modul]


BEDINGUNGEN = Bedingungsregistratur()


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
    bedingung_typ: str
    wert: int = 1
    wert_bis: int | None = None


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
            if a.ereignis == ereignis and trifft_zu(a.bedingung_typ, kontext, a.wert, a.wert_bis)
        ]


REGISTRATUR = Registratur()


def trifft_zu(bedingung_typ: str, kontext: dict, wert, wert_bis=None) -> bool:
    """Wertet eine Bedingung aus, gleich ob sie aus dem Code oder aus der
    Datenbank stammt.

    Eine unbekannte Art trifft NIE zu. Das ist wichtig für die Modultrennung:
    Wer das Quiz-Modul nicht installiert hat, dessen Datenbank kann trotzdem
    noch Quiz-Achievements enthalten - die dürfen dann schlicht nie fallen,
    statt einen Fehler zu werfen.
    """
    art = BEDINGUNGEN.holen(bedingung_typ)
    return art.trifft_zu(kontext, wert, wert_bis) if art else False


def kandidaten_aus_regeln(ereignis: str, kontext: dict, regeln) -> list[str]:
    """Wie Registratur.kandidaten(), aber über Regeln aus der Datenbank.

    Getrennt gehalten, damit die reine Auswertung ohne Datenbank prüfbar
    bleibt: `regeln` ist einfach eine Liste von Zuordnungen mit den Schlüsseln
    id, ereignis, bedingung_typ, wert, wert_bis und aktiv.
    """
    return [
        r["id"] for r in regeln
        if r.get("aktiv", 1)
        and r.get("ereignis") == ereignis
        and trifft_zu(r.get("bedingung_typ"), kontext, r.get("wert"), r.get("wert_bis"))
    ]
