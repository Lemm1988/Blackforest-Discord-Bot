"""Aufbau der Hilfetexte — reine Textarbeit, ohne discord.py.

Ersetzt den entfernten >bothelp-Befehl und die eingebaute Hilfe von discord.py.
Beide hatten denselben Mangel: Sie zeigten **jedem alles**. `grant_access` und
`revoke_access` standen damit in der Nachricht, die jedes Mitglied bekam.

Zwei Grundsätze aus der Absprache mit Patrick (03.09.2026):

* Wer einen Befehl nicht benutzen darf, sieht ihn hier auch nicht — nicht
  ausgegraut, nicht mit Hinweis, sondern gar nicht.
* Die Gliederung folgt den **Modulen** des Bots. Sie sollen sich später einzeln
  installieren lassen, also muss auch die Hilfe erkennen lassen, was zu wem
  gehört. Fällt ein Modul weg, verschwindet sein Block von selbst mit.

Warum das hier ohne discord.py steht: Damit die Texte ohne laufenden Bot prüfbar
sind (die CI installiert bewusst nur pytest), und damit die Formulierungen an
einer Stelle liegen statt zwischen Embed-Aufrufen verstreut. Der Cog
(cogs/hilfe.py) sammelt nur die Daten ein und baut das Embed.
"""
from __future__ import annotations

BEGRUESSUNG = (
    "Hallo! Ich bin **Blackforest's Helper**. 🌱\n"
    "Ich kümmere mich hier um das Pflanzenquiz, euren Fortschritt auf dem Server "
    "und die Ordnung im Chat."
)

ABSCHLUSS = (
    "Tipp: `/help <befehl>` erklärt einen einzelnen Befehl genauer — "
    "zum Beispiel `/help frage`."
)

# Kein einziger Befehl passt? Kann eigentlich nicht vorkommen, weil die meisten
# fuer alle offen sind - aber ein leeres Embed waere ratlos machend.
LEER = (
    "Für dich sind gerade keine Befehle verfügbar. Wende dich an einen Admin, "
    "wenn du denkst, dass das nicht stimmt."
)


def darf_sehen(recht: str | None, rechte_pruefer) -> bool:
    """Entscheidet, ob ein Befehl in der Hilfe auftaucht.

    recht=None bedeutet "für alle offen". Sonst wird gefragt, ob der Aufrufer
    das Flag hat. Die Prüfung selbst wird hereingereicht, damit dieses Modul
    nicht die Datenbank kennen muss.
    """
    if not recht:
        return True
    return bool(rechte_pruefer(recht))


def sichtbare_befehle(befehle: list[dict], rechte_pruefer) -> list[dict]:
    return [b for b in befehle if darf_sehen(b.get("recht"), rechte_pruefer)]


def modulblock(modul: dict) -> str:
    """Ein Modul als Textblock: Überschrift, dann eine Zeile je Befehl."""
    zeilen = [f"**{modul['label']}**"]
    for befehl in modul["befehle"]:
        zeilen.append(f"`/{befehl['name']}` — {befehl['kurz']}")
    return "\n".join(zeilen)


def uebersicht(module: list[dict], rechte_pruefer) -> str:
    """Der komplette Hilfetext für einen bestimmten Aufrufer.

    module: [{"label": ..., "reihenfolge": int, "befehle": [{...}]}]
    Module ohne sichtbare Befehle fallen ganz weg — sonst stünde bei einem
    normalen Mitglied eine leere Überschrift "Administration" da und verriete,
    dass es dort etwas gibt.
    """
    bloecke = []
    for modul in sorted(module, key=lambda m: (m.get("reihenfolge", 99), m["label"])):
        sichtbar = sichtbare_befehle(modul["befehle"], rechte_pruefer)
        if not sichtbar:
            continue
        bloecke.append(modulblock({**modul, "befehle": sichtbar}))

    if not bloecke:
        return f"{BEGRUESSUNG}\n\n{LEER}"

    return f"{BEGRUESSUNG}\n\n" + "\n\n".join(bloecke) + f"\n\n{ABSCHLUSS}"


def einzelhilfe(befehl: dict) -> str:
    """Der ausführliche Text zu einem einzelnen Befehl.

    "lang" ist optional: Fehlt er, wird die Kurzbeschreibung genommen, damit ein
    neu hinzugekommener Befehl nicht mit einer leeren Hilfe dasteht.
    """
    teile = [f"**/{befehl['name']}**", befehl.get("lang") or befehl["kurz"]]

    if befehl.get("verwendung"):
        teile.append(f"\n**So rufst du ihn auf**\n`{befehl['verwendung']}`")

    if befehl.get("recht"):
        teile.append(
            f"\n**Berechtigung**\nDafür brauchst du das Recht `{befehl['recht']}`."
        )
    return "\n".join(teile)


def nicht_gefunden(name: str, verfuegbar: list[str]) -> str:
    """Antwort auf `/help quatsch`.

    Nennt bewusst NUR die Befehle, die der Aufrufer ohnehin sehen darf - sonst
    waere die Fehlermeldung ein Schleichweg, um die Adminbefehle zu erfahren.
    """
    text = f"Einen Befehl `/{name}` kenne ich nicht."
    if verfuegbar:
        liste = ", ".join(f"`/{n}`" for n in sorted(verfuegbar))
        text += f"\n\nDas hier kannst du benutzen:\n{liste}"
    return text
