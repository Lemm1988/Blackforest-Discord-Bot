# BlackforestBot v2

Discord-Bot + Web-Backend für den Blackforest-Discord-Server (Pflanzenquiz, XP/Level,
Achievements, Personality/Mood-System, Automod, Moderationswerkzeuge, u.a.) — plus eine
Android-App und ein lokales Verwaltungs-Dashboard für den Fernzugriff. Bot und Backend
laufen als ein einziger Python-Prozess (discord.py + Flask/waitress).

**Vollständige technische Dokumentation: [`docs/BlackforestBot-Dokumentation.md`](docs/BlackforestBot-Dokumentation.md)**
(Architektur, Befehle, Datenbank, Berechtigungen, Settings, Sicherheit — alles Detail).
Für SSH/Iroh-Tunnel/Fernzugriff-Hintergrund: [`docs/Fernzugriff-Setup-Anleitung.md`](docs/Fernzugriff-Setup-Anleitung.md).

## Struktur

| Ordner | Inhalt |
|---|---|
| `bot/` | Discord-Bot: Cogs, Datenbankschicht, Mood/Events-Logik, Daten |
| `backend/` | Flask-Web-Backend (öffentlich, über Iroh-Tunnel erreichbar) + interner Nur-lokal-Endpunkt (`internal_app.py`, Port 5052) |
| `scripts/` | Betriebs-Skripte: `backup_db.sh`, `manage_bot_access.sh` (CLI für App-Zugänge), `tunnel_access_core.py` (gemeinsame Kernlogik) |
| `android-app/` | Bot Connect App (Kotlin) — Android-App mit eigenem Iroh-Tunnel-Zugang zum Backend, siehe `android-app/README.md` |
| `bot-connect-dashboard/` | Bot Connect Dashboard — lokales Web-Tool (NUR `127.0.0.1:5050`) zur Verwaltung der App-Zugänge, siehe `bot-connect-dashboard/README.md` |
| `docs/` | Vollständige Projekt-Dokumentation (siehe oben) |
| `.github/workflows/` | CI: baut `dumbpipe` für Android + die Debug-APK (muss auf Repo-Root-Ebene liegen, GitHub-Vorgabe) |

## Setup

1. `bot/.env.example` nach `bot/.env` kopieren und ausfüllen (siehe Kommentare darin
   sowie Abschnitt 12 der Dokumentation für den OAuth-Teil).
2. Python-Virtualenv unter `venv/` anlegen, `bot/requirements.txt` installieren.
3. Start: `cd bot && ../venv/bin/python main.py` (oder als systemd-Dienst, siehe Doku
   Abschnitt 13).

## Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

Aktuell **824 Tests**.

Laufen auch in der CI bei jedem Push und Pull Request
(`.github/workflows/tests.yml`). Sie brauchen **keine** Laufzeit-Abhängigkeiten des
Bots, kein systemd und kein `dumbpipe` — alles Privilegierte wird durch Fakes ersetzt.

Der Schwerpunkt liegt bewusst auf Fehlern, die real aufgetreten sind und dabei
**stumm** blieben — das ist der rote Faden dieses Projekts:

* ein Widerruf, der Erfolg meldete, während der Tunnel weiterlief;
* ein Löschen, das den falschen Eintrag traf;
* fest verdrahtete Pfade, die beim Umzug alles lahmlegten;
* zwei Schritte im Android-Build, ohne die die App nie durchkommt;
* ein Befehl, der Discords Drei-Sekunden-Frist riss und deshalb nicht einmal mehr
  seine eigene Fehlermeldung zustellen konnte;
* ein Knopf, der tat, was er sollte, es aber nicht sagte — und dadurch von einem
  kaputten Knopf nicht zu unterscheiden war;
* eine Discord-Rolle und eine Backend-Berechtigung, die auseinanderliefen, ohne
  dass irgendetwas darauf hinwies.

Wer hier etwas ändert, sollte die Tests eher erweitern als anpassen.

## Sicherheit

`.env`, `*.db`, Zertifikate/Schlüssel (`backend/ssl/*.key`/`*.crt`, `*.pem`) und
Backups (`/backups/`, `*.bak_*`) sind über `.gitignore` ausgeschlossen und liegen nie in
diesem Repo.
