# Bot Connect Dashboard

Eigenständiges, viertes Teil des Bot-Ökosystems (neben **Bot**, **Bot Backend**
und **Bot Connect App**): ein winziges lokales Web-Tool, mit dem die Person mit
direktem Zugriff auf den Pi die Iroh-Tunnel-Zugänge (App-Zugänge) der Android-App
erstellen und widerrufen kann — ohne SSH-Copy-Paste, per Knopfdruck.

## Warum ein komplett getrennter Prozess auf einem eigenen Port

**Dieses Dashboard darf NIEMALS über den Iroh-Tunnel oder das Internet erreichbar
sein.** Es läuft deshalb bewusst:

- als **eigener Prozess** auf **Port 5050** (nicht 5000/8500, das ist das
  Haupt-Backend),
- **NUR gebunden an `127.0.0.1`** (`app.run(host="127.0.0.1", ...)` in `app.py`)
  — der Pi nimmt auf diesem Port grundsätzlich keine Verbindungen von anderen
  Geräten im Netzwerk an, egal ob LAN oder Internet,
- **ohne** eigenen `dumbpipe`-Tunnel-Dienst,
- **ohne** nginx-Reverse-Proxy-Eintrag.

**Das ist absichtlich so und darf nicht "der Einfachheit halber" doch getunnelt
werden.** Der Grund: Über dieses Dashboard lassen sich per Knopfdruck neue
Tunnel-Zugänge zum Bot-Backend erstellen — wäre das Dashboard selbst übers
Internet erreichbar, könnte sich potenziell jeder, der drauf zugreift, selbst
einen neuen Zugang zum Backend verschaffen. Die Netzwerk-Isolation (nur
localhost) IST hier die gesamte Zugriffskontrolle — es gibt bewusst **kein**
Login/Passwort-System obendrauf, genau wie an anderer Stelle in diesem Projekt
schon der SSH-Zugriff auf den Pi selbst als vertrauenswürdig vorausgesetzt wird
(siehe `Fernzugriff-Setup-Anleitung.md`, Abschnitt 1.6).

## Zugriff

Nur von einem Gerät mit direktem Zugriff auf den Pi:

- **Lokal auf dem Pi selbst:** `http://127.0.0.1:5050` im Browser.
- **Per SSH-Port-Forwarding von einem anderen Gerät:**
  ```bash
  ssh -L 5050:127.0.0.1:5050 picom@192.168.178.30
  ```
  Danach auf dem eigenen Gerät `http://127.0.0.1:5050` öffnen — der Traffic läuft
  komplett durch den bestehenden, bereits vertrauenswürdigen SSH-Tunnel.

## Architektur

- `app.py` — winzige Flask-App (nutzt die bestehende `venv/` des Hauptprojekts
  mit, keine eigene virtuelle Umgebung nötig, da nur Flask+requests gebraucht
  werden und das schon vorhanden ist). Läuft absichtlich über Flasks eingebauten
  Entwicklungsserver statt waitress+nginx wie das Haupt-Backend — das wäre hier
  unnötiger Aufwand, da der Dienst nie mit echtem Internet-Traffic konfrontiert
  ist, immer nur mit einem einzelnen lokalen Nutzer.
- `templates/index.html` — eine einzige, komplett eigenständige Seite (kein
  Bootstrap/CDN, keine Abhängigkeit vom Haupt-Backend-Design), da dieses Tool
  bewusst unabhängig vom großen Backend bleibt.
- **Kernlogik (Erstellen/Widerrufen der Zugänge) liegt NICHT hier**, sondern in
  `../scripts/tunnel_access_core.py` — dieselbe Logik, die auch
  `../scripts/manage_bot_access.sh` (SSH/CLI-Nutzung) verwendet. Keine doppelte
  Implementierung.
- **Mitgliederliste fürs Dropdown** kommt per HTTP von `127.0.0.1:5052`
  (`backend/internal_app.py`, läuft im Bot-Prozess) — bewusst NICHT von einer
  eigenen Discord-API-Anbindung hier im Dashboard (kein zweiter Bot-Token nötig)
  und bewusst NICHT vom bestehenden Backend-Port 5001 (der ist trotz
  "nur 127.0.0.1" tatsächlich über den Iroh-Tunnel aus dem Internet erreichbar,
  siehe die ausführliche Begründung in `backend/internal_app.py`). Port 5052 hat
  aus genau demselben Grund wie dieses Dashboard selbst KEINEN nginx-Eintrag.
- **Jeder Zugang ist an eine konkrete Discord-User-ID gebunden** (Auswahl per
  Dropdown, nicht mehr freier Text) — Sicherheits-Fix: erst dadurch kann
  `revoke_grant()` beim Widerruf gezielt eine eventuell bestehende Login-Session
  dieser Person im Haupt-Backend mit-invalidieren (`invalidate_user_session()` in
  `bot/database.py`, geprüft bei jedem Request in `backend/app.py`). Ohne diese
  Bindung bliebe ein bereits eingeloggtes Discord-OAuth-Session-Cookie trotz
  entzogenem Tunnel-Zugang technisch weiter gültig.
- `bot-connect-dashboard.service` — Referenz-Unit-Datei zum Einrichten als
  systemd-Dienst (Boot-Autostart), siehe unten.

## Als systemd-Dienst einrichten

```bash
sudo cp bot-connect-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bot-connect-dashboard.service
```

Status prüfen: `sudo systemctl status bot-connect-dashboard.service`

## Verhältnis zur schreibgeschützten Übersichtsseite im Haupt-Backend

Die bestehende Seite "App-Zugänge" im großen, übers Internet erreichbaren
Backend (`is_admin`/Owner) zeigt **dieselben** Daten rein lesend an — bewusst
**ohne** Erstellen/Widerrufen-Funktion, genau aus dem oben genannten Grund.
Erstellen/Widerrufen passiert ausschließlich hier, lokal.
