"""Zeitstempel werden in Ortszeit angezeigt, nicht in UTC.

Hintergrund (06.09.2026): Beim Suchen eines Fehlers zeigte der Audit-Log im
Backend 21:29, das Systemlog fuer dasselbe Ereignis 23:29. Es sah nach zwei
verschiedenen Vorgaengen aus und kostete Zeit, bis der Groschen fiel.

Ursache: SQLite schreibt mit `datetime('now')` UTC. Alle sieben Zeitspalten der
Datenbank sind davon betroffen - timestamp, granted_at, created_at,
unlocked_at, vergeben_at, added_at, updated_at.

Gespeichert bleibt UTC. Das ist richtig: Es ist eindeutig und ueberlebt jeden
Wechsel der Zeitzone. Umgerechnet wird erst bei der Anzeige.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = (REPO / "backend" / "app.py").read_text(encoding="utf-8")
TEMPLATES = REPO / "backend" / "templates"

#: Die Spalten, die per SQLite-Default entstehen und damit UTC sind.
UTC_SPALTEN = ("timestamp", "granted_at", "created_at", "unlocked_at",
               "vergeben_at", "added_at", "updated_at", "revoked_at")


def test_der_filter_existiert():
    assert '@app.template_filter("lokalzeit")' in APP


def test_der_filter_erklaert_warum_es_ihn_gibt():
    """Sonst nimmt ihn beim naechsten Aufraeumen jemand wieder heraus."""
    stelle = APP[APP.index('@app.template_filter("lokalzeit")'):][:1600]
    assert "UTC" in stelle and "Anzeige" in stelle


def test_jede_angezeigte_zeitspalte_geht_durch_den_filter():
    """Die eigentliche Absicherung: eine neue Vorlage, die einen Zeitstempel
    roh ausgibt, faellt hier auf."""
    roh = []
    for datei in sorted(TEMPLATES.glob("*.html")):
        for nr, zeile in enumerate(datei.read_text(encoding="utf-8").splitlines(), 1):
            for ausdruck in re.findall(r"\{\{[^}]*\}\}", zeile):
                if not any(re.search(rf"\b{s}\b", ausdruck) for s in UTC_SPALTEN):
                    continue
                if "lokalzeit" in ausdruck:
                    continue
                roh.append(f"{datei.name}:{nr} {ausdruck.strip()}")
    assert not roh, "Zeitstempel ohne | lokalzeit:\n" + "\n".join(roh)


def test_unbekannte_werte_werden_nicht_verschluckt():
    """Lieber roh anzeigen als eine leere Zelle, die nichts erklaert."""
    stelle = APP[APP.index('@app.template_filter("lokalzeit")'):][:1800]
    assert "return wert" in stelle


def test_leere_werte_ergeben_leeren_text():
    stelle = APP[APP.index('@app.template_filter("lokalzeit")'):][:1800]
    assert "if not wert:" in stelle
