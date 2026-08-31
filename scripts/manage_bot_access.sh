#!/bin/bash
# Duenner CLI-Wrapper um die gemeinsame Kernlogik in tunnel_access_core.py.
# Die eigentliche Logik (Secret-Erzeugung, systemd-Dienst-Verwaltung,
# Ticket-Erzeugung) lebt NUR dort - wird auch vom lokalen Bot Connect Dashboard
# (bot-connect-dashboard/) genutzt, keine doppelte Implementierung mehr.
#
# Nutzung:
#   manage_bot_access.sh add "Name"
#   manage_bot_access.sh revoke "Name"
#   manage_bot_access.sh list
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/../venv/bin/python" "$DIR/tunnel_access_core.py" "$@"
