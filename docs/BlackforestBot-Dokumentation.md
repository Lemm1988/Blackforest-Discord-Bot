# BlackforestBot v2 — Dokumentation

Diese Datei beschreibt den Discord-Bot, sein Web-Backend, die Android-App und das lokale
Verwaltungs-Dashboard — das komplette "Bot-Ökosystem". Alles rund um SSH-Zugriff und den
Iroh/dumbpipe-Fernzugriffstunnel im Detail steht separat in
`Fernzugriff-Setup-Anleitung.md` (Pi: `~/Desktop/`, GitHub: `docs/`).

Stand: 31.08.2026. Projektordner: `~/Desktop/BlackforestBot-v2/` (Pi), Spiegel im
GitHub-Repo `github.com/Lemm1988/Blackforest-Discord-Bot` (privat, `main`-Branch ist die
Quelle der Wahrheit für den Code seit der Umstellung auf Git-Versionsverlauf).

---

## Inhaltsverzeichnis

1. Architektur-Überblick (Bot-Ökosystem, vier Teile)
2. Vollständige Befehlsliste (alle 8 Cogs)
3. Backend-Routen (`backend/app.py` + `backend/internal_app.py`)
4. Komplettes Datenbankschema (25 Tabellen)
5. Berechtigungssystem (Owner, `is_admin`, 9 Flags, Basis-Rechte)
6. Vollständige Settings-Referenz (74 Einstellungen)
7. Menü-/Modul-Struktur im Backend
8. Sicherheitsfundament (Owner-Schutz, Audit-Log, Backup, Session-Invalidierung)
9. Personality/Mood/Events-System
10. Bot Connect App (Android) + Bot Connect Dashboard
11. OAuth-Setup
12. Start / Stop / Logs
13. Umgesetzte Sicherheitsmaßnahmen
14. Unterschiede zum alten Bot
15. Den Bot erweitern
16. Testsuite (`tests/`, 478 Tests)
17. Offene Punkte (Stand 03.09.2026)

---

## 1. Architektur-Überblick

Das Projekt besteht aus **vier eigenständigen Teilen** ("Bot-Ökosystem"):

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. BOT + BOT BACKEND — ein einziger Python-Prozess (bot/main.py)     │
│                                                                        │
│    discord.py-Bot (Hauptthread)                                       │
│    ├── 8 Cogs: quiz, plants, fun, moderation, reactions, giveaway,    │
│    │           admin, personality                                     │
│    ├── Flask-Backend-Thread (Waitress, 127.0.0.1:5001)                │
│    │   -> nginx (127.0.0.1:5000, TLS) -> Iroh-Tunnel -> Internet      │
│    └── Interner Mitglieder-Endpunkt-Thread (Waitress, 127.0.0.1:5052) │
│        -> NUR lokal, KEIN nginx/Tunnel-Eintrag                        │
└─────────────────────────────────────────────────────────────────────┘

┌───────────────────────────┐   ┌──────────────────────────────────────┐
│ 3. BOT CONNECT APP         │   │ 4. BOT CONNECT DASHBOARD                │
│    (Android, android-app/) │   │    (bot-connect-dashboard/)             │
│    Eigener dumbpipe-Tunnel │   │    Eigener Flask-Prozess, NUR           │
│    pro Person, WebView auf │   │    127.0.0.1:5050, verwaltet App-       │
│    das Backend             │   │    Zugänge (erstellen/widerrufen)       │
└───────────────────────────┘   └──────────────────────────────────────┘
```

**Warum Bot und Backend EIN Prozess sind:** Der alte Bot hatte ein eigenes, internes
Mini-Flask-API auf Port 5001, nur damit das separate Backend dem Bot per HTTP-Call sagen
konnte "poste dieses Embed". Mit `asyncio.run_coroutine_threadsafe` kann das Backend die
Bot-Funktionen direkt im selben Speicherraum aufrufen — kein internes HTTP-API mehr nötig.

**Web-Anfrage-Pfad (öffentliches Backend):**

```
Browser (über Iroh-Tunnel) → nginx (127.0.0.1:5000, TLS-Terminierung)
                            → waitress (127.0.0.1:5001, reines HTTP, intern)
                            → Flask-App (WSGI)
```

**Wichtige Erkenntnis (31.08.2026):** Port 5001 ist trotz "nur 127.0.0.1"-Bindung
tatsächlich übers Internet erreichbar, weil der dumbpipe-Tunnel selbst auf Loopback
terminiert, bevor nginx ins Spiel kommt — für nginx sehen Tunnel-Traffic und rein lokaler
Traffic identisch aus. Deshalb bekam der interne Mitglieder-Endpunkt (fürs Dashboard)
einen komplett separaten Port **5052**, für den nginx buchstäblich keinen Eintrag besitzt
— das ist die einzige Konstruktion, die echte Netzwerk-Isolation garantiert. Siehe
Abschnitt 10 und `Fernzugriff-Setup-Anleitung.md` Abschnitt 7.5 für Details.

**Zentrale, wiederverwendete Support-Module:**

| Modul | Zweck |
|---|---|
| `bot/database.py` | Zentrale Datenschicht — alle SQL-Zugriffe, Schema, Migrationen |
| `bot/announcements.py` | Zentraler Versand ALLER Discord-Ankündigungen |
| `bot/mood.py` | Reine, deterministische Mood-Logik + Text-Varianten (DB-gespeist seit Phase B3) |
| `bot/events.py` | Reine, deterministische Zufallsereignis-Logik |
| `backend/internal_app.py` | Nur-lokaler Mitglieder-Endpunkt (Port 5052) fürs Dashboard |
| `scripts/tunnel_access_core.py` | Einzige Implementierung der Tunnel-Zugangsverwaltung (Erstellen/Widerrufen), von CLI-Skript UND Dashboard genutzt |

---

## 2. Vollständige Befehlsliste

Alle Befehle sind **Hybrid-Commands** (`@commands.hybrid_command`), also technisch für
Prefix-Befehl (`>befehl`) **und** Slash-Command (`/befehl`) vorgesehen. **17 Befehle
insgesamt.**

> **Gelöst am 03.09.2026.** Vorher war kein einziger Slash-Befehl bei Discord
> registriert — `bot.tree.sync()` kam im Projekt nicht vor, nachgeprüft über die API: 0
> global, 0 serverspezifisch. Die Befehle existierten ausschließlich als `>befehl` und
> waren für neue Mitglieder praktisch unauffindbar.
>
> **Seitdem gilt das Umgekehrte: `>` ist abgeschaltet, es gibt nur noch `/`.**
> `command_prefix=commands.when_mentioned` lässt kein Zeichen mehr übrig, mit dem sich ein
> Befehl aus dem Chat aufrufen ließe. Wer trotzdem `>xp` tippt, bekommt einen freundlichen
> Hinweis auf die neue Schreibweise statt Schweigen (`cogs/hilfe.py`, `on_message`) — eng
> gefasst, damit der Bot nicht auf jedes Zitat antwortet. Der Hinweis löscht sich nach 30
> Sekunden selbst und darf verschwinden, sobald sich niemand mehr vertippt.

### Quiz (`cogs/quiz.py`)

| Befehl | Beschreibung |
|---|---|
| `frage` | Zieht eine zufällige, verifizierte, unbeantwortete Frage. Antwort über vier Buttons (A–D). |
| `frage_einreichen` | **Neu:** Frage+4 Antworten+korrekte Antwort direkt per Discord-Befehl einreichen (Slash-Variante: A/B/C/D-Dropdown), ohne Web-Formular. Nutzt dieselbe Validierung wie `/qinput` (`database.validate_question_submission()`, nicht dupliziert). |
| `gamescore` | Eigener Punktestand im Quiz. |
| `quiz_rangliste` | Top 15 nach **Quiz-Punkten**. Bis 03.09.2026 `leaderboard` — der alte Name ließ offen, um welche Wertung es geht. |
| `rules` | Spielregeln als Embed. |
| `botgame` | Ausführliche Quiz-Infos per DM. |

### XP & Level (`cogs/xp_level.py`, neu 03.09.2026)

Am 03.09.2026 aus `cogs/quiz.py` herausgelöst. Der Grund ist inhaltlich: **XP ist kein
Quiz-Feature.** Es entsteht an drei Stellen, von denen nur eine das Quiz ist — Chat
(`cogs/moderation.py`), Quizantworten (`cogs/quiz.py`) und Zufallsereignisse
(`cogs/personality.py`). Solange `/xp` im Quiz-Cog lag, hätte wer nur das Quiz-Modul
installiert einen Befehl bekommen, der überwiegend Chat-Fortschritt anzeigt; und wer nur
XP & Level installiert, hätte gar keine Möglichkeit gehabt, seine XP zu sehen. Im Backend
war die Trennung längst vollzogen (`/xp-level`), nur die Bot-Befehle waren nicht
mitgezogen.

| Befehl | Beschreibung |
|---|---|
| `xp` | Eigener XP-Stand, Level, verbleibende XP bis zum nächsten. |
| `serverxp_rangliste` | Top 15 nach **Server-XP**. Getrennt von `quiz_rangliste`: Es sind zwei Wertungen mit zwei Datentöpfen (`user_xp` bzw. `players`) — jemand kann in der einen ganz oben stehen und in der anderen gar nicht auftauchen. |
| `achievements` | Alle Achievements mit Freischaltstatus (nur für den Aufrufer sichtbar). |

### Pflanzen (`cogs/plants.py`)

| Befehl | Beschreibung |
|---|---|
| `pflanze` | Zufälliges Info-Embed aus `plantinfos`. |

### Sonstiges (`cogs/fun.py`)

| Befehl | Beschreibung |
|---|---|
| `zitat` | Zufälliges Zitat. |

**Am 03.09.2026 entfernt (Freigabe Patrick):**

| Entfernt | Warum, und was an die Stelle tritt |
|---|---|
| `gumo` | Ersetzt durch die **automatische tägliche Guten-Morgen-Nachricht** (siehe Abschnitt 9). Der Befehl war zudem defekt: `bot/data/gumo.txt` enthielt Python-Quelltext statt Textzeilen, der Bot postete deshalb Zeilen wie `"Guten Morgen, {name}!",` oder `gumo_responses = [` — **keine** der 89 Zeilen war sauber. Datei gelöscht (Sicherung im Backup des alten Bots). |
| `hallo` | Altlast des ersten Bots. Neue Mitglieder werden über die Willkommensnachricht begrüßt (Textpool `welcome`). Der zugehörige Textpool `hello` (17 Texte) wurde mitentfernt, da er keinen anderen Nutzer hatte. |
| `bothelp` | Wird durch `/help` ersetzt. Der alte Befehl zeigte **jedem alles** — auch `grant_access` und `revoke_access` standen in der DM, die jedes Mitglied bekam. |

### Admin (`cogs/admin.py`)

| Befehl | Wer darf es? | Beschreibung |
|---|---|---|
| `grant_access` | `is_admin`/Owner | Vergibt eines der 9 Flags. Lehnt die Owner-ID ab. |
| `revoke_access` | `is_admin`/Owner | Entzieht ein Flag. Lehnt die Owner-ID ab. |
| `my_access` | Jeder | Eigene Flags anzeigen. |
| `dm` | `send_dm` | DM im Namen des Bots. |

### Moderation / Reactions / Giveaway / Personality

Keine eigenen Befehle, nur Event-Listener/Hintergrund-Tasks — siehe Abschnitt 1 der
Vorversion dieser Doku bzw. unverändert: Automod (Spam→Link→Schimpfwort-Reihenfolge,
jetzt modernisiert siehe Abschnitt 7), 👍-Rollen-Reaktion, Giveaway-Ziehung, Mood-/
Event-/Community-Reaktions-Herzschlag-Task.

---

## 3. Backend-Routen

`require_guild_member` = jedes aktuelle Server-Mitglied. `require_permission("X")` =
genau dieses Flag oder `is_admin`. `require_any_permission(...)` = mindestens eines.
**60 Routen insgesamt** (59 im öffentlichen Backend + 1 im internen Mitglieder-Endpunkt).

### Auth & Dashboard

| Route | Berechtigung | Zweck |
|---|---|---|
| `/`, `/login/discord`, `/callback`, `/logout` | – | OAuth-Login-Flow (mit `state`-Parameter gegen Login-CSRF) |
| `/dashboard` | Guild-Mitglied | Fragen-Statistiken + eigener XP/Level-Fortschritt |
| `/achievements` | Guild-Mitglied | Eigene Achievement-Übersicht |

### Spiele → Pflanzenquiz (`/games/quiz/*`)

| Route | Berechtigung | Zweck |
|---|---|---|
| `/games` | Guild-Mitglied | Übersichtsseite aller Spiele (aktuell nur Pflanzenquiz, aber erweiterbar) |
| `/games/quiz` | Guild-Mitglied | Leaderboard; Sub-Nav (Übersicht/Einreichen/Verifizieren/Kategorien) |
| `/games/quiz/settings` | `manage_game_settings` | Quiz-Settings (Punkte, Zeitlimit, Cooldown) |
| `/dbot`, `/qinput` | Guild-Mitglied | Fragen einreichen (validiert: `form.get()`, `correct_option ∈ {A,B,C,D}`) |
| `/verify_questions` | `verify_questions` | Fragen verifizieren/bearbeiten/löschen |
| `/categories*` | `manage_categories` | Kategorien-Verwaltung |

### XP & Level (`/xp-level`, eigenständiges Modul, aus dem Quiz herausgelöst)

| Route | Berechtigung | Zweck |
|---|---|---|
| `/xp-level` | Guild-Mitglied | Sub-Nav (Einstellungen/Achievements) |
| `/xp-level/settings` | `manage_game_settings` | XP-/Level-Kurve, Nachrichten-XP-Settings |

### Moderation → Automod + Aktionen & Überwachung

| Route | Berechtigung | Zweck |
|---|---|---|
| `/moderation` | `manage_badword_filter` | Wortliste, Verstöße, Filter-Settings (Info-Tooltips) |
| `/moderation/settings`, `/spam_settings`, `/link_settings` | `manage_badword_filter` | Filter-Verhalten (Link-Filter: kombinierbare Regeln, siehe Abschnitt 6) |
| `/moderation/link_whitelist/*`, `/moderation/add`, `/moderation/remove/<id>`, `/moderation/reset_violations` | `manage_badword_filter` | Listen-/Verstoß-Pflege |
| `/members` | `moderate_members` | Überwachungs-Übersicht: Mitgliederliste sortiert nach Verstößen |
| `/members/<id>` | `moderate_members` | Detail: Aktivität, Quiz/XP, Automod-Historie, Aktionsbuttons |
| `/members/<id>/kick`, `/ban`, `/unban`, `/timeout` | `moderate_members` | Echte Moderationsaktionen — **Owner ist strukturell geschützt** (siehe Abschnitt 8) |

### Personality (`/personality/*`)

| Route | Berechtigung | Zweck |
|---|---|---|
| `/personality`, `/personality/settings` | `manage_game_settings` | Haupt-Settings + Mood-Anzeige |
| `/personality/texts*` | `manage_game_settings` | Admin-editierbare Text-Varianten-Pools (ersetzen die hartcodierten Pools aus `mood.py`) |
| `/personality/thresholds*` | `manage_game_settings` | Mood-Erkennungsschwellen (jetzt Settings statt hartcodiert) |

### Server-Setup (`/server-setup/*`, neu)

| Route | Berechtigung | Zweck |
|---|---|---|
| `/server-setup` | `manage_badword_filter` | Willkommensnachricht + Rollen-Reaktion (vorher fälschlich unter Automod) |
| `/server-setup/welcome_settings`, `/role_settings` | `manage_badword_filter` | Speichern |

### Admin-Werkzeuge / Benutzerverwaltung / Rollen-Zuordnung / App-Zugänge

| Route | Berechtigung | Zweck |
|---|---|---|
| `/sendemb`, `/api/embed` | `create_embed` | Embed erstellen |
| `/dm`, `/api/user_lookup`, `/api/dm` | `send_dm` | DM senden |
| `/giveaway`, `/api/giveaway` | `create_giveaway` | Giveaway starten |
| `/api/channels`, `/api/roles`, `/api/members` | je nach Kontext | Live-Listen für Formulare (Kanal-/Rollen-Dropdowns, Mitglieder-Picker) |
| `/users`, `/users/save`, `/users/revoke_one` | `is_admin` | Benutzerverwaltung, Owner geschützt |
| `/audit_log` | `is_admin` | Reine Ansicht, insert-only |
| `/tunnel_access` | `is_admin` | **Neu, rein lesend:** App-Zugänge-Übersicht (Name, Discord-ID, Status). Erstellen/Widerrufen NUR über das lokale Dashboard/CLI, nicht hier. |

### Interner Endpunkt (`backend/internal_app.py`, Port 5052, NICHT getunnelt)

| Route | Berechtigung | Zweck |
|---|---|---|
| `/members` | Keine (Netzwerk-Isolation IST die Zugriffskontrolle) | Mitgliederliste fürs Dashboard-Dropdown |

---

## 4. Komplettes Datenbankschema (`bot/data/bot.db`)

**25 Tabellen.** Schema-Änderungen laufen ausnahmslos idempotent über
`CREATE TABLE IF NOT EXISTS` bzw. `_ensure_column()`/dedizierte Migrationsfunktionen —
nie destruktiv.

### Quiz-Kern
`questions`, `categories`, `players`, `answered_questions` — unverändert.

### Rechte & Sicherheit
| Tabelle | Zweck |
|---|---|
| `permissions` | Veraltet, nur Migrationsquelle |
| `user_permissions` | Granulares Berechtigungssystem, 9 Flags |
| `audit_log` | Insert-only, Rechte-Änderungen + Config-Änderungen + Owner-Schutz-Auslöser |
| `user_session_invalidations` | **Neu:** `discord_user_id` (PK) + `invalidated_at` — erzwingt Session-Logout bei Tunnel-Widerruf, allgemein nutzbar |

### Automod
| Tabelle | Zweck |
|---|---|
| `badwords`, `badword_violations` | Wortliste + Verstoß-Historie (jetzt mit Homoglyphen-/Teil-Obfuskations-Erkennung, siehe Abschnitt 9) |
| `link_whitelist` | Erlaubte Domains |
| `link_filter_whitelisted_roles` | **Neu:** Rollen, die vom Link-Filter ausgenommen sind |
| `link_filter_exempt_channels` | **Neu:** Kanäle, die komplett ausgenommen sind |

### XP / Level / Achievements
`user_xp`, `achievements`, `user_achievements`, `user_stats` — unverändert (letztere jetzt
zusätzlich mit `message_count`/`last_message_at` für die Überwachungs-Übersicht).

### Personality / Mood / Events / Server-Statistik
`bot_state`, `server_stats_daily`, `daily_active_users`, `server_records`,
`personality_texts` (Admin-editierbare Text-Varianten, seit Phase B3) — unverändert.

### App-Zugänge (neu, Android-App-Ökosystem)
| Tabelle | Zweck |
|---|---|
| `tunnel_access_grants` | `label`, `slug`, `discord_user_id`, `created_at`, `revoked_at`. **`slug` hat KEINE Unique-Constraint** (Bugfix 31.08.2026 — war ursprünglich global-eindeutig, dadurch ließ sich ein einmal vergebener Name nach Widerruf nie wieder neu anlegen; Eindeutigkeit gilt jetzt nur unter aktiven Zeilen, Historie bleibt vollständig erhalten, gleiches Prinzip wie Audit-Log). |

### Sonstiges
`settings`, `giveaways` — unverändert.

---

## 5. Berechtigungssystem

Unverändertes dreistufiges Modell (Owner-Bypass → `is_admin`-Bypass → 9 granulare Flags →
Basis-Rechte per Mitgliedschaft), siehe Vorversion dieser Doku für die volle Erklärung.
**Neu seit Phase B4: `moderate_members`** (Kick/Ban/Unban/Timeout im Backend) — bewusst
ein eigenes, neuntes Flag statt `is_admin` wiederzuverwenden, konsistent mit dem
granularen Modell. `is_admin` impliziert es weiterhin automatisch.

| Flag | Bedeutung |
|---|---|
| `is_admin` | Voller Zugriff, impliziert alle anderen |
| `verify_questions` | Fragen verifizieren |
| `manage_categories` | Quiz-Kategorien |
| `create_embed` | Embeds erstellen |
| `create_giveaway` | Giveaways starten |
| `send_dm` | DMs senden |
| `manage_badword_filter` | Automod + Server-Setup (Willkommen/Rollen-Reaktion) |
| `manage_game_settings` | Quiz-/XP&Level-/Personality-Settings |
| `moderate_members` | **Neu:** Kick/Ban/Unban/Timeout — Owner ist hiervon strukturell ausgenommen (siehe Abschnitt 8) |

---

## 6. Vollständige Settings-Referenz (74 Einstellungen)

Änderungen wirken sofort, ohne Bot-Neustart, werden im Audit-Log protokolliert.

### Willkommen / Regeln (`/server-setup`)
`welcome_channel_id`, `welcome_message_enabled` (neu), `rules_message_id`,
`rules_channel_name`, `member_role_name`, `role_reaction_enabled` (neu) — vorher teils
gar nicht änderbar (nur in `.env`/Code), jetzt vollständig über die Web-UI.

### Guten-Morgen-Nachricht (`/server-setup`, neu 03.09.2026)
`morning_greeting_enabled` (Standard aus), `morning_greeting_time` (`HH:MM`, lokale Zeit),
`morning_greeting_channel_id` (leer = der Bot grüßt **nirgendwo**, kein Rückfall auf einen
anderen Kanal), `morning_greeting_grace_minutes` (Standard 60).

Das Nachlauffenster regelt den Fall, dass der Pi zur eingestellten Zeit aus war: Innerhalb
des Fensters wird nachgeholt, danach fällt der Gruß für den Tag aus — und der Tag wird
trotzdem abgehakt, damit eine spätere Änderung der Uhrzeit ihn nicht nachträglich auslöst.
Ein „Guten Morgen" um 15 Uhr ist schlimmer als gar keins.

### Schimpfwortfilter (`/moderation`)
`badword_filter_enabled`, `badword_filter_action`, `badword_timeout_threshold`,
`badword_timeout_duration_minutes`, `badword_violation_expiry_mode`,
`badword_aggressive_normalization_enabled` (**neu** — schaltet Homoglyphen-/
Teil-Obfuskations-Erkennung ab, falls Fehlalarme auftreten; Basis-Erkennung
Leetspeak/Wiederholung/Vollspacing bleibt davon unabhängig immer aktiv).

### Spam-/Flutschutz
`spam_filter_enabled`, `spam_message_threshold`, `spam_time_window_seconds`,
`spam_filter_action`, `spam_timeout_duration_minutes` — unverändert.

### Link-Filter (grundlegend überarbeitet, 31.08.2026)
**Von einem exklusiven Modus zu drei unabhängig kombinierbaren Regeln:**
`link_filter_block_all_enabled`, `link_filter_whitelist_enabled`,
`link_filter_new_members_enabled` — beliebig gleichzeitig aktivierbar (z.B. Whitelist UND
"neue Mitglieder dürfen keine Links" gleichzeitig). Bei `block_all` + `whitelist`
gleichzeitig gewinnt `block_all`. `link_filter_mode` (alter exklusiver Wert) bleibt als
Setting bestehen, wird aber nicht mehr aktiv ausgewertet (nur historische Migrationsquelle).
Zusätzlich: `link_filter_enabled`, `link_filter_new_member_days`, `link_filter_action`,
`link_filter_timeout_threshold`, `link_filter_timeout_duration_minutes`. Ausnahmen
(Rollen/Kanäle) liegen in eigenen Tabellen, nicht als Settings (siehe Abschnitt 4).

### Quiz (`/games/quiz/settings`)
`quiz_points_correct`, `quiz_answer_timeout_seconds`, `quiz_cooldown_seconds`,
`game_quiz_enabled` (neu — An/Aus-Schalter fürs Spiel selbst).

### XP / Level (`/xp-level/settings`, aus dem Quiz herausgelöst)
`xp_level_base`, `xp_level_growth`, `xp_cooldown_seconds`, `xp_per_quiz`,
`message_xp_enabled`, `xp_per_message`, `daily_xp_limit`.

### Ankündigungen
`announcement_channel_id`, `announcements_enabled` — unverändert.

### Personality / Mood (`/personality/settings`, `/personality/thresholds`)
`personality_enabled`, `mood_enabled`, `mood_update_interval_minutes`,
`personality_response_frequency`, `personality_humor_level`, `personality_response_mode`,
`personality_spontaneous_channel_id`, `personality_spontaneous_min_hours`,
`dynamic_status_enabled`, `status_rotation_minutes`.

**Neu, vorher hartcodiert (User-Wunsch, überschreibt bewusst die Phase-9-Entscheidung):**
`mood_night_start_hour`/`_end_hour`, `mood_activity_window_minutes`,
`mood_excited_active_user_threshold`, `mood_bored_inactivity_minutes`,
`mood_violation_window_minutes`, `mood_annoyed_violation_threshold` — die
Mood-Erkennungsschwellen selbst, mit unveränderten Defaults (keine Verhaltensänderung
ohne Admin-Eingriff). Text-Varianten-Pools liegen in der Tabelle `personality_texts`
(Abschnitt 4), nicht als Settings.

### Zufallsereignisse
`random_events_enabled`, `event_check_interval_minutes`, `event_probability_percent`,
`event_channel_id`, `event_weight_humor/_quiz/_xpdrop/_community`, `event_xpdrop_amount`,
`event_community_bonus_multiplier`, `event_community_bonus_minutes` — unverändert.

### Erweiterte Community-Reaktionen
`community_reactions_enabled`, `streak_announcement_thresholds`, `milestone_thresholds`
— unverändert.

---

## 7. Menü-/Modul-Struktur im Backend

Vorbereitung auf eine spätere echte Modularisierung (Module einzeln
hinzufügbar/entfernbar) — aktuell nur Navigation/Menü sauber nach dieser Logik getrennt,
Code/Ordner bleiben technisch wo sie sind:

| Navbar-Punkt | Enthält |
|---|---|
| **Spiele → Pflanzenquiz** | Übersicht, Settings, Fragen einreichen, verifizieren, Kategorien — Struktur erweiterbar für künftige weitere Spiele (jedes bekäme eigenes Leaderboard/Settings/Sub-Nav) |
| **XP & Level** | Eigenständiges Kern-Modul, da sowohl vom Quiz als auch von Nachrichten-XP gespeist |
| **Moderation → Automod** | Schimpfwort-/Spam-/Link-Filter |
| **Moderation → Aktionen & Überwachung** | Kick/Ban/Timeout, Überwachungs-Dashboard |
| **Moderation → Embed senden** | Ansagen als Embed in einen Kanal (vorher unter Admin-Werkzeuge) |
| **Moderation → DM senden** | Private Nachricht im Namen des Bots, z.B. Verwarnung (vorher unter Admin-Werkzeuge) |
| **Bot-Setup → Willkommen & Regeln** | Willkommensnachricht, Rollen-Reaktion |
| **Bot-Setup → Giveaway starten** | Giveaway anlegen |
| **Bot-Setup → Personality** | Haupt-Settings, Text-Varianten, Mood-Schwellen (vorher eigener Menüpunkt) |
| **Admin-Werkzeuge** | Benutzerverwaltung, Audit-Log, App-Zugänge — Kernsystem, kein Modul |

**Umbenennung und Umsortierung (02.09.2026):** „Server-Setup" heißt jetzt **Bot-Setup**,
weil dort das Verhalten des *Bots* eingerichtet wird und nicht der Discord-Server.
Personality gehört sachlich dorthin und stand vorher als eigener Punkt daneben. Embed und
DM sind in der Praxis Moderationsmittel (Ansagen, Verwarnungen) und keine Bot-Verwaltung —
sie liegen deshalb unter Moderation. Unter Admin-Werkzeugen stehen jetzt nur noch
Benutzerverwaltung, Audit-Log und App-Zugänge, die vorher einzeln die Hauptleiste
verbreiterten.

---

## 8. Sicherheitsfundament

### Owner-Schutz (unverändert seit Phase 1)
`database.has_permission()` prüft den Owner zuerst, vor jeder SQL-Abfrage.
`/users`, `grant_access`/`revoke_access` lehnen die Owner-ID als Ziel ab. **Erweitert um
Moderationsaktionen (Phase B4):** `guild.kick()`/`ban()`/`timeout()` werden für die
Owner-ID serverseitig NIE aufgerufen (per Fake-Discord-Objekten bewiesen: 0 tatsächliche
API-Calls gegen den Owner), jeder Versuch wird als `owner_protection_triggered` geloggt —
exakt dasselbe Schutzprinzip wie bei der Rechtevergabe, jetzt zusätzlich auf echte
Moderation angewendet.

### Audit-Log (unverändert)
Insert-only, protokolliert Rechte-Änderungen, abgelehnte Owner-Zugriffsversuche,
Config-Änderungen.

### Session-Invalidierung (neu, 31.08.2026)
Netzwerk-Widerruf allein reicht nicht: eine bestehende Discord-OAuth-Browser-Session
bliebe sonst trotz entzogenem Tunnel-Zugang technisch gültig. Beim Login wird
`session["login_time"]` gesetzt; `database.invalidate_user_session(discord_user_id)`
setzt einen Zeitstempel in `user_session_invalidations`; ein `@app.before_request`-Hook
(`_enforce_session_invalidation` in `backend/app.py`) prüft bei JEDEM authentifizierten
Request, ob die Session vor diesem Zeitstempel ausgestellt wurde — falls ja, wird sie
serverseitig verworfen und ein Login erzwungen. Wird automatisch beim Widerruf eines
App-Zugangs ausgelöst (`scripts/tunnel_access_core.py`), ist aber bewusst allgemein
gehalten (nicht tunnel-spezifisch benannt) für künftige "Session sofort beenden"-Zwecke.

### Automatisiertes Backup (unverändert)
`scripts/backup_db.sh`, SQLite Online-Backup-API, Ziel `/var/lib/influxdb/blackforestbot-backups/`,
14-Tage-Rotation. systemd-Timer angelegt, Aktivierung liegt beim User.

---

## 9. Personality/Mood/Events-System

Grundprinzip unverändert: **Mood ist ein rein interner, deterministischer Spielzustand.**
Ablauf (Herzschlag-Task, 60s-Takt), fünf Mood-Werte (`happy` vorbereitet, ungenutzt),
Prioritätsreihenfolge `annoyed` → `sleepy` → `excited` → `bored` → `neutral` — siehe
Vorversion dieser Doku für die volle Herleitung. **Seit 31.08.2026 sind die
Erkennungsschwellen selbst Settings** (Abschnitt 6), nicht mehr hartcodiert — User-Wunsch,
überschreibt die ursprüngliche Phase-9-Entscheidung.

### Tägliche Guten-Morgen-Nachricht (neu 03.09.2026)

Hängt am **selben** Herzschlag-Task wie Mood, Status und Zufallsereignisse — bewusst kein
zweiter Zeitplaner. Ersetzt den entfernten `>gumo`-Befehl.

**Ausdrücklich unabhängig von der Stimmung** (Vorgabe Patrick): eine flache Liste im
Textpool `morning`, jeder Text gleich wahrscheinlich, ausgewählt per `random.choice()`
statt `mood.pick_variant()`. Der Pool ist deshalb der erste mit `has_mood: False` **und**
`has_tier: False` — die Bearbeitungsseite blendet beide Auswahlfelder dann von selbst aus,
dafür war kein Backend-Code nötig. Neue Seed-Form `"plain"` in `_seed_personality_texts()`.

Die Texte enthalten bewusst **kein** `{name}`: Die Nachricht richtet sich an den ganzen
Kanal, nicht an eine Person. Genau daran scheiterte die Übernahme der 89 alten
`gumo`-Texte, die durchgehend auf `{name}` gemünzt waren.

Die Zeitentscheidung steht als reine, I/O-freie Funktion in `bot/scheduling.py`
(`morgengruss_faellig()`) — gleiche Trennung wie bei `events.py`, damit sie ohne Discord
und ohne Warten auf den nächsten Morgen testbar ist (38 Tests, siehe Abschnitt 16). Der
Merker gegen doppelte Grüße ist ein **lokales Kalenderdatum** in `bot_state`
(`last_morning_greeting`), kein UTC-Zeitstempel wie die übrigen `last_*`-Werte: Die Frage
lautet „war heute schon", nicht „wie lange ist es her".

**Text-Varianten editierbar (Phase B3):** `/personality/texts` — pro Pool (Guten-Morgen,
Quiz-Feedback, Willkommensnachricht, spontane Nachricht, Status-Text, Humor-Event) eine
Tabelle mit Inline-Bearbeiten/Löschen + Hinzufügen. `mood.py` bleibt bewusst I/O-frei
(reine Funktionen bekommen den Pool als Parameter), nur die Cogs holen ihn aus der DB.
Absturzschutz: ein Platzhalter-Tippfehler in einem Admin-Text (`{Nam}` statt `{name}`)
fängt `pick_variant()` ab und fällt auf "kein Text" zurück statt zu crashen.

**Automod-Modernisierung (31.08.2026):** Neu erkannt werden Unicode-Homoglyphen
(kyrillisch/griechisch → lateinisch) und Teil-Obfuskation innerhalb eines Wort-Tokens
(z.B. `sch.eisse`, `arsch-loch`) — beide über `badword_aggressive_normalization_enabled`
abschaltbar, da "riskantere" (nicht wortgrenzen-geschützte) Techniken. Bereits vorhandene
Erkennung (Leetspeak, Wortgrenzen-Schutz, Wiederholung, Vollspacing) bleibt unabhängig
davon immer aktiv. (i)-Info-Tooltips erklären auf `/moderation` den Ablauf jedes Filters.

Für Zufallsereignisse und erweiterte Community-Reaktionen (Server-Rekorde, Quiz-Serien,
Meilensteine) gilt die Beschreibung der Vorversion unverändert.

---

## 10. Bot Connect App (Android) + Bot Connect Dashboard

Vollständige Details, Architektur-Begründungen und Installationsanleitungen liegen in den
jeweiligen README.md-Dateien im Repo — hier nur die Kurzfassung + Verweis:

- **Bot Connect App** (`android-app/README.md`): Kotlin, minSdk 26, Package
  `de.blackforest.bottunnel`. Bündelt `dumbpipe` (für Android cross-kompiliert per GitHub
  Actions, `.github/workflows/build-android.yml`) als Subprozess, WebView mit
  Zertifikats-Pinning auf `https://127.0.0.1:8500`, aktives Warten (bis 45s) auf
  einsatzbereiten Tunnel statt fixer Verzögerung, Tunnel-Prozess-Logging für Diagnose.
  Tunnel läuft nur, während die App im Vordergrund ist.
- **Bot Connect Dashboard** (`bot-connect-dashboard/README.md`): separater Flask-Prozess,
  NUR `127.0.0.1:5050`, kein Login (Netzwerk-Isolation ist die Zugriffskontrolle).
  Erstellt/widerruft App-Zugänge (Dropdown aus Discord-Mitgliederliste, über den internen
  Port 5052 bezogen), teilt sich die Kernlogik mit `scripts/manage_bot_access.sh`
  (`scripts/tunnel_access_core.py`) — keine doppelte Implementierung.

**Historisch abgelöst:** Der früher einzige, geteilte Tunnel-Dienst
(`dumbpipe-discordbot.service`, ein Ticket für alle) wurde am 31.08.2026 komplett
deaktiviert — er ließ sich nicht einzeln widerrufen. Siehe
`Fernzugriff-Setup-Anleitung.md` Abschnitt 7 für die volle Historie.

---

## 11. OAuth-Setup

Unverändert gegenüber der Vorversion, mit einer Ergänzung: der Login-Flow verwendet jetzt
einen `state`-Parameter (`oauth.py`, `build_authorize_url()`) gegen Login-CSRF —
Session-gebunden, per `secrets.compare_digest()` im Callback verglichen.

```
DISCORD_CLIENT_ID=<...>
DISCORD_CLIENT_SECRET=<...>
BOOTSTRAP_ADMIN_DISCORD_ID=<...>
BOT_OWNER_ID=<...>
FLASK_SECRET_KEY=<...>
```

Scope: nur `identify`. Vorlage: `bot/.env.example` (nur Variablennamen, keine Werte).

---

## 12. Start / Stop / Logs

Unverändert:

```bash
sudo systemctl restart blackforestbot.service
journalctl -u blackforestbot -f
sudo systemctl status blackforestbot.service
```

**Neu, zwei weitere Dienste im Ökosystem:**
```bash
sudo systemctl status bot-connect-dashboard.service   # Port 5050
```
Der interne Mitglieder-Endpunkt (Port 5052) läuft als Thread IM Bot-Prozess selbst, kein
eigener systemd-Dienst nötig.

**Gelöst am 03.09.2026 (vorher als bekanntes Problem geführt).** Der Dienst schrieb in
Wahrheit gar kein Log — nicht „verzögert", sondern nie. Nachgemessen über 24 Stunden: 21
Zeilen im Journal, alle 21 von systemd, keine einzige vom Bot. Python puffert stdout
blockweise, sobald es kein Terminal ist; die Ausgaben blieben im 8-KB-Puffer, der bei drei
kurzen Startmeldungen nie volllief. Damit war jede Fehlersuche über das Bot-Log
aussichtslos — und die unbehandelten Ausnahmen, die discord.py normalerweise auf stderr
schreibt, waren ebenfalls unsichtbar (siehe Abschnitt 16).

Behoben über eine Ergänzungsdatei, bewusst nicht in der Haupt-Unit, damit ein Update sie
nicht entfernt:

```
/etc/systemd/system/blackforestbot.service.d/logging.conf

[Service]
Environment=PYTHONUNBUFFERED=1
```

Seither erscheinen die Startmeldungen (`Logged in as ...`, Waitress-Ports) unmittelbar in
`journalctl -u blackforestbot -f`. Der frühere Notbehelf — ein Vordergrund-Lauf mit
`python -u` — wird dadurch überflüssig.

**Vor jeder Code-Änderung:** Bot stoppen → Backup von `.env`+`bot.db` → Änderung →
Syntax-Check → Bot starten → Tests → Bestandsdaten-Kontrolle → **git commit + push**
(seit der Umstellung auf Versionsverlauf fester Bestandteil des Workflows).

---

## 13. Umgesetzte Sicherheitsmaßnahmen

Ergänzend zur Vorversion (Secrets-Handling, generierter Secret-Key, kein Debug-Modus,
`.gitignore`, abgesicherte `/api/*`-Routen, waitress+nginx, ProxyFix, Owner-Schutz,
Audit-Log, Backup, kein `manage_users`-Flag) — **neu seit 31.08.2026:**

| Maßnahme | Warum |
|---|---|
| CSRF-Schutz (Session-Token, Formularfeld/Header) | Verhindert zustandsändernde Fremd-Requests |
| OAuth `state`-Parameter | Verhindert Login-CSRF |
| `math.isfinite()`-Prüfung bei Personality-Zahlenfeldern | `inf`/`nan` crashte vorher mit 500er |
| `form.get()` statt `form[...]` bei `/qinput` | Kein ungefangener `KeyError` mehr |
| Serverseitiges Logging aller `/api/*`-Fehler | Vorher nur an Client zurückgegeben, nie geloggt |
| `markupsafe.escape()` in der OAuth-Fehlerseite | War reflektiertes XSS (roher Exception-Text) |
| DB-Indizes auf `badword_violations.discord_user_id`/`questions.category_id` | Performance bei wachsenden Tabellen |
| Session-Invalidierung bei Tunnel-Widerruf | Siehe Abschnitt 8 |
| Interner Port 5052 statt Wiederverwendung von 5001 | Siehe Abschnitt 1 — Port 5001 ist trotz "127.0.0.1" tunnel-erreichbar |
| Owner-Schutz auf echte Moderationsaktionen erweitert | Siehe Abschnitt 8 |
| `_stop_service_reliably()` in `tunnel_access_core.py` | Verhindert Reste durch Race zwischen Widerruf und dumbpipe-Absturz-Neustart |

---

## 14. Unterschiede zum alten Bot

Unverändert gegenüber der Vorversion (siehe dort für die volle Liste: Cogs statt
Monolith, Hybrid-Commands, Buttons statt Chattext, getrenntes XP-System,
DB-persistierte Giveaways, OAuth statt eigenem Login, granulare Flags, Backup+Audit-Log,
Mood-/Personality-System, ein Prozess statt zwei, waitress+nginx). **Zusätzlich neu:**
Git-Versionsverlauf (vorher keiner), Android-App + lokales Dashboard statt reinem
Termux/CLI-Zugriff, modernisierter Schimpfwortfilter (Homoglyphen/Teil-Obfuskation statt
reiner Wortliste).

---

## 15. Den Bot erweitern

Unverändert gegenüber der Vorversion (neues Cog in `bot/cogs/`, in `load_cogs()`
eintragen; neue Settings über `config.DEFAULT_SETTINGS` + `database.get_setting()`/
`set_setting()`; neuer Laufzeit-Zustand über `database.get_bot_state()`/`set_bot_state()`).
**Für ein neues Modul im Menüsinn** (Abschnitt 7): eigener Navbar-Punkt oder Sub-Nav nach
dem Muster von `/games/quiz` (`_quiz_subnav.html`) bzw. `/moderation`
(`_moderation_subnav.html`), inkl. der `with context`-Lektion bei Jinja-Makro-Imports
(Makros erben den Template-Kontext beim Import standardmäßig NICHT — `has_permission()`
wäre sonst `undefined`).

---

## 16. Testsuite (`tests/`, 478 Tests)

Die Tests laufen bei jedem Push auf `main` und bei jedem Pull Request
(`.github/workflows/tests.yml`) und lokal mit:

```bash
cd /opt/BlackforestBot-v2 && venv/bin/python -m pytest -q
```

### Grundprinzip: keine Laufzeitabhängigkeiten

Die CI installiert **ausschließlich pytest** — kein discord.py, kein Flask, keine
Datenbank. Das ist eine bewusste Entscheidung: Die Tests laufen dadurch in unter einer
Sekunde, brauchen kein Netz und können nicht an einer Paketversion scheitern.

Damit die Tests trotzdem **echten Produktivcode** prüfen und keine Nachbauten (die genau
dann auseinanderlaufen, wenn es darauf ankommt), gibt es zwei Techniken:

- **`tests/discord_source.py`** parst `backend/app.py` und führt gezielt einzelne
  Funktionen daraus aus — mit einem Ersatz für `discord.Embed`, dessen Längenberechnung
  gegen discord.py 2.4.0 abgeglichen ist. Der Rest des Moduls (Flask-App, Datenbank) wird
  nie angefasst.
- **AST-Prüfungen**, die strukturelle Zusagen festhalten: dass eine Whitelist *vor* dem
  ersten SQL-Aufruf greift, dass eine Grenze überhaupt existiert.

### Was abgedeckt ist

| Datei | Tests | Sichert ab |
|---|---|---|
| `test_discord_limits.py` | 76 | Alle acht Embed-Grenzen gegen die offizielle Dokumentation, jeweils genau am Limit erlaubt und ein Zeichen darüber abgelehnt; die 6000-Zeichen-Gesamtsumme; Farbwerte; Zeitzonenbehandlung |
| `test_rollenabgleich.py` | 15 | Abgleich Backend-Rechte gegen Discord-Rollen; insbesondere dass nicht zugeordnete Rollen nie als überzählig gelten |
| `test_nutzertexte.py` | 97 | Oberflächentexte enthalten keine Entwicklerhistorie, keinen Jargon und keine Verweise auf das alte Befehlspräfix |
| `test_csrf_coverage.py` | 73 | Jedes POST-Formular und jeder `fetch`-POST trägt ein CSRF-Token — auf **beiden** zulässigen Wegen (verstecktes Feld oder `X-CSRF-Token`-Header) |
| `test_tunnel_access.py` | 14 | Widerruf von Iroh-Zugängen, insbesondere das Fail-Open-Verhalten bei fehlendem sudo |
| `test_moderation_limits.py` | 33 | Timeout-Deckelung auf 28 Tage, DM-Länge, SQL-Spalten-Whitelists |
| `test_moderation_hierarchy.py` | 7 | Rollenhierarchie als eigene Schranke neben den Berechtigungen |
| `test_android_build.py` | 4 | Der `cargo update`-Schritt im Build-Workflow, ohne den die APK gegen die kaputte iroh-Version gebaut wird |
| `test_hilfe.py` | 39 | Rechtefilterung in `/help` mit gestellten Berechtigungen, Modulgliederung, Übergangshinweis für `>` |
| `test_befehlsfehler.py` | 27 | Jeder absehbare Befehlsfehler sagt etwas; Prüfungen antworten nicht mehr selbst |
| `test_achievements_module.py` | 43 | Modultrennung der Achievements und alle Auslöseschwellen; prüft ausdrücklich, dass `database.py` keinen Achievement-Namen mehr kennt |
| `test_morning_greeting.py` | 38 | Zeitlogik der täglichen Guten-Morgen-Nachricht: Fenster, Tagesmerker gegen doppelte Grüße nach einem Neustart, Randuhrzeiten um Mitternacht, unbrauchbare Eingaben |
| `test_doku.py` | 4 | Die Testzahlen in dieser Tabelle stimmen mit der Wirklichkeit überein — eine Doku mit falschen Zahlen ist schlimmer als eine ohne |
| `test_mood.py` | 5 | Mood-Texte, Häufigkeitsgrenzen, Nachtstunden über Mitternacht |
| `test_webview_dialogs.py` | 3 | `WebChromeClient` in der App — ohne ihn schlucken WebViews `confirm()` und Kick/Ban/Timeout bleiben wirkungslos |

### Warum die Zahlen mit Quelle und Datum dokumentiert sind

In `test_discord_limits.py` und `test_moderation_limits.py` steht zu jeder Grenze, woher
sie stammt und wann sie nachgeschlagen wurde. Grund: Diese Zahlen sehen willkürlich aus
(warum 40320? warum 4096?) und werden sonst irgendwann „aufgerundet" oder aus dem
Gedächtnis korrigiert. Fällt einer dieser Tests, ist entweder der Code falsch **oder
Discord hat seine Grenzen geändert** — im zweiten Fall gehört die neue Zahl mit Datum und
Quelle in dieselbe Datei.

### Die Tests wurden gegen absichtlich eingebaute Fehler geprüft

Ein Test, der nie fehlschlägt, ist wertlos. Die neuen Prüfungen wurden deshalb mit
gezielten Mutationen im Produktivcode gegengeprüft — Gesamtlimit entfernt, Feldzahl von 25
auf 30 gesetzt, `>` durch `>=` ersetzt, ein CSRF-Token aus einem Template gelöscht. Jede
Mutation wurde von mindestens einem Test gefangen, danach wurde der Originalzustand
wiederhergestellt.

---

### Rollenhierarchie

Stand 03.09.2026, über die Discord-API nachgeprüft:

| Rolle | Position | Vom Bot verwaltbar |
|---|---|---|
| Manager | 7 | **nein** — steht über dem Bot |
| Blackforest´s Helper (Bot) | 6 | — |
| Moderator | 5 | ja |
| Supporter, Ideenschmied, Member, Server Booster | 4–1 | ja |

Discord erlaubt einem Bot nur, Rollen **unterhalb** seiner eigenen höchsten Rolle zu
vergeben, zu entziehen und deren Träger zu kicken, zu bannen oder stummzuschalten. Das
Administrator-Recht hebt das nicht auf — die Hierarchie ist eine eigene Schranke.

**Die Anordnung hat sich am 03.09.2026 geändert.** Vorher stand der Bot unter Moderator,
damit das Moderationsteam vor ihm geschützt war. Patrick hat ihn darüber gezogen, weil der
Bot Moderator-Rollen automatisch vergeben können soll (siehe
`~/Desktop/pläne/11-rollenvergabe.md`). Beides gleichzeitig geht nicht: Ein Bot, der eine
Rolle vergeben kann, kann sie auch entziehen und ihre Träger moderieren.

**Bewusst in Kauf genommen:** Moderatoren sind nicht mehr vor dem Bot geschützt.
Geschützt bleiben ausschließlich **Manager** und der Server-Eigentümer.

Manager über dem Bot zu lassen ist der verbliebene Schutz. Jede weitere Erhöhung der
Bot-Rolle vergrößert den Kreis derer, die der Bot entfernen kann — Vorschläge dazu sollten
diesen Zusammenhang immer mitnennen.

---

## 17. Offene Punkte (Stand 03.09.2026)

- ~~**`ERR_TIMED_OUT` in der Android-App**~~ — **am 02.09.2026 gelöst.** Die hier
  vermutete Relay-/NAT-Instabilität war NICHT die Ursache. Es waren zwei unabhängige
  Fehler: dumbpipes mitgelieferte `Cargo.lock` nagelte iroh auf eine Version fest, die
  den Android-JNI-Panic nur in Debug-Builds abfängt, und die Bereitschaftsprüfung der
  App führte ihre Versuche ohne Pause hintereinander aus — sie gab damit Millisekunden
  nach dem Start auf, rund 3 Sekunden bevor der Tunnel überhaupt bereit sein konnte.
  Vollständige Analyse in `Fernzugriff-Setup-Anleitung.md`, Abschnitt 9.1.
- **Echter Online/Idle/DND-Status** in der Überwachungs-Übersicht: aktuell Näherung über
  Nachrichtenaktivität statt echtem Discord-Presence-Status (bräuchte den privilegierten
  Presence-Intent, manuell im Discord Developer Portal freizuschalten).
- **Live-Test der Moderationsaktionen** (Kick/Ban/Timeout) gegen den echten Server:
  bisher nur per Fake-Discord-Objekten/DB-Kopien verifiziert.
- **Kein einziger Slash-Befehl ist bei Discord registriert** (gefunden 03.09.2026). 17
  Befehle sind als `commands.hybrid_command` gebaut und damit für `/befehl` vorgesehen,
  inklusive Beschreibungstexten und Auswahllisten — aber `bot.tree.sync()` kommt im
  Projekt nicht vor. Bei Discord abgefragt: 0 global und 0 serverspezifisch registrierte
  Befehle. Alle 17 existieren ausschließlich als `>befehl`, was für neue Servermitglieder
  praktisch unauffindbar ist. Plan liegt unter `~/Desktop/pläne/01-slash-commands.md`.
- **Keine Fehlerbehandlung für Chat-Befehle.** Es gibt keinen `on_command_error`-Handler;
  fehlende Argumente, falsche Argumenttypen und Cooldowns bleiben für den Nutzer völlig
  stumm. Dieselbe Fehlerklasse wie beim WebView-Problem: Es passiert nichts und nichts
  sagt warum. Plan unter `~/Desktop/pläne/04-befehlsfehler.md`.
- **Moderationsgründe werden nicht auf 512 Zeichen begrenzt**, die von Discord für den
  `X-Audit-Log-Reason`-Header dokumentierte Obergrenze (URL-kodiert gezählt, ein Umlaut
  belegt dort sechs Zeichen). Ein zu langer Grund lässt Kick/Ban/Timeout mit einer
  nichtssagenden 400er-Meldung scheitern. Plan unter
  `~/Desktop/pläne/02-moderationsgrund.md`.
- **Bild-/Link-Felder im Embed-Builder werden nicht geprüft.** Discord akzeptiert nur
  `http`, `https` und `attachment` und antwortet sonst mit `Invalid Form Body`, ohne das
  betroffene Feld zu nennen. Plan unter `~/Desktop/pläne/03-embed-urls.md`.
