"""Registratur der installierbaren Module.

Ziel (Patrick, 03.09.2026): Ein Modul soll sich nachträglich installieren
lassen, und die Datenbank soll sich dabei selbst aufbauen. Ein Modul bringt
deshalb alles mit, was nur es betrifft:

* **sein Schema** — die Tabellen, die es braucht. Beim Start werden sie angelegt,
  falls sie fehlen. Ein Modul, das später dazukommt, findet seine Tabellen also
  beim nächsten Start vor, ohne dass jemand etwas von Hand anlegen muss.
* **seine Achievements** — welche es gibt und wann sie fallen.
* **seine Bedingungen** für verdiente Rollen, jeweils samt eigener Abfrage.
* **seinen Ereignis-Kontext** — die Zahlen, die seine Achievements brauchen.
  Vorher stand das als if/elif in database.py, mit Quiz-Wissen darin
  (`increment_user_stat(uid, "quiz_correct_count")`). Ohne das Quizmodul hätte
  diese Zeile auf eine Tabelle gezeigt, die es nicht gibt.

Was NICHT hierher gehört: Die reine Mechanik. Achievement-Freischaltung,
Rollenvergabe und die Tabellen, die alle Module gemeinsam nutzen (Einstellungen,
Audit-Log, Berechtigungen), bleiben im Kern.

Ehrliche Einschränkung, Stand 03.09.2026: Die Abfragefunktionen der Module —
etwa `get_leaderboard()` oder `award_xp()` — stehen weiterhin in database.py. Sie
mitzuverschieben wäre ein Umbau von rund 30 Funktionen und ein eigener Schritt.
Für das Ziel „Datenbank baut sich selbst auf" reicht das Schema; ein entferntes
Modul liesse aber vorerst seine Abfragefunktionen zurück.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Modul:
    name: str
    label: str
    schema: tuple[str, ...] = ()
    """CREATE-TABLE-Anweisungen. Immer mit IF NOT EXISTS, damit ein zweiter
    Start nichts kaputt macht."""

    migrationen: tuple[Callable, ...] = ()
    """Schrittweise Anpassungen an bestehenden Tabellen des Moduls — etwa eine
    nachträglich hinzugekommene Spalte. Laufen direkt nach dem Schema und nur,
    wenn das Modul installiert ist. Stünden sie zentral, würden sie ohne das
    Modul auf Tabellen zugreifen, die es nicht gibt."""

    ereignis_kontext: dict[str, Callable] = field(default_factory=dict)
    """{Ereignisname: Funktion(user_id) -> dict}. Liefert die Zahlen, die die
    Achievements dieses Moduls für dieses Ereignis brauchen."""


class Modulregistratur:
    def __init__(self) -> None:
        self._module: dict[str, Modul] = {}

    def eintragen(self, modul: Modul) -> None:
        if modul.name in self._module:
            raise ValueError(f"Modul {modul.name!r} ist doppelt eingetragen.")
        self._module[modul.name] = modul

    def alle(self) -> list[Modul]:
        return [self._module[n] for n in sorted(self._module)]

    def namen(self) -> list[str]:
        return sorted(self._module)

    def schema_anwenden(self, conn) -> list[str]:
        """Legt die Tabellen aller installierten Module an. Gibt die Namen der
        Module zurück, deren Schema ausgeführt wurde."""
        angewendet = []
        for modul in self.alle():
            for anweisung in modul.schema:
                conn.execute(anweisung)
            for schritt in modul.migrationen:
                schritt(conn)
            if modul.schema or modul.migrationen:
                angewendet.append(modul.name)
        return angewendet

    def kontext(self, ereignis: str, discord_user_id) -> dict:
        """Sammelt den Kontext aller Module für ein Ereignis.

        Ein Fehler in einem Modul darf die anderen nicht mitreissen - fehlt eine
        Zahl, gilt die betreffende Bedingung schlicht als nicht erfüllt.
        """
        gesammelt: dict = {}
        for modul in self.alle():
            beschaffer = modul.ereignis_kontext.get(ereignis)
            if beschaffer is None:
                continue
            try:
                gesammelt.update(beschaffer(discord_user_id))
            except Exception as exc:
                print(f"[module] Kontext für {ereignis!r} aus {modul.name!r} "
                      f"nicht ermittelbar: {exc}")
        return gesammelt


REGISTRATUR = Modulregistratur()

# Welche Module installiert sind. Sobald sie einzeln verkauft werden, kommt die
# Liste aus der Konfiguration - bis dahin steht hier an EINER Stelle, was aktiv
# ist. Der Kern ist immer dabei.
INSTALLIERT = ("kern", "quiz", "xp_level")


def laden(*namen: str) -> None:
    """Lädt die Moduldateien. Ihr Import trägt sie in die Registraturen ein."""
    import importlib

    for name in namen or INSTALLIERT:
        importlib.import_module(f"modul_{name}")
