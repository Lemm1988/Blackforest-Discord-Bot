"""Entscheidet, ob ein Link in einer Nachricht ein Verstoß ist.

Bis zum 06.09.2026 steckte diese Entscheidung mitten in `cogs/moderation.py`,
verwoben mit dem Löschen der Nachricht, dem Verwarnen und dem Timeout. Man
konnte sie deshalb nur prüfen, indem man den ganzen Bot startete und eine
Nachricht schrieb.

Hier steht nur das Urteil — kein discord.py, keine Datenbank, kein Schreiben.
Das hat zwei Gründe: Die Regeln greifen ineinander und sind einzeln schwer zu
überblicken, und die geplante Testbox im Backend („Text eingeben, sehen ob der
Filter anschlägt und warum") braucht genau dieselbe Entscheidung, ohne dabei
etwas zu löschen.

**Die Reihenfolge der Regeln ist bewusst gewählt:**

1. **Sperrliste** — eine gesperrte Domain bleibt gesperrt, auch wenn sonst
   alles erlaubt wäre. Wer eine Domain ausdrücklich verbietet, meint es.
2. **Alles blockieren** — jeder Link ist ein Verstoß.
3. **Erlaubtliste** — ein Verstoß, außer die Domain steht darauf.
4. **Neue Mitglieder** — ein Verstoß, wenn der Autor noch nicht lange dabei
   ist; unabhängig von der Domain, sonst könnten neue Mitglieder über eine
   erlaubte Domain doch Links posten.

Die Ausnahmen für Rollen und Kanäle liegen bewusst **nicht** hier: Sie sagen
„diese Person" oder „dieser Kanal" ist außen vor, und das ist eine Frage des
Kontexts, nicht des Links. Sie werden vorher geprüft.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Erkennt Links in einer Nachricht. Bewusst großzügig: Lieber eine Adresse zu
#: viel finden und sie dann anhand der Regeln freigeben, als eine zu übersehen.
LINK_MUSTER = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)

SPERRLISTE = "sperrliste"
ALLES_BLOCKIEREN = "alles_blockieren"
NICHT_ERLAUBT = "nicht_erlaubt"
NEUES_MITGLIED = "neues_mitglied"

#: Warum der Filter angeschlagen hat, in einem Satz. Für die Testbox im Backend
#: und für das Protokoll — im Chat bekommt niemand die Regel genannt, das wäre
#: eine Anleitung zum Umgehen.
REGEL_TEXTE = {
    SPERRLISTE: "Die Domain steht auf der Sperrliste.",
    ALLES_BLOCKIEREN: "Es sind zurzeit gar keine Links erlaubt.",
    NICHT_ERLAUBT: "Die Domain steht nicht auf der Liste der erlaubten Domains.",
    NEUES_MITGLIED: "Neue Mitglieder dürfen noch keine Links posten.",
}


@dataclass(frozen=True)
class Entscheidung:
    """Das Urteil über einen Link."""

    verstoss: bool
    regel: str = ""
    domain: str = ""
    url: str = ""

    @property
    def begruendung(self) -> str:
        return REGEL_TEXTE.get(self.regel, "")


def domain_aus_url(url: str) -> str:
    """Reduziert eine Adresse auf die reine Domain.

    `https://www.example.com/foo?x=1` wird zu `example.com`.
    """
    rest = (url or "").strip()
    for vorsatz in ("https://", "http://", "www."):
        if rest.lower().startswith(vorsatz):
            rest = rest[len(vorsatz):]
    return rest.split("/")[0].split("?")[0].split("#")[0].split(":")[0].lower()


def _eintrag_normalisieren(eintrag: str) -> str:
    """Macht aus einem Listeneintrag eine vergleichbare Domain.

    `*.example.com` ist erlaubt und bedeutet dasselbe wie `example.com` —
    Unterdomains sind ohnehin immer eingeschlossen. Die Schreibweise wird
    angenommen, weil sie naheliegt und ein Eintrag, der stillschweigend nie
    greift, das Schlimmste ist, was eine Sperrliste tun kann.
    """
    wert = (eintrag or "").strip().lower()
    if wert.startswith("*."):
        wert = wert[2:]
    return domain_aus_url(wert)


def passt_auf_liste(domain: str, liste) -> bool:
    """True, wenn die Domain selbst oder eine ihrer Unterdomains auf der Liste steht.

    Steht `example.com` auf der Liste, gilt auch `cdn.example.com`. Die
    umgekehrte Richtung gilt nicht: `cdn.example.com` auf der Liste erlaubt
    nicht die ganze Domain.
    """
    if not domain:
        return False
    for eintrag in liste or ():
        norm = _eintrag_normalisieren(eintrag)
        if norm and (domain == norm or domain.endswith("." + norm)):
            return True
    return False


def erster_link(text: str) -> str:
    """Die erste Adresse in einer Nachricht, oder ein leerer Text."""
    treffer = LINK_MUSTER.search(text or "")
    return treffer.group(0) if treffer else ""


def pruefe(
    *,
    url: str,
    gesperrt=(),
    erlaubt=(),
    alles_blockieren: bool = False,
    erlaubtliste_aktiv: bool = False,
    neue_mitglieder_aktiv: bool = False,
    ist_neues_mitglied: bool = False,
) -> Entscheidung:
    """Das Urteil über eine einzelne Adresse."""
    domain = domain_aus_url(url)
    if not domain:
        return Entscheidung(False, url=url)

    if passt_auf_liste(domain, gesperrt):
        return Entscheidung(True, SPERRLISTE, domain, url)
    if alles_blockieren:
        return Entscheidung(True, ALLES_BLOCKIEREN, domain, url)
    if erlaubtliste_aktiv and not passt_auf_liste(domain, erlaubt):
        return Entscheidung(True, NICHT_ERLAUBT, domain, url)
    if neue_mitglieder_aktiv and ist_neues_mitglied:
        return Entscheidung(True, NEUES_MITGLIED, domain, url)
    return Entscheidung(False, "", domain, url)


def pruefe_nachricht(text: str, **regeln) -> Entscheidung:
    """Sucht die erste Adresse in einem Text und beurteilt sie.

    Für die Testbox im Backend: Dort gibt jemand einen ganzen Satz ein, keine
    blanke Adresse.
    """
    url = erster_link(text)
    if not url:
        return Entscheidung(False)
    return pruefe(url=url, **regeln)
