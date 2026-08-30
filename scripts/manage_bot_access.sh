#!/bin/bash
# Verwaltung einzelner Iroh-Tunnel-Zugaenge fuer die Android-App: jede Person
# bekommt ein EIGENES dumbpipe-Secret + einen eigenen systemd-Dienst (alle leiten
# zu localhost:5000 weiter, wie der bestehende dumbpipe-discordbot.service) statt
# eines einzigen geteilten Tickets - Widerruf trifft dadurch nur die eine Person,
# alle anderen Zugaenge bleiben unberuehrt. Laeuft manuell per SSH, NICHT vom
# Web-Backend aus aufrufbar (das Backend darf keine systemd-Dienste anlegen/loeschen).
#
# Nutzung:
#   manage_bot_access.sh add "Name"
#   manage_bot_access.sh revoke "Name"
#   manage_bot_access.sh list
set -euo pipefail

DB_PATH="/home/picom/Desktop/BlackforestBot-v2/bot/data/bot.db"
CONFIG_DIR="/home/picom/.config/dumbpipe"
DUMBPIPE_BIN="/home/picom/.cargo/bin/dumbpipe"

# Idempotent, wie jede Schema-Aenderung in diesem Projekt - falls bot/database.py
# (Bot-Neustart) die Tabelle noch nicht angelegt hat, macht das Skript es selbst.
sqlite3 "$DB_PATH" "CREATE TABLE IF NOT EXISTS tunnel_access_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);"

usage() {
    echo "Nutzung: $0 add \"Name\" | revoke \"Name\" | list"
    exit 1
}

slugify() {
    echo "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-*//;s/-*$//'
}

sql_escape() {
    echo "$1" | sed "s/'/''/g"
}

cmd_add() {
    local label="${1:-}"
    [ -z "$label" ] && { echo "Fehler: Name/Label fehlt."; usage; }

    local slug
    slug=$(slugify "$label")
    [ -z "$slug" ] && { echo "Fehler: Name ergibt einen leeren Slug (nur Sonderzeichen?)."; exit 1; }

    local existing
    existing=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM tunnel_access_grants WHERE slug = '$slug' AND revoked_at IS NULL;")
    if [ "$existing" != "0" ]; then
        echo "Fehler: Ein AKTIVER Zugang mit dem Namen '$label' (Slug '$slug') existiert bereits."
        exit 1
    fi

    local secret_file="$CONFIG_DIR/env-person-$slug"
    local service_name="dumbpipe-discordbot-$slug.service"
    local unit_file="/etc/systemd/system/$service_name"

    if [ -f "$secret_file" ] || [ -f "$unit_file" ]; then
        echo "Fehler: Es gibt bereits Reste eines Zugangs mit Slug '$slug' (Secret-Datei oder"
        echo "Dienst-Datei existiert noch, evtl. von einem frueher widerrufenen, gleichnamigen"
        echo "Zugang). Bitte einen anderen Namen waehlen oder manuell aufraeumen."
        exit 1
    fi

    echo "Erzeuge neuen Iroh-Tunnel-Zugang fuer '$label' (Slug: $slug)..."

    local secret
    secret=$(openssl rand -hex 32)
    echo "IROH_SECRET=$secret" > "$secret_file"
    chmod 600 "$secret_file"

    sudo tee "$unit_file" > /dev/null << EOF
[Unit]
Description=dumbpipe iroh tunnel for Discord bot backend - person: $label
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=picom
EnvironmentFile=$secret_file
ExecStart=$DUMBPIPE_BIN listen-tcp --host localhost:5000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable --now "$service_name" > /dev/null

    # Kurz warten, bis der Dienst wirklich laeuft, bevor wir das Ticket abfragen.
    sleep 1

    local ticket
    ticket=$(
        set -a
        # shellcheck disable=SC1090
        source "$secret_file"
        set +a
        "$DUMBPIPE_BIN" generate-ticket
    )

    local now label_escaped
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    label_escaped=$(sql_escape "$label")
    sqlite3 "$DB_PATH" "INSERT INTO tunnel_access_grants (label, slug, created_at, revoked_at) VALUES ('$label_escaped', '$slug', '$now', NULL);"

    echo ""
    echo "=================================================================="
    echo "Zugang fuer '$label' erstellt und aktiv (Dienst: $service_name)."
    echo ""
    echo "Ticket (an die Person weitergeben, in der Android-App unter"
    echo "'Ticket aendern' eintragen lassen):"
    echo ""
    echo "$ticket"
    echo "=================================================================="
}

cmd_revoke() {
    local label="${1:-}"
    [ -z "$label" ] && { echo "Fehler: Name/Label fehlt."; usage; }

    local slug
    slug=$(slugify "$label")

    local found
    found=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM tunnel_access_grants WHERE slug = '$slug' AND revoked_at IS NULL;")
    if [ "$found" = "0" ]; then
        echo "Fehler: Kein AKTIVER Zugang mit dem Namen '$label' (Slug '$slug') gefunden."
        echo "(Namen sind case-insensitive und werden normalisiert - siehe 'list' fuer die genauen Eintraege.)"
        exit 1
    fi

    local service_name="dumbpipe-discordbot-$slug.service"
    local secret_file="$CONFIG_DIR/env-person-$slug"

    echo "Widerrufe Zugang fuer '$label' (Slug: $slug)..."

    sudo systemctl stop "$service_name" 2>/dev/null || true
    sudo systemctl disable "$service_name" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/$service_name"
    sudo systemctl daemon-reload
    rm -f "$secret_file"

    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    sqlite3 "$DB_PATH" "UPDATE tunnel_access_grants SET revoked_at = '$now' WHERE slug = '$slug' AND revoked_at IS NULL;"

    echo "Erledigt: Dienst gestoppt+deaktiviert, Secret geloescht, Eintrag als widerrufen markiert."
    echo "Das bisherige Ticket dieser Person funktioniert ab sofort nicht mehr. Alle anderen"
    echo "Zugaenge sind davon unberuehrt."
}

cmd_list() {
    echo "Aktuelle App-Zugaenge:"
    echo ""
    sqlite3 -header -column "$DB_PATH" \
        "SELECT label AS Name, created_at AS 'Erstellt am',
                CASE WHEN revoked_at IS NULL THEN 'aktiv' ELSE 'widerrufen am ' || revoked_at END AS Status
         FROM tunnel_access_grants
         ORDER BY created_at DESC;"
}

case "${1:-}" in
    add) cmd_add "${2:-}" ;;
    revoke) cmd_revoke "${2:-}" ;;
    list) cmd_list ;;
    *) usage ;;
esac
