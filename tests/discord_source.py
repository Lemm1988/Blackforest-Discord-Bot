"""Laedt einzelne Funktionen aus backend/app.py, ohne das Modul zu importieren.

Hintergrund: backend/app.py importiert discord.py, Flask und die Bot-Datenbank.
Die CI installiert bewusst nur pytest (siehe .github/workflows/tests.yml), damit
die Tests schnell bleiben und nicht an Paketversionen scheitern. Ein direkter
Import waere dort also nicht moeglich.

Statt deshalb auf Tests zu verzichten oder die Logik zu duplizieren (eine Kopie
wuerde genau dann auseinanderlaufen, wenn es darauf ankommt), wird hier der
Quelltext geparst und nur der benoetigte Ausschnitt ausgefuehrt. Getestet wird
damit der echte Produktivcode, nicht ein Nachbau.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_PY = REPO / "backend" / "app.py"


class FakeEmbed:
    """Minimaler Ersatz fuer discord.Embed.

    __len__ zaehlt exakt das, was Discord fuer das 6000-Zeichen-Gesamtlimit
    zusammenrechnet: "the combined sum of characters in all title, description,
    field.name, field.value, footer.text, and author.name fields".
    Nachgeprueft gegen discord.py 2.4.0, Embed.__len__ - die Implementierung
    dort zaehlt dieselben sechs Felder und sonst nichts (insbesondere keine
    URLs, keine Bilder).
    """

    def __init__(self, *, title=None, description=None, color=None, url=None):
        self.title = title
        self.description = description
        self.color = color
        self.url = url
        self.fields: list[dict] = []
        self.footer: dict | None = None
        self.author: dict | None = None
        self.image: str | None = None
        self.thumbnail: str | None = None
        self.timestamp = None

    def add_field(self, *, name, value, inline=False):
        self.fields.append({"name": name, "value": value, "inline": inline})

    def set_author(self, *, name, url=None, icon_url=None):
        self.author = {"name": name, "url": url, "icon_url": icon_url}

    def set_footer(self, *, text, icon_url=None):
        self.footer = {"text": text, "icon_url": icon_url}

    def set_image(self, *, url):
        self.image = url

    def set_thumbnail(self, *, url):
        self.thumbnail = url

    def __len__(self):
        total = len(self.title or "") + len(self.description or "")
        for field in self.fields:
            total += len(field["name"]) + len(field["value"])
        if self.footer:
            total += len(self.footer["text"])
        if self.author:
            total += len(self.author["name"])
        return total


class _FakeDiscordModule:
    Embed = FakeEmbed


def load_from_app(*namen: str) -> dict:
    """Fuehrt genau die genannten Top-Level-Definitionen aus backend/app.py aus.

    Alles andere im Modul (Flask-App, Datenbank, discord.py-Import) wird nicht
    angefasst. Fehlt einer der Namen, schlaegt das hier laut fehl statt still
    ein leeres Ergebnis zu liefern - sonst wuerde ein umbenannter Helfer die
    Tests unbemerkt wirkungslos machen.
    """
    baum = ast.parse(APP_PY.read_text(), filename=str(APP_PY))
    gesucht = set(namen)
    knoten = []

    for node in baum.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in gesucht:
            knoten.append(node)
            gesucht.discard(node.name)
        elif isinstance(node, ast.Assign):
            for ziel in node.targets:
                if isinstance(ziel, ast.Name) and ziel.id in gesucht:
                    knoten.append(node)
                    gesucht.discard(ziel.id)

    if gesucht:
        raise AssertionError(
            f"In {APP_PY.name} nicht gefunden: {sorted(gesucht)}. "
            "Wurde umbenannt oder verschoben - der Test muss nachgezogen werden."
        )

    from datetime import datetime, timezone

    namensraum: dict = {
        "discord": _FakeDiscordModule,
        "datetime": datetime,
        "timezone": timezone,
        "__name__": "app_ausschnitt",
    }
    modul = ast.Module(body=knoten, type_ignores=[])
    exec(compile(modul, filename=str(APP_PY), mode="exec"), namensraum)  # noqa: S102
    return namensraum
