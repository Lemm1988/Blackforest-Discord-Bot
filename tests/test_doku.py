"""Die Dokumentation sagt die Wahrheit über die Testsuite.

Abschnitt 16 der Entwicklerdoku führt jede Testdatei mit ihrer Anzahl auf. Diese
Zahlen sind zweimal stillschweigend veraltet — beide Male, weil zwei Testdateien
**pro Template** parametrisiert sind (`test_nutzertexte.py` und
`test_csrf_coverage.py`): Eine neue Seite im Backend lässt ihre Testzahl von
selbst wachsen, ohne dass jemand eine Testdatei anfasst.

Eine Doku mit falschen Zahlen ist schlimmer als eine ohne: Wer sie liest,
verlässt sich darauf. Deshalb prüft dieser Test sie gegen die Wirklichkeit.

Schlägt er fehl, ist nicht der Code kaputt — dann muss die Tabelle in
`docs/BlackforestBot-Dokumentation.md`, Abschnitt 16, nachgezogen werden.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOKU = REPO / "docs" / "BlackforestBot-Dokumentation.md"
TESTS = REPO / "tests"


def _dokumentierte_zahlen() -> dict[str, int]:
    treffer = re.findall(r"^\| `(test_[a-z_]+\.py)` \| (\d+) \|", DOKU.read_text(), re.M)
    return {name: int(anzahl) for name, anzahl in treffer}


def _tatsaechliche_zahlen() -> dict[str, int]:
    """Zählt über pytest selbst, nicht über eine eigene Zählweise - sonst
    weicht der Test von dem ab, was die CI meldet."""
    import subprocess
    import sys

    ergebnis = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(TESTS)],
        capture_output=True, text=True, cwd=str(REPO),
    )
    zahlen: dict[str, int] = {}
    for zeile in ergebnis.stdout.splitlines():
        if "::" not in zeile:
            continue
        datei = zeile.split("::")[0].split("/")[-1]
        if datei.startswith("test_"):
            zahlen[datei] = zahlen.get(datei, 0) + 1
    return zahlen


def test_jede_testdatei_steht_in_der_doku():
    fehlend = set(_tatsaechliche_zahlen()) - set(_dokumentierte_zahlen())
    assert not fehlend, (
        f"Diese Testdateien fehlen in der Tabelle in Abschnitt 16 der Doku: "
        f"{sorted(fehlend)}"
    )


def test_keine_verschwundene_datei_in_der_doku():
    ueberzaehlig = set(_dokumentierte_zahlen()) - set(_tatsaechliche_zahlen())
    assert not ueberzaehlig, (
        f"Die Doku führt Testdateien auf, die es nicht mehr gibt: {sorted(ueberzaehlig)}"
    )


def test_die_zahlen_stimmen():
    doku, echt = _dokumentierte_zahlen(), _tatsaechliche_zahlen()
    abweichungen = {
        name: (doku[name], echt[name])
        for name in doku.keys() & echt.keys()
        if doku[name] != echt[name]
    }
    assert not abweichungen, (
        "Abschnitt 16 der Doku nennt falsche Testzahlen "
        f"(Doku, tatsächlich): {abweichungen}"
    )


def test_die_gesamtzahl_in_der_ueberschrift_stimmt():
    """Sie steht an zwei Stellen - im Inhaltsverzeichnis und in der Überschrift."""
    text = DOKU.read_text()
    genannt = {int(n) for n in re.findall(r"Testsuite \(`tests/`, (\d+) Tests\)", text)}
    echt = sum(_tatsaechliche_zahlen().values())
    assert genannt, "In der Doku steht keine Gesamtzahl."
    assert genannt == {echt}, (
        f"Die Doku nennt {sorted(genannt)} Tests, tatsächlich sind es {echt}."
    )
