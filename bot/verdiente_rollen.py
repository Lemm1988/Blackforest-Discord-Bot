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


@dataclass(frozen=True)
class Bedingungsart:
    schluessel: str
    label: str
    einheit: str
    hinweis: str


# Die auswählbaren Bedingungen. Jede stützt sich auf einen Wert, den der Bot
# ohnehin führt - nachvollziehbar für den Nutzer und nicht fälschbar.
BEDINGUNGSARTEN = {
    a.schluessel: a
    for a in (
        Bedingungsart(
            "quiz_richtig", "Richtig beantwortete Quizfragen", "Fragen",
            "Zählt jede richtig beantwortete Quizfrage seit Beginn.",
        ),
        Bedingungsart(
            "level", "Erreichtes Level", "Level",
            "Level ergibt sich aus den gesammelten XP (Chat, Quiz, Zufallsereignisse).",
        ),
        Bedingungsart(
            "xp", "Gesammelte XP", "XP",
            "Alle XP zusammen, unabhängig davon, woher sie stammen.",
        ),
        Bedingungsart(
            "fragen_eingereicht", "Eingereichte Quizfragen", "Fragen",
            "Zählt eingereichte Fragen, auch noch nicht freigegebene.",
        ),
        Bedingungsart(
            "tage_dabei", "Tage auf dem Server", "Tage",
            "Seit dem Beitritt, unabhängig von der Aktivität.",
        ),
    )
}


def ist_gueltige_art(art: str) -> bool:
    return art in BEDINGUNGSARTEN


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
