"""Gemeinsame Test-Fixtures.

Die Tests laufen auf GitHub-Runnern, also ohne systemd, ohne dumbpipe-Binary und
ohne den Pi. Alles Privilegierte (sudo/systemctl) wird deshalb durch einen
steuerbaren Fake ersetzt - was kein Verlust ist, im Gegenteil: Genau so lassen
sich die Faelle pruefen, die auf echter Hardware kaum reproduzierbar sind, etwa
"sudo wird verweigert" oder "der Dienst laeuft nach dem Widerruf immer noch".
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "bot"))


class FakeSystemd:
    """Ersetzt subprocess.run fuer sudo-/systemctl-Aufrufe.

    Bildet die beiden Eigenheiten nach, die in der Praxis zu einem echten
    Sicherheitsfehler gefuehrt haben:

    1. Bei verweigertem sudo ist der Rueckgabewert != 0, stderr enthaelt die
       Meldung und **stdout ist leer**. Genau diese leere Ausgabe wurde frueher
       als "Dienst ist nicht aktiv" fehlgedeutet - der Widerruf meldete Erfolg,
       waehrend der Tunnel weiterlief.
    2. `systemctl is-active` liefert auch im Normalfall einen Rueckgabewert != 0,
       wenn der Dienst nicht laeuft. Der Rueckgabewert allein taugt also nicht als
       Erfolgskriterium - entscheidend ist die Ausgabe, die aber vorhanden sein muss.
    """

    def __init__(self):
        self.active: set[str] = set()
        self.sudo_denied = False
        self.calls: list[list[str]] = []
        self.unstoppable: set[str] = set()

    def run(self, cmd, *args, **kwargs):
        self.calls.append(list(cmd))

        if not cmd or cmd[0] != "sudo":
            if cmd and "generate-ticket" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "endpointtestticket000", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        if self.sudo_denied:
            return subprocess.CompletedProcess(cmd, 1, "", "sudo: Ein Passwort ist notwendig")

        tool = cmd[1] if len(cmd) > 1 else ""

        if tool == "tee":
            Path(cmd[2]).write_text(kwargs.get("input") or "unit\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        if tool == "rm":
            for p in cmd[2:]:
                if p.startswith("/"):
                    Path(p).unlink(missing_ok=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        if tool != "systemctl":
            return subprocess.CompletedProcess(cmd, 0, "", "")

        verb = cmd[2] if len(cmd) > 2 else ""
        unit = cmd[-1]

        if verb == "is-active":
            state = "active" if unit in self.active else "inactive"
            return subprocess.CompletedProcess(cmd, 0 if state == "active" else 3, state, "")
        if verb == "stop":
            if unit not in self.unstoppable:
                self.active.discard(unit)
        elif verb in ("start", "enable"):
            if verb == "start" or "--now" in cmd:
                self.active.add(unit)
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def core(tmp_path, monkeypatch):
    """tunnel_access_core mit temporaeren Pfaden und gefaktem systemd.

    Der Fake haengt als `core.fake` am Modul, damit Tests den Zustand steuern
    koennen (z.B. `core.fake.sudo_denied = True`).
    """
    import tunnel_access_core as mod

    config_dir = tmp_path / "config"
    systemd_dir = tmp_path / "systemd"
    config_dir.mkdir()
    systemd_dir.mkdir()

    monkeypatch.setattr(mod, "DB_PATH", tmp_path / "bot.db")
    monkeypatch.setattr(mod, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(mod, "SYSTEMD_DIR", systemd_dir)
    monkeypatch.setattr(mod, "DUMBPIPE_BIN", tmp_path / "dumbpipe")
    monkeypatch.setattr(mod, "SERVICE_USER", "testuser")

    fake = FakeSystemd()
    monkeypatch.setattr(mod.subprocess, "run", fake.run)
    # Wartezeiten in _stop_service_reliably/resume_grant ueberspringen
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    mod.fake = fake
    return mod
