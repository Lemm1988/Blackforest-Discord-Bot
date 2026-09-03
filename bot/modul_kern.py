"""Kernmodul — was ohne jedes Zusatzmodul funktioniert.

Bringt kein eigenes Schema mit: Die gemeinsam genutzten Tabellen
(Einstellungen, Audit-Log, Berechtigungen, Rollenregeln) stehen weiterhin
zentral in database.py, weil sie zu keinem verkaufbaren Modul gehoeren.

Die Zugehoerigkeit zum Server kennt Discord selbst - dafuer braucht es weder
Quiz noch XP-System.
"""
import module
import verdiente_rollen
from verdiente_rollen import Bedingungsart

NAME = "kern"

# wert=None: Kommt nicht aus der Datenbank, sondern vom Cog - nur der kennt das
# Beitrittsdatum.
verdiente_rollen.REGISTRATUR.eintragen(Bedingungsart(
    "tage_dabei", "Tage auf dem Server", "Tage",
    "Seit dem Beitritt, unabh\u00e4ngig von der Aktivit\u00e4t.",
    NAME, None,
))

module.REGISTRATUR.eintragen(module.Modul(name=NAME, label="Kern"))
