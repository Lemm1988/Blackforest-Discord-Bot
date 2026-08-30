# BlackforestBot v2

Discord-Bot + Web-Backend fuer den Blackforest-Discord-Server (Pflanzenquiz, XP/Level,
Achievements, Personality/Mood-System, Automod, Moderationswerkzeuge, u.a.). Bot und
Backend laufen als ein einziger Python-Prozess (discord.py + Flask/waitress), siehe
`BlackforestBot-Dokumentation.md` (liegt auf dem Pi unter `~/Desktop/`, nicht Teil dieses
Repos) fuer die vollstaendige technische Dokumentation.

## Struktur

| Ordner | Inhalt |
|---|---|
| `bot/` | Discord-Bot: Cogs, Datenbankschicht, Mood/Events-Logik, Daten |
| `backend/` | Flask-Web-Backend: Routen, Templates, statische Assets |
| `scripts/` | Betriebs-Skripte (z.B. `backup_db.sh`) |
| `android-app/` | Cross-Compile-Pipeline (GitHub Actions) fuer eine kuenftige Android-App mit Iroh-Tunnel-Zugang zum Backend, siehe `android-app/README.md` |
| `.github/workflows/` | CI: Android-Cross-Compile-Build (muss auf Repo-Root-Ebene liegen, GitHub-Vorgabe) |

## Setup

1. `bot/.env.example` nach `bot/.env` kopieren und ausfuellen (siehe Kommentare darin
   sowie Abschnitt 9 der Bot-Dokumentation fuer den OAuth-Teil).
2. Python-Virtualenv unter `venv/` anlegen, `bot/requirements.txt` installieren.
3. Start: `cd bot && ../venv/bin/python main.py` (oder als systemd-Dienst, siehe Doku
   Abschnitt 10).

## Sicherheit

`.env`, `*.db`, Zertifikate/Schluessel (`backend/ssl/*.key`/`*.crt`, `*.pem`) und
Backups (`/backups/`, `*.bak_*`) sind ueber `.gitignore` ausgeschlossen und liegen nie in
diesem Repo.
