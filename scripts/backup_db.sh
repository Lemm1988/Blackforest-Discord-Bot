#!/bin/bash
# Tägliches Backup von bot.db (SQLite Online-Backup, konsistent auch bei
# laufendem Bot-Prozess mit offener Schreibverbindung) + .env auf den
# USB-Stick unter /var/lib/influxdb (NICHT die SD-Karte).
#
# Rotation: Backups älter als RETENTION_DAYS werden automatisch gelöscht,
# damit der Stick nicht unbegrenzt vollläuft.
set -euo pipefail

DB_PATH="/home/picom/Desktop/BlackforestBot-v2/bot/data/bot.db"
ENV_PATH="/home/picom/Desktop/BlackforestBot-v2/bot/.env"
BACKUP_DIR="/var/lib/influxdb/blackforestbot-backups"
RETENTION_DAYS=14
TS=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# SQLite Online-Backup-API via ".backup": liest die DB über eine eigene,
# konsistente Snapshot-Verbindung aus - anders als ein einfaches "cp", das
# theoretisch mitten in einer laufenden Transaktion des Bot-Prozesses kopieren
# könnte und dann eine beschädigte/inkonsistente Kopie erzeugen würde.
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/bot_db_$TS.db'"

# .env separat sichern (enthält Discord-Token/Secrets) - Inhalt wird an keiner
# Stelle dieses Skripts ausgegeben oder geloggt, nur die Datei kopiert.
cp "$ENV_PATH" "$BACKUP_DIR/env_$TS"

chmod 600 "$BACKUP_DIR/bot_db_$TS.db" "$BACKUP_DIR/env_$TS"

# Rotation: nur die eigenen, klar benannten Backup-Dateien anfassen.
find "$BACKUP_DIR" -maxdepth 1 -name "bot_db_*.db" -mtime "+$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -name "env_*" -mtime "+$RETENTION_DAYS" -delete

echo "Backup abgeschlossen: $BACKUP_DIR/bot_db_$TS.db"
