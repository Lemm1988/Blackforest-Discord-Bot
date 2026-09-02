"""Regressionstests fuer die Tunnel-Zugangsverwaltung.

Jeder Test hier bildet einen Fehler ab, der real aufgetreten ist. Alle waren
**stumm** - nichts stuerzte ab, nichts stand im Log, die Oberflaeche meldete
Erfolg. Genau deshalb sind sie hier festgeschrieben: Ein Widerruf, der nur
scheinbar wirkt, ist schlimmer als einer, der sichtbar fehlschlaegt.
"""
from __future__ import annotations

import pytest

DISCORD_ID = "123456789012345678"


def _svc(slug: str) -> str:
    return f"dumbpipe-discordbot-{slug}.service"


# --------------------------------------------------------------------------
# Der Kernfehler: verweigertes sudo wurde als "Dienst gestoppt" gewertet
# --------------------------------------------------------------------------

def test_stop_raises_when_sudo_is_denied(core):
    """Verweigertes sudo darf NIEMALS als Erfolg durchgehen.

    Der urspruengliche Code pruefte nur `result.stdout.strip() != "active"`.
    Bei verweigertem sudo ist stdout leer, und leer ist nicht "active" - also
    galt der Dienst als gestoppt, obwohl gar kein Befehl gelaufen war.
    """
    core.fake.sudo_denied = True
    with pytest.raises(core.TunnelAccessError):
        core._stop_service_reliably(_svc("egal"))


def test_revoke_fails_loudly_when_sudo_is_denied(core):
    """Ein Widerruf ohne Rechte muss abbrechen, nicht Erfolg melden."""
    core.add_grant("person", DISCORD_ID)
    core.fake.sudo_denied = True

    with pytest.raises(core.TunnelAccessError):
        core.revoke_grant("person")

    # Und er darf den Zugang dann auch NICHT als widerrufen markieren -
    # sonst zeigt die Oberflaeche "widerrufen" bei laufendem Tunnel.
    grant = [g for g in core.list_grants() if g["slug"] == "person"][0]
    assert grant["revoked_at"] is None


def test_revoke_stops_service_and_removes_files(core):
    """Der Normalfall: nach dem Widerruf ist wirklich alles weg."""
    core.add_grant("person", DISCORD_ID)
    assert _svc("person") in core.fake.active

    core.revoke_grant("person")

    assert _svc("person") not in core.fake.active
    assert not (core.SYSTEMD_DIR / _svc("person")).exists()
    assert not (core.CONFIG_DIR / "env-person-person").exists()


def test_revoke_refuses_when_service_cannot_be_stopped(core):
    """Laesst sich der Dienst nicht stoppen (Absturz-Neustart-Schleife), darf der
    Widerruf nicht so tun, als waere er erfolgt."""
    core.add_grant("person", DISCORD_ID)
    core.fake.unstoppable.add(_svc("person"))

    with pytest.raises(core.TunnelAccessError):
        core.revoke_grant("person")

    grant = [g for g in core.list_grants() if g["slug"] == "person"][0]
    assert grant["revoked_at"] is None


# --------------------------------------------------------------------------
# Loeschen per ID statt per Name
# --------------------------------------------------------------------------

def test_delete_history_entry_keeps_the_active_grant(core):
    """Aufraeumen der Historie darf den aktiven Zugang nicht mitreissen.

    Dienst-, Datei- und Secret-Namen haengen allein am Slug. Nach Widerruf und
    Neuanlage existieren mehrere Zeilen desselben Namens - ein Loeschen "per Name"
    entzog dabei versehentlich den noch aktiven Zugang.
    """
    core.add_grant("person", DISCORD_ID)
    core.revoke_grant("person")
    core.add_grant("person", DISCORD_ID)

    rows = [g for g in core.list_grants() if g["slug"] == "person"]
    alt = next(g for g in rows if g["revoked_at"])
    aktiv = next(g for g in rows if not g["revoked_at"])

    core.delete_grant(alt["id"])

    verbleibend = [g for g in core.list_grants() if g["slug"] == "person"]
    assert [g["id"] for g in verbleibend] == [aktiv["id"]]
    assert _svc("person") in core.fake.active, "Tunnel des aktiven Zugangs muss weiterlaufen"


def test_delete_of_live_grant_revokes_first(core):
    """Ein noch aktiver Eintrag darf nicht einfach aus der DB verschwinden -
    sonst liefe sein Tunnel weiter, ohne dass ihn noch jemand sehen oder
    abschalten koennte."""
    core.add_grant("person", DISCORD_ID)
    grant = [g for g in core.list_grants() if g["slug"] == "person"][0]

    core.delete_grant(grant["id"])

    assert _svc("person") not in core.fake.active
    assert not (core.CONFIG_DIR / "env-person-person").exists()


# --------------------------------------------------------------------------
# Status muss die Wirklichkeit zeigen, nicht nur die Datenbank
# --------------------------------------------------------------------------

def test_status_flags_revoked_but_still_running_as_danger(core):
    """Der gefaehrlichste Zustand ueberhaupt - muss als GEFAHR auffallen."""
    core.add_grant("person", DISCORD_ID)
    core.revoke_grant("person")
    # Tunnel laeuft entgegen der Datenbank wieder
    core.fake.active.add(_svc("person"))

    grant = [g for g in core.list_grants() if g["slug"] == "person"][0]
    assert grant["status"] == "GEFAHR"


def test_status_flags_active_without_tunnel_as_broken(core):
    """Als aktiv gefuehrt, aber kein Tunnel - die Person kaeme nicht durch."""
    core.add_grant("person", DISCORD_ID)
    core.fake.active.discard(_svc("person"))

    grant = [g for g in core.list_grants() if g["slug"] == "person"][0]
    assert grant["status"] == "DEFEKT"


def test_replaced_history_row_is_not_a_false_alarm(core):
    """Nach Widerruf und Neuanlage gehoert der laufende Dienst dem NEUEN Eintrag.
    Die alte Zeile darf deshalb nicht als GEFAHR erscheinen."""
    core.add_grant("person", DISCORD_ID)
    core.revoke_grant("person")
    core.add_grant("person", DISCORD_ID)

    rows = [g for g in core.list_grants() if g["slug"] == "person"]
    alt = next(g for g in rows if g["revoked_at"])
    aktiv = next(g for g in rows if not g["revoked_at"])

    assert alt["status"] == "ersetzt"
    assert aktiv["status"] == "aktiv"


# --------------------------------------------------------------------------
# Pausieren: Verbindung zu, Ticket bleibt gueltig
# --------------------------------------------------------------------------

def test_pause_stops_tunnel_but_keeps_secret(core):
    core.add_grant("person", DISCORD_ID)
    core.pause_grant("person")

    grant = [g for g in core.list_grants() if g["slug"] == "person"][0]
    assert grant["status"] == "pausiert"
    assert _svc("person") not in core.fake.active
    assert (core.CONFIG_DIR / "env-person-person").exists(), "Ticket muss gueltig bleiben"


def test_resume_restores_the_same_ticket(core):
    """Nach dem Fortsetzen muss dasselbe Ticket weiter funktionieren - sonst
    waere Pausieren nur ein umstaendlicher Widerruf."""
    core.add_grant("person", DISCORD_ID)
    vorher = (core.CONFIG_DIR / "env-person-person").read_text()

    core.pause_grant("person")
    core.resume_grant("person")

    grant = [g for g in core.list_grants() if g["slug"] == "person"][0]
    assert grant["status"] == "aktiv"
    assert (core.CONFIG_DIR / "env-person-person").read_text() == vorher


def test_pause_is_rejected_for_revoked_grant(core):
    core.add_grant("person", DISCORD_ID)
    core.revoke_grant("person")
    with pytest.raises(core.TunnelAccessError):
        core.pause_grant("person")


# --------------------------------------------------------------------------
# Neu setzen erzeugt zwingend ein anderes Secret
# --------------------------------------------------------------------------

def test_reset_creates_a_new_secret(core):
    """Ein Widerruf darf keine blosse Pause sein: Nach dem Neusetzen muss das
    alte Secret weg und ein anderes da sein."""
    core.add_grant("person", DISCORD_ID)
    altes_secret = (core.CONFIG_DIR / "env-person-person").read_text()

    core.reset_grant("person")

    neues_secret = (core.CONFIG_DIR / "env-person-person").read_text()
    assert neues_secret != altes_secret


# --------------------------------------------------------------------------
# Keine fest verdrahteten Pfade mehr
# --------------------------------------------------------------------------

def test_no_hardcoded_user_paths_in_source():
    """Absolute Pfade auf ein bestimmtes Home-Verzeichnis haben beim Umzug auf ein
    neues System stillschweigend alles lahmgelegt: Das Dashboard startete, konnte
    aber die Datenbank nicht oeffnen, und neu erzeugte Units trugen einen falschen
    Benutzer."""
    from pathlib import Path

    quelle = (Path(__file__).resolve().parent.parent / "scripts" / "tunnel_access_core.py").read_text()
    code = "\n".join(
        z for z in quelle.splitlines() if not z.lstrip().startswith("#")
    )
    assert "/home/picom" not in code
    assert "User=picom" not in code
