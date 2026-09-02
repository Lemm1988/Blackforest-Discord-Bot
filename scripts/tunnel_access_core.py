"""Gemeinsame Kernlogik zur Verwaltung der Iroh-Tunnel-Zugaenge (App-Zugaenge der
Android-App). Bewusst die EINZIGE Implementierung dieser sicherheitsrelevanten
Logik (Secret-Erzeugung, systemd-Dienst-Verwaltung, Ticket-Erzeugung,
Session-Invalidierung) - wird sowohl vom CLI-Wrapper (manage_bot_access.sh, fuer
SSH-Nutzung) als auch vom lokalen Bot Connect Dashboard
(bot-connect-dashboard/app.py, NUR 127.0.0.1, siehe dessen README.md) importiert.
Keine Duplikate dieser Logik pflegen.
"""
from __future__ import annotations

import getpass
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Pfade werden abgeleitet statt hart verdrahtet: Die frueheren Absolutpfade
# (/home/picom/Desktop/BlackforestBot-v2, /home/picom/.config, /home/picom/.cargo)
# brachen beim Umzug auf ein neues OS/Benutzerkonto lautlos - das Dashboard lief,
# konnte aber die DB nicht oeffnen, und neu erzeugte Units hatten einen falschen
# User. Ableiten haelt das unabhaengig von Installationsort und Benutzername.
_REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _REPO_ROOT / "bot" / "data" / "bot.db"
CONFIG_DIR = Path.home() / ".config" / "dumbpipe"
DUMBPIPE_BIN = Path.home() / ".cargo" / "bin" / "dumbpipe"
SYSTEMD_DIR = Path("/etc/systemd/system")
SERVICE_USER = getpass.getuser()


class TunnelAccessError(Exception):
    """Erwartbare, dem Bedienenden anzuzeigende Fehler (leerer Name, Duplikat,
    fehlgeschlagene systemd-Einrichtung) - kein Programmierfehler."""


def _migrate_drop_slug_unique(conn: sqlite3.Connection) -> None:
    """Bugfix: `slug` hatte urspruenglich eine GLOBALE, dauerhafte UNIQUE-Constraint
    (nicht nur "unter aktiven Zugaengen") - dadurch schlug jeder erneute `add` mit
    einem frueher schon einmal genutzten (und laengst widerrufenen) Namen mit einem
    unbehandelten sqlite3.IntegrityError fehl ("kann keine neuen Zugaenge mehr
    anlegen, wenn ich jemanden geloescht habe"). Migriert idempotent auf einen
    normalen (nicht-eindeutigen) Index - Eindeutigkeit wird stattdessen weiterhin
    wie bisher nur unter AKTIVEN Zugaengen erzwungen (siehe add_grant()), History
    bleibt dabei vollstaendig erhalten (gleiches Nachvollziehbarkeits-Prinzip wie
    ueberall in diesem Projekt: nie loeschen, nur markieren).
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tunnel_access_grants'"
    ).fetchone()
    if not row or not row["sql"] or "UNIQUE" not in row["sql"]:
        return  # bereits migriert oder Tabelle existiert noch gar nicht
    conn.execute("ALTER TABLE tunnel_access_grants RENAME TO tunnel_access_grants_old_unique")
    conn.execute(
        """
        CREATE TABLE tunnel_access_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            slug TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT,
            discord_user_id TEXT
        )
        """
    )
    old_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tunnel_access_grants_old_unique)")}
    discord_col = "discord_user_id" if "discord_user_id" in old_cols else "NULL"
    conn.execute(
        f"INSERT INTO tunnel_access_grants (id, label, slug, created_at, revoked_at, discord_user_id) "
        f"SELECT id, label, slug, created_at, revoked_at, {discord_col} FROM tunnel_access_grants_old_unique"
    )
    conn.execute("DROP TABLE tunnel_access_grants_old_unique")
    conn.commit()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent, wie jede Schema-Aenderung in diesem Projekt - falls
    bot/database.py (Bot-Neustart) das Schema noch nicht auf dem aktuellen Stand
    hat (z.B. weil dieses Skript direkt nach einem git pull, aber vor einem
    Bot-Neustart laeuft), legt dieses Modul es defensiv selbst an."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tunnel_access_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            slug TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT
        )
        """
    )
    _migrate_drop_slug_unique(conn)
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(tunnel_access_grants)")}
    if "discord_user_id" not in existing_cols:
        conn.execute("ALTER TABLE tunnel_access_grants ADD COLUMN discord_user_id TEXT")
    if "paused_at" not in existing_cols:
        # Pausieren ist bewusst ein EIGENER Zustand und nicht bloss "Dienst laeuft
        # gerade nicht": Ohne diese Spalte waere ein absichtlich pausierter Zugang
        # nicht von einem defekten zu unterscheiden (Dienst weg, obwohl er laufen
        # sollte) - genau diese Verwechslung gab es real, als ein Zugang in der DB
        # als aktiv stand, aber gar keine Unit-Datei existierte.
        conn.execute("ALTER TABLE tunnel_access_grants ADD COLUMN paused_at TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tunnel_access_grants_slug ON tunnel_access_grants (slug)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_session_invalidations (
            discord_user_id TEXT PRIMARY KEY,
            invalidated_at TEXT NOT NULL
        )
        """
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower())
    return slug.strip("-")


def _run_privileged(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    """Fuehrt einen sudo-Aufruf aus und wirft bei Fehlschlag TunnelAccessError.

    Warum das noetig ist: Das Dashboard laeuft als systemd-Dienst, also ohne TTY.
    Fehlt die passende sudoers-Regel (/etc/sudoers.d/bot-tunnel-access), scheitert
    JEDER sudo-Aufruf sofort - die Meldung landet auf stderr, stdout bleibt leer.
    Frueher wurden diese Aufrufe mit capture_output und ohne check ausgefuehrt,
    ihre Rueckgabewerte also ignoriert. Ein Widerruf loeschte dann Secret und
    DB-Eintrag, liess den laufenden dumbpipe-Prozess aber am Leben - und der haelt
    IROH_SECRET im Speicher, bediente das widerrufene Ticket also unbeirrt weiter,
    waehrend das Dashboard Erfolg meldete.
    """
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"Exit-Code {result.returncode}"
        raise TunnelAccessError(f"{what} fehlgeschlagen: {detail}")
    return result


def _stop_service_reliably(service_name: str) -> bool:
    """`systemctl stop` allein reicht NICHT zuverlaessig aus: dumbpipe stuerzt
    gelegentlich selbststaendig ab (Relay-Verbindungsprobleme, unabhaengig von
    diesem Projekt), und `Restart=always` startet es dann automatisch neu. Trifft
    ein `stop` GENAU in das Zeitfenster zwischen einem Absturz und dem bereits von
    systemd gequeueten automatischen Neustart (RestartSec=5), kann der Dienst
    Sekunden NACH dem `stop` wieder aktiv werden - obwohl `stop` selbst
    erfolgreich war. Deshalb: mehrfach stoppen und mit `is-active` verifizieren,
    bis der Dienst nachweislich dauerhaft inaktiv bleibt, statt dem ersten `stop`
    blind zu vertrauen. Gibt True zurueck, wenn der Dienst am Ende zuverlaessig
    inaktiv ist."""
    for _ in range(5):
        # Wirft, wenn der Aufruf selbst nicht durchgeht (fehlende sudo-Rechte) -
        # frueher wurde das stillschweigend als Erfolg gewertet.
        _run_privileged(["sudo", "systemctl", "stop", service_name], f"Stoppen von {service_name}")
        time.sleep(1)

        # is-active liefert bei NICHT laufendem Dienst absichtlich einen
        # Exit-Code != 0, hier ist also nicht der Rueckgabewert entscheidend,
        # sondern die Ausgabe. ENTSCHEIDEND: eine LEERE Ausgabe bedeutet, dass der
        # Befehl gar nicht erst gelaufen ist (z.B. sudo verweigert) - das darf
        # NICHT als "nicht aktiv" durchgehen. Genau diese Verwechslung war die
        # Ursache dafuer, dass ein Widerruf Erfolg meldete, ohne den Tunnel zu
        # beenden. Deshalb: nur eine ausdrueckliche Statusmeldung zaehlt.
        result = subprocess.run(
            ["sudo", "systemctl", "is-active", service_name], capture_output=True, text=True
        )
        status = result.stdout.strip()
        if not status:
            detail = (result.stderr or "").strip() or f"Exit-Code {result.returncode}"
            raise TunnelAccessError(
                f"Status von {service_name} nicht feststellbar (keine Ausgabe von "
                f"'systemctl is-active'): {detail}"
            )
        if status != "active":
            return True
    return False


def service_state(slug: str) -> str:
    """Liefert den tatsaechlichen systemd-Zustand ('active', 'inactive', ...).
    Leere Ausgabe (z.B. verweigertes sudo) wird als 'unbekannt' gemeldet, nie als
    'nicht aktiv' - dieselbe Vorsicht wie in _stop_service_reliably()."""
    result = subprocess.run(
        ["sudo", "systemctl", "is-active", f"dumbpipe-discordbot-{slug}.service"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() or "unbekannt"


def _unit_target(slug: str) -> str | None:
    """Liest aus der Unit-Datei aus, wohin der Tunnel tatsaechlich weiterleitet.
    Nicht geraten, sondern aus ExecStart gelesen - so faellt auch auf, wenn eine
    von Hand veraenderte Unit woanders hinzeigt als erwartet."""
    unit_file = SYSTEMD_DIR / f"dumbpipe-discordbot-{slug}.service"
    if not unit_file.exists():
        return None
    try:
        for line in unit_file.read_text().splitlines():
            if line.startswith("ExecStart=") and "--host" in line:
                parts = line.split()
                return parts[parts.index("--host") + 1]
    except (OSError, ValueError, IndexError):
        return None
    return None


def list_grants() -> list[dict]:
    """Listet die Zugaenge MIT Abgleich gegen die Wirklichkeit.

    Der DB-Eintrag allein sagt nur, was gewollt ist - nicht, was tatsaechlich
    laeuft. Beide Richtungen sind real vorgekommen: ein als 'aktiv' gefuehrter
    Zugang ohne jede Unit-Datei (die Person kam nicht durch), und ein als
    'widerrufen' gefuehrter Zugang, dessen Tunnel weiterlief (der Widerruf war
    wirkungslos, siehe Abschnitt 9.2 der Fernzugriff-Anleitung). Deshalb wird hier
    zusaetzlich geprueft, ob Secret, Unit-Datei und Dienst wirklich existieren, und
    daraus ein Status abgeleitet, der Abweichungen benennt statt sie zu verbergen.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, label, slug, discord_user_id, created_at, revoked_at, paused_at "
            "FROM tunnel_access_grants ORDER BY created_at DESC"
        ).fetchall()

    # Slugs, zu denen es einen aktuell gueltigen (nicht widerrufenen) Zugang gibt.
    # Wichtig, weil Dienst- und Dateinamen nur vom Slug abhaengen: Wurde ein Zugang
    # widerrufen und fuer dieselbe Person neu angelegt, gehoeren Dienst, Unit-Datei
    # und Secret zum NEUEN Eintrag. Die alten Zeilen sind blosse Historie und
    # duerfen nicht so aussehen, als liefe ihr Tunnel noch - sonst schlaegt das
    # Dashboard bei jedem regulaeren Neusetzen falschen Alarm.
    live_slugs = {r["slug"] for r in rows if not r["revoked_at"]}

    # systemd nur einmal pro Slug fragen statt einmal pro Zeile.
    state_cache: dict[str, str] = {}

    grants = []
    for row in rows:
        g = dict(row)
        slug = g["slug"]
        superseded = bool(g["revoked_at"]) and slug in live_slugs
        g["superseded"] = superseded

        if superseded:
            # Historische Zeile: Die Dateien auf der Platte gehoeren dem neuen
            # Eintrag, ueber diesen hier sagen sie nichts aus.
            g["secret_exists"] = g["unit_exists"] = False
            g["service_state"] = "n/a"
            g["target"] = None
            g["status"] = "ersetzt"
            grants.append(g)
            continue

        if slug not in state_cache:
            state_cache[slug] = service_state(slug)
        g["secret_exists"] = (CONFIG_DIR / f"env-person-{slug}").exists()
        g["unit_exists"] = (SYSTEMD_DIR / f"dumbpipe-discordbot-{slug}.service").exists()
        g["service_state"] = state_cache[slug]
        g["target"] = _unit_target(slug)

        running = g["service_state"] == "active"
        if g["revoked_at"]:
            # Der gefaehrlichste Fall: widerrufen, aber es laeuft noch etwas.
            g["status"] = "GEFAHR" if running else "widerrufen"
        elif g["paused_at"]:
            g["status"] = "FEHLER" if running else "pausiert"
        elif running:
            g["status"] = "aktiv"
        else:
            g["status"] = "DEFEKT"
        grants.append(g)
    return grants


def _grant_row(slug: str, require_active: bool = False) -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT label, slug, discord_user_id, revoked_at, paused_at "
            "FROM tunnel_access_grants WHERE slug = ? ORDER BY created_at DESC LIMIT 1",
            (slug,),
        ).fetchone()
    if not row:
        raise TunnelAccessError(f"Kein Zugang mit dem Namen '{slug}' gefunden.")
    if require_active and row["revoked_at"]:
        raise TunnelAccessError(
            f"Zugang '{row['label']}' ist bereits widerrufen - ein widerrufener Zugang "
            "laesst sich nicht pausieren oder fortsetzen, nur neu anlegen."
        )
    return dict(row)


def show_ticket(label: str) -> str:
    """Erzeugt das Ticket eines bestehenden Zugangs erneut aus seinem Secret.

    Das Ticket leitet sich deterministisch aus IROH_SECRET ab, ist also jederzeit
    reproduzierbar - es wird nirgends gespeichert. Bewusste Abwaegung: Ein Ticket
    ist ein Bearer-Credential, frueher galt "wird nur einmal angezeigt". Da das
    Dashboard ausschliesslich auf 127.0.0.1 lauscht und ohnehin das Werkzeug ist,
    mit dem Zugaenge vergeben werden, ueberwiegt hier der praktische Nutzen:
    nachvollziehen zu koennen, welches Ticket zu welchem Zugang gehoert, ohne
    dafuer ein neues ausstellen und verteilen zu muessen.
    """
    row = _grant_row(slugify(label))
    if row["revoked_at"]:
        raise TunnelAccessError(
            f"Zugang '{row['label']}' ist widerrufen - sein Ticket ist ungueltig und "
            "wird deshalb nicht mehr angezeigt."
        )
    secret_file = CONFIG_DIR / f"env-person-{row['slug']}"
    if not secret_file.exists():
        raise TunnelAccessError(f"Secret-Datei fehlt: {secret_file}")

    secret = ""
    for line in secret_file.read_text().splitlines():
        if line.startswith("IROH_SECRET="):
            secret = line.split("=", 1)[1].strip()
    if not secret:
        raise TunnelAccessError(f"Kein IROH_SECRET in {secret_file} gefunden.")

    try:
        return subprocess.run(
            [str(DUMBPIPE_BIN), "generate-ticket"],
            env={**os.environ, "IROH_SECRET": secret},
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as e:
        raise TunnelAccessError(f"Ticket-Erzeugung fehlgeschlagen: {e.stderr}") from e


def pause_grant(label: str) -> None:
    """Haelt den Tunnel an, ohne Secret oder Ticket zu vernichten.

    Unterschied zum Widerruf: Das Ticket bleibt gueltig und funktioniert nach dem
    Fortsetzen unveraendert weiter - es muss also nichts neu verteilt werden. Fuer
    einen dauerhaften Entzug ist trotzdem revoke_grant() das richtige Mittel, denn
    nur dort wird das Secret geloescht und die Login-Session invalidiert.
    """
    row = _grant_row(slugify(label), require_active=True)
    slug, service_name = row["slug"], f"dumbpipe-discordbot-{row['slug']}.service"
    if row["paused_at"]:
        raise TunnelAccessError(f"Zugang '{row['label']}' ist bereits pausiert.")

    if not _stop_service_reliably(service_name):
        raise TunnelAccessError(f"Dienst '{service_name}' liess sich nicht stoppen.")
    # disable, damit er einen Neustart des Pi nicht ueberlebt - sonst waere die
    # Pause nach dem naechsten Boot stillschweigend wieder aufgehoben.
    _run_privileged(["sudo", "systemctl", "disable", service_name], f"Deaktivieren von {service_name}")

    with _connect() as conn:
        conn.execute(
            "UPDATE tunnel_access_grants SET paused_at = ? WHERE slug = ? AND revoked_at IS NULL",
            (_now(), slug),
        )
        conn.commit()


def resume_grant(label: str) -> None:
    """Nimmt einen pausierten Zugang wieder in Betrieb - gleiches Ticket wie zuvor."""
    row = _grant_row(slugify(label), require_active=True)
    slug, service_name = row["slug"], f"dumbpipe-discordbot-{row['slug']}.service"
    if not row["paused_at"]:
        raise TunnelAccessError(f"Zugang '{row['label']}' ist nicht pausiert.")
    if not (SYSTEMD_DIR / service_name).exists():
        raise TunnelAccessError(
            f"Unit-Datei fuer '{row['label']}' fehlt - der Zugang laesst sich nicht "
            "fortsetzen, bitte neu setzen."
        )

    _run_privileged(["sudo", "systemctl", "enable", "--now", service_name], f"Starten von {service_name}")
    time.sleep(1)
    if service_state(slug) != "active":
        raise TunnelAccessError(
            f"Dienst '{service_name}' laeuft nach dem Fortsetzen nicht - bitte pruefen: "
            f"journalctl -u {service_name}"
        )

    with _connect() as conn:
        conn.execute(
            "UPDATE tunnel_access_grants SET paused_at = NULL WHERE slug = ? AND revoked_at IS NULL",
            (slug,),
        )
        conn.commit()


def delete_grant(grant_id: int) -> None:
    """Entfernt GENAU EINEN Eintrag anhand seiner Zeilen-ID.

    Bewusst ueber die ID und nicht ueber den Namen: Nach einem Widerruf mit
    anschliessendem Neuanlegen existieren mehrere Zeilen mit demselben Slug. Ein
    Loeschen "per Name" wuerde die noch aktive Zeile mitreissen - beim Aufraeumen
    alter Eintraege waere das ein unbeabsichtigter Zugangsentzug.

    Ist der Eintrag noch aktiv (nicht widerrufen), wird zuerst regulaer widerrufen -
    sonst bliebe ein laufender Tunnel zurueck, den danach kein Eintrag mehr sichtbar
    macht und den folglich niemand mehr abschalten koennte.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, label, slug, revoked_at FROM tunnel_access_grants WHERE id = ?",
            (grant_id,),
        ).fetchone()
    if not row:
        raise TunnelAccessError(f"Kein Eintrag mit der ID {grant_id} gefunden.")

    if not row["revoked_at"]:
        # revoke_grant() arbeitet ueber den Namen, trifft dabei aber ausdruecklich
        # nur die nicht-widerrufene Zeile - und genau diese ist es hier.
        revoke_grant(row["label"])

    with _connect() as conn:
        conn.execute("DELETE FROM tunnel_access_grants WHERE id = ?", (grant_id,))
        conn.commit()


def reset_grant(label: str) -> str:
    """Widerruft den bestehenden Zugang und legt ihn fuer dieselbe Person neu an.

    Ergebnis ist zwingend ein NEUES Ticket: Das alte Secret wird beim Widerruf
    geloescht, ein neues zufaellig erzeugt. Ein Wiederbeleben des alten Tickets
    waere ein Widerspruch in sich - dann waere ein Widerruf nur eine Pause.
    """
    row = _grant_row(slugify(label))
    if not row["discord_user_id"]:
        raise TunnelAccessError(
            f"Zugang '{row['label']}' hat keine hinterlegte Discord-User-ID und kann "
            "deshalb nicht automatisch neu gesetzt werden - bitte neu anlegen."
        )
    if not row["revoked_at"]:
        revoke_grant(row["label"])
    return add_grant(row["label"], row["discord_user_id"])


def add_grant(label: str, discord_user_id: str) -> str:
    """Legt einen neuen, eigenstaendigen dumbpipe-Dienst (eigenes Secret, eigener
    systemd-Dienst, alle leiten zu localhost:5000 weiter) an, gebunden an eine
    konkrete Discord-User-ID (Sicherheits-Fix: nur so kann revoke_grant() gezielt
    eine bestehende Backend-Login-Session dieser Person mit-invalidieren), und
    gibt das frisch erzeugte Ticket zurueck."""
    label = (label or "").strip()
    discord_user_id = (discord_user_id or "").strip()
    if not label:
        raise TunnelAccessError("Name/Label darf nicht leer sein.")
    if not discord_user_id or not discord_user_id.isdigit():
        raise TunnelAccessError("Es wird eine gueltige Discord-User-ID (nur Ziffern) benoetigt.")
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
After=network-online.target nginx.service
Wants=network-online.target

[Service]
Type=simple
User={SERVICE_USER}
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

    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO tunnel_access_grants (label, slug, discord_user_id, created_at, revoked_at) "
                "VALUES (?, ?, ?, ?, NULL)",
                (label, slug, discord_user_id, _now()),
            )
            conn.commit()
    except Exception as e:
        # Rollback: ohne DB-Eintrag waere der Dienst eine "Leiche" - nicht in
        # list_grants() sichtbar, nie ueber revoke_grant() auffindbar/widerrufbar.
        # Lieber den bereits gestarteten Dienst wieder einreissen und einen klaren
        # Fehler melden, als einen unsichtbaren, dauerhaft laufenden Zugang zu
        # hinterlassen (genau das ist einmal beim Reproduzieren dieses Bugs passiert).
        # Der Rollback darf nicht selbst still scheitern - sonst bliebe genau die
        # unsichtbare Leiche zurueck, die er verhindern soll. Ein Fehlschlag wird
        # deshalb eingesammelt und der Meldung angehaengt, statt den urspruenglichen
        # Fehler zu ueberschreiben oder zu verschlucken.
        rollback_problem = None
        try:
            _stop_service_reliably(service_name)
            _run_privileged(["sudo", "systemctl", "disable", service_name], "Deaktivieren")
            _run_privileged(["sudo", "rm", "-f", str(unit_file)], "Entfernen der Unit-Datei")
            _run_privileged(["sudo", "systemctl", "daemon-reload"], "systemctl daemon-reload")
        except Exception as cleanup_error:
            rollback_problem = cleanup_error
        secret_file.unlink(missing_ok=True)

        if rollback_problem is not None:
            raise TunnelAccessError(
                f"Datenbank-Eintrag fehlgeschlagen ({e}) UND der Rollback schlug ebenfalls fehl "
                f"({rollback_problem}). ACHTUNG: Der Dienst '{service_name}' laeuft moeglicherweise "
                "noch und waere dann ein nicht im Dashboard sichtbarer, offener Zugang - bitte "
                "sofort manuell pruefen: systemctl status " + service_name
            ) from e
        raise TunnelAccessError(f"Datenbank-Eintrag fehlgeschlagen, Dienst wieder zurueckgerollt: {e}") from e

    return ticket


def revoke_grant(label: str) -> None:
    """Stoppt+deaktiviert NUR den zu diesem Namen gehoerenden Dienst, loescht dessen
    Secret, markiert den Eintrag als widerrufen (nicht loeschen - Nachvollziehbarkeit,
    gleiches Prinzip wie beim Audit-Log). Alle anderen Zugaenge bleiben unberuehrt.

    Sicherheits-Fix: invalidiert zusaetzlich eine eventuell bestehende
    Backend-Login-Session der verknuepften Discord-User-ID (siehe
    database.invalidate_user_session() / app.py _enforce_session_invalidation) -
    sonst bliebe ein bereits eingeloggtes Session-Cookie trotz entzogenem
    Tunnel-Zugang technisch weiter gueltig."""
    label = (label or "").strip()
    if not label:
        raise TunnelAccessError("Name/Label darf nicht leer sein.")
    slug = slugify(label)

    with _connect() as conn:
        row = conn.execute(
            "SELECT discord_user_id FROM tunnel_access_grants WHERE slug = ? AND revoked_at IS NULL",
            (slug,),
        ).fetchone()
        if not row:
            raise TunnelAccessError(f"Kein aktiver Zugang mit dem Namen '{label}' gefunden.")
        discord_user_id = row["discord_user_id"]

    service_name = f"dumbpipe-discordbot-{slug}.service"
    unit_file = SYSTEMD_DIR / service_name
    secret_file = CONFIG_DIR / f"env-person-{slug}"

    if not _stop_service_reliably(service_name):
        raise TunnelAccessError(
            f"Dienst '{service_name}' liess sich nicht zuverlaessig dauerhaft stoppen "
            "(vermutlich Absturz-Neustart-Schleife von dumbpipe selbst). Widerruf wurde "
            "NICHT durchgefuehrt, um keine Reste zu hinterlassen - bitte kurz erneut "
            "versuchen oder den Dienst manuell pruefen (journalctl -u " + service_name + ")."
        )
    _run_privileged(["sudo", "systemctl", "disable", service_name], f"Deaktivieren von {service_name}")
    _run_privileged(["sudo", "rm", "-f", str(unit_file)], f"Entfernen von {unit_file}")
    _run_privileged(["sudo", "systemctl", "daemon-reload"], "systemctl daemon-reload")

    # Abschlusskontrolle, bevor der Zugang in der DB als widerrufen gilt: Erst
    # wenn Unit-Datei UND laufender Dienst nachweislich weg sind, ist der
    # Netzwerkzugang wirklich entzogen. Der laufende dumbpipe-Prozess ist der
    # EINZIGE wirksame Hebel - alle Personen-Tunnel leiten auf dasselbe
    # localhost:5000, das Backend kann also gar nicht unterscheiden, durch wessen
    # Tunnel eine Anfrage kam (alles kommt als 127.0.0.1 an).
    if unit_file.exists():
        raise TunnelAccessError(
            f"Unit-Datei {unit_file} existiert nach dem Entfernen weiterhin - Widerruf "
            "unvollstaendig, Zugang ist moeglicherweise noch offen."
        )
    check = subprocess.run(
        ["sudo", "systemctl", "is-active", service_name], capture_output=True, text=True
    )
    if check.stdout.strip() == "active":
        raise TunnelAccessError(
            f"Dienst {service_name} laeuft nach dem Widerruf immer noch - Zugang ist "
            "noch offen, bitte manuell pruefen."
        )

    secret_file.unlink(missing_ok=True)

    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE tunnel_access_grants SET revoked_at = ? WHERE slug = ? AND revoked_at IS NULL",
            (now, slug),
        )
        if discord_user_id:
            conn.execute(
                "INSERT INTO user_session_invalidations (discord_user_id, invalidated_at) VALUES (?, ?) "
                "ON CONFLICT(discord_user_id) DO UPDATE SET invalidated_at = excluded.invalidated_at",
                (discord_user_id, now),
            )
        conn.commit()


# --- CLI-Einstiegspunkt, genutzt vom duennen manage_bot_access.sh-Wrapper ---

def _cli() -> None:
    if len(sys.argv) < 2:
        _usage()
    cmd = sys.argv[1]
    try:
        if cmd == "add":
            if len(sys.argv) < 4:
                _usage()
            label, discord_user_id = sys.argv[2], sys.argv[3]
            ticket = add_grant(label, discord_user_id)
            print(f"\nZugang fuer '{label}' (Discord-ID {discord_user_id}) erstellt und aktiv.")
            print("\nTicket (an die Person weitergeben, in der Android-App eintragen lassen):\n")
            print(ticket)
        elif cmd == "revoke":
            if len(sys.argv) < 3:
                _usage()
            label = sys.argv[2]
            revoke_grant(label)
            print(f"Zugang fuer '{label}' widerrufen. Eine eventuell bestehende Backend-Login-Session")
            print("dieser Person wurde ebenfalls invalidiert. Alle anderen Zugaenge sind unberuehrt.")
        elif cmd == "list":
            grants = list_grants()
            if not grants:
                print("Noch keine App-Zugaenge vergeben.")
            for g in grants:
                status = "aktiv" if not g["revoked_at"] else f"widerrufen am {g['revoked_at']}"
                uid = g["discord_user_id"] or "—"
                print(f"{g['label']:20s} {uid:20s} {g['created_at']:22s} {status}")
        else:
            _usage()
    except TunnelAccessError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        sys.exit(1)


def _usage() -> None:
    print('Nutzung: add "Name" <discord_user_id> | revoke "Name" | list', file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    _cli()
