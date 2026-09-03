"""Bedingungen, die zu keinem verkaufbaren Modul gehören.

Die Zugehörigkeit zum Server kennt Discord selbst - dafür braucht es weder Quiz
noch XP-System. Diese Bedingung ist deshalb immer verfügbar.

wert=None: Der Wert kommt nicht aus der Datenbank, sondern vom Cog, der als
Einziger das Beitrittsdatum kennt.
"""
from verdiente_rollen import REGISTRATUR, Bedingungsart

MODUL = "kern"

REGISTRATUR.eintragen(Bedingungsart(
    schluessel="tage_dabei",
    label="Tage auf dem Server",
    einheit="Tage",
    hinweis="Seit dem Beitritt, unabhängig von der Aktivität.",
    modul=MODUL,
    wert=None,
))
