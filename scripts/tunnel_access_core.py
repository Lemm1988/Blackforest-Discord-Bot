"""Gemeinsame Kernlogik zur Verwaltung der Iroh-Tunnel-Zugaenge (App-Zugaenge der
Android-App). Bewusst die EINZIGE Implementierung dieser sicherheitsrelevanten
Logik (Secret-Erzeugung, systemd-Dienst-Verwaltung, Ticket-Erzeugung) - wird
sowohl vom CLI-Wrapper (manage_bot_access.sh, fuer SSH-Nutzung) als auch vom
lokalen Bot Connect Dashboard (bot-connect-dashboard/app.py, NUR 127.0.0.1,
siehe dessen README.md) importiert. Keine Duplikate dieser Logik pflegen.
"""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/home/picom/Desktop/BlackforestBot-v2/bot/data/bot.db")
CONFIG_DIR = Path("/home/picom/.config/dumbpipe")
DUMBPIPE_BIN = Path("/home/picom/.cargo/bin/dumbpipe")
SYSTEMD_DIR = Path("/etc/systemd/system")


class TunnelAccessError(Exception):
    """Erwartbare, dem Bedienenden anzuzeigende Fehler (leerer Name, Duplikat,
    fehlgeschlagene systemd-Einrichtung) - kein Programmierfehler."""


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tunnel_access_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            revoked_at TEXT
        )
        """
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower())
    return slug.strip("-")


def list_grants() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT label, slug, created_at, revoked_at FROM tunnel_access_grants "
            "ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def add_grant(label: str) -> str:
    """Legt einen neuen, eigenstaendigen dumbpipe-Dienst (eigenes Secret, eigener
    systemd-Dienst, alle leiten zu localhost:5000 weiter) an und gibt das frisch
    erzeugte Ticket zurueck."""
    label = (label or "").strip()
    if not label:
        raise TunnelAccessError("Name/Label darf nicht leer sein.")
    slug = slugify(label)
    if not slug:
        raise TunnelAccessError("Name ergibt einen leeren Slug (nur Sonderzeichen?).")

    with _connect() as conn:
        existing = conn.execute(
            "SELECT COUNT(*) c FROM tunnel_access_grants WHERE slug = ? AND revoked_at IS NULL",
            (slug,),
        ).fetchone()["c"]
        if existing:
            raise TunnelAccessError(f"Ein aktiver Zugang mit dem Namen '{label}' existiert bereits.")

    secret_file = CONFIG_DIR / f"env-person-{slug}"
    service_name = f"dumbpipe-discordbot-{slug}.service"
    unit_file = SYSTEMD_DIR / service_name

    if secret_file.exists() or unit_file.exists():
        raise TunnelAccessError(
            f"Es gibt bereits Reste eines Zugangs mit Slug '{slug}' (Secret- oder "
            "Dienst-Datei vorhanden, evtl. von einem frueher widerrufenen, "
            "gleichnamigen Zugang). Bitte anderen Namen waehlen oder manuell aufraeumen."
        )

    secret = secrets.token_hex(32)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(f"IROH_SECRET={secret}\n")
    secret_file.chmod(0o600)

    unit_content = f"""[Unit]
Description=dumbpipe iroh tunnel for Discord bot backend - person: {label}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=picom
EnvironmentFile={secret_file}
ExecStart={DUMBPIPE_BIN} listen-tcp --host localhost:5000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    try:
        subprocess.run(
            ["sudo", "tee", str(unit_file)],
            input=unit_content, text=True, capture_output=True, check=True,
        )
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True, capture_output=True)
        subprocess.run(
            ["sudo", "systemctl", "enable", "--now", service_name],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        secret_file.unlink(missing_ok=True)
        raise TunnelAccessError(f"systemd-Einrichtung fehlgeschlagen: {e.stderr}") from e

    time.sleep(1)  # kurz warten, bis der Dienst wirklich laeuft

    try:
        ticket = subprocess.run(
            [str(DUMBPIPE_BIN), "generate-ticket"],
            env={**os.environ, "IROH_SECRET": secret},
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as e:
        raise TunnelAccessError(f"Ticket-Erzeugung fehlgeschlagen: {e.stderr}") from e

    with _connect() as conn:
        conn.execute(
            "INSERT INTO tunnel_access_grants (label, slug, created_at, revoked_at) VALUES (?, ?, ?, NULL)",
            (label, slug, _now()),
        )
        conn.commit()

    return ticket


def revoke_grant(label: str) -> None:
    """Stoppt+deaktiviert NUR den zu diesem Namen gehoerenden Dienst, loescht dessen
    Secret, markiert den Eintrag als widerrufen (nicht loeschen - Nachvollziehbarkeit,
    gleiches Prinzip wie beim Audit-Log). Alle anderen Zugaenge bleiben unberuehrt."""
    label = (label or "").strip()
    if not label:
        raise TunnelAccessError("Name/Label darf nicht leer sein.")
    slug = slugify(label)

    with _connect() as conn:
        found = conn.execute(
            "SELECT COUNT(*) c FROM tunnel_access_grants WHERE slug = ? AND revoked_at IS NULL",
            (slug,),
        ).fetchone()["c"]
        if not found:
            raise TunnelAccessError(f"Kein aktiver Zugang mit dem Namen '{label}' gefunden.")

    service_name = f"dumbpipe-discordbot-{slug}.service"
    unit_file = SYSTEMD_DIR / service_name
    secret_file = CONFIG_DIR / f"env-person-{slug}"

    subprocess.run(["sudo", "systemctl", "stop", service_name], capture_output=True)
    subprocess.run(["sudo", "systemctl", "disable", service_name], capture_output=True)
    subprocess.run(["sudo", "rm", "-f", str(unit_file)], capture_output=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], capture_output=True)
    secret_file.unlink(missing_ok=True)

    with _connect() as conn:
        conn.execute(
            "UPDATE tunnel_access_grants SET revoked_at = ? WHERE slug = ? AND revoked_at IS NULL",
            (_now(), slug),
        )
        conn.commit()


# --- CLI-Einstiegspunkt, genutzt vom duennen manage_bot_access.sh-Wrapper ---

def _cli() -> None:
    if len(sys.argv) < 2:
        _usage()
    cmd = sys.argv[1]
    try:
        if cmd == "add":
            if len(sys.argv) < 3:
                _usage()
            label = sys.argv[2]
            ticket = add_grant(label)
            print(f"\nZugang fuer '{label}' erstellt und aktiv.")
            print("\nTicket (an die Person weitergeben, in der Android-App eintragen lassen):\n")
            print(ticket)
        elif cmd == "revoke":
            if len(sys.argv) < 3:
                _usage()
            label = sys.argv[2]
            revoke_grant(label)
            print(f"Zugang fuer '{label}' widerrufen. Alle anderen Zugaenge sind davon unberuehrt.")
        elif cmd == "list":
            grants = list_grants()
            if not grants:
                print("Noch keine App-Zugaenge vergeben.")
            for g in grants:
                status = "aktiv" if not g["revoked_at"] else f"widerrufen am {g['revoked_at']}"
                print(f"{g['label']:20s} {g['created_at']:22s} {status}")
        else:
            _usage()
    except TunnelAccessError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        sys.exit(1)


def _usage() -> None:
    print('Nutzung: add "Name" | revoke "Name" | list', file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    _cli()
