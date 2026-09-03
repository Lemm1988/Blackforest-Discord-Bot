"""Verdiente Rollen: Bedingung erfüllt → Rolle wird angeboten.

Reine Logik ohne discord.py und ohne Datenbank, damit sie einzeln testbar ist.
Der Cog besorgt die Zahlen und kümmert sich um Discord, hier steht nur die
Entscheidung.

Anlass (Patrick, 03.09.2026): „wenn der user der ideenschmied werden will 100
fragen beantwortet hat sagt ihm der Bot bescheid und er kann die rolle vom bot
erhalten aber man muss das eben alles im backend einstellen können."

Die Zahlen dafür sammelt der Bot längst — es musste nichts Neues gemessen
werden.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Bedingungsart:
    """Eine auswählbare Bedingung.

    modul  - zu welchem Funktionsbereich sie gehört. Entscheidend, weil die
             Module einzeln installierbar sein sollen: Ohne Pflanzenquiz darf
             „Richtig beantwortete Quizfragen" gar nicht erst zur Auswahl
             stehen - man könnte sonst eine Regel anlegen, die nie greift, und
             niemand fände heraus warum.
    wert   - liefert die Zahl für eine Person. Steht im Modul selbst, nicht in
             database.py: Die Abfrage greift auf Tabellen zu, die es ohne das
             Modul womöglich gar nicht gibt. None bedeutet, dass der Wert nicht
             aus der Datenbank kommt, sondern vom Cog beigesteuert wird (etwa
             die Tage auf dem Server - das weiss nur Discord).
    """

    schluessel: str
    label: str
    einheit: str
    hinweis: str
    modul: str
    wert: Callable[[str], int] | None = None


class Bedingungsregistratur:
    def __init__(self) -> None:
        self._arten: dict[str, Bedingungsart] = {}

    def eintragen(self, art: Bedingungsart) -> None:
        if art.schluessel in self._arten:
            raise ValueError(
                f"Bedingung {art.schluessel!r} ist doppelt vergeben "
                f"(Modul {self._arten[art.schluessel].modul} und {art.modul})."
            )
        self._arten[art.schluessel] = art

    def alle(self) -> dict[str, Bedingungsart]:
        return dict(sorted(self._arten.items()))

    def holen(self, schluessel: str) -> Bedingungsart | None:
        return self._arten.get(schluessel)

    def module(self) -> list[str]:
        return sorted({a.modul for a in self._arten.values()})

    def werte(self, discord_user_id) -> dict:
        """Sammelt die Zahlen aller installierten Bedingungen für eine Person.

        Scheitert eine einzelne Abfrage - etwa weil eine Tabelle eines halb
        entfernten Moduls fehlt -, wird sie übersprungen statt alles
        mitzureissen. Eine fehlende Zahl gilt in erfuellt() als nicht erfüllt.
        """
        gesammelt = {}
        for schluessel, art in self._arten.items():
            if art.wert is None:
                continue
            try:
                gesammelt[schluessel] = art.wert(discord_user_id)
            except Exception as exc:
                print(f"[verdiente rollen] Wert für {schluessel!r} nicht ermittelbar: {exc}")
        return gesammelt


REGISTRATUR = Bedingungsregistratur()


def lade_module(*module: str) -> None:
    """Lädt die Bedingungen der installierten Module.

    Gleiche Bauart wie bei achievements.lade_module(). Sobald Module wirklich
    einzeln verkauft werden, kommt die Liste aus der Konfiguration.
    """
    import importlib

    for name in module:
        importlib.import_module(f"bedingungen_{name}")


def BEDINGUNGSARTEN() -> dict:
    """Für das Backend: alle installierten Bedingungen."""
    return REGISTRATUR.alle()


def ist_gueltige_art(art: str) -> bool:
    return REGISTRATUR.holen(art) is not None


def erfuellt(art: str, schwelle: int, werte: dict) -> bool:
    """Ist die Bedingung erfüllt?

    werte enthält die Zahlen einer Person, wie der Cog sie besorgt hat. Fehlt
    der passende Wert, gilt die Bedingung als NICHT erfüllt - lieber eine Rolle
    zu spät angeboten als eine, die niemand verdient hat.
    """
    if not ist_gueltige_art(art):
        return False
    wert = werte.get(art)
    if wert is None:
        return False
    return int(wert) >= int(schwelle)


def fortschritt(art: str, schwelle: int, werte: dict) -> tuple[int, int]:
    """(erreicht, nötig) — für die Anzeige im Backend und in Nachrichten."""
    return int(werte.get(art) or 0), int(schwelle)


def ankuendigung(vorlage: str, *, mention: str, rolle: str, wert: int, schwelle: int) -> str:
    """Setzt die Platzhalter in den vom Admin hinterlegten Text ein.

    Eine leere oder fehlende Vorlage bekommt einen Standardtext - sonst stünde
    der Nutzer vor einer leeren Nachricht mit einem Knopf und wüsste nicht,
    worum es geht.
    """
    vorlage = (vorlage or "").strip()
    if not vorlage:
        vorlage = "🎉 {mention}, du hast **{wert}** erreicht — damit kannst du dir die Rolle **{rolle}** holen!"
    return (
        vorlage.replace("{mention}", mention)
        .replace("{rolle}", rolle)
        .replace("{wert}", str(wert))
        .replace("{schwelle}", str(schwelle))
    )


def knopf_kennung(regel_id: int, user_id) -> str:
    """Kennung des Abhol-Knopfes.

    Enthält die Nutzer-ID, damit der Knopf nur für den funktioniert, für den er
    gedacht ist. Sonst könnte jeder, der die Nachricht sieht, sich die Rolle
    holen - die Ankündigung steht ja im Kanal.

    Discord begrenzt die custom_id auf 100 Zeichen; die drei Bestandteile
    bleiben mit grossem Abstand darunter.
    """
    return f"verdienterolle:{int(regel_id)}:{user_id}"


def knopf_kennung_lesen(kennung: str) -> tuple[int, str] | None:
    """Zerlegt die Kennung wieder. None, wenn sie nicht von uns stammt."""
    teile = (kennung or "").split(":")
    if len(teile) != 3 or teile[0] != "verdienterolle":
        return None
    try:
        return int(teile[1]), teile[2]
    except ValueError:
        return None
