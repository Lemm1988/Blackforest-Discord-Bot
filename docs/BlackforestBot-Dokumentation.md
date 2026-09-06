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
2. Vollständige Befehlsliste (16 Slash-Befehle)
3. Backend-Routen (`backend/app.py` + `backend/internal_app.py`)
4. Komplettes Datenbankschema (31 Tabellen: 25 im Kern, 6 aus den Modulen)
5. Berechtigungssystem (Owner, `is_admin`, 9 Flags, Basis-Rechte)
6. Vollständige Settings-Referenz (70 mit Voreinstellung)
7. Menü-/Modul-Struktur im Backend
8. Rollenvergabe (Reaktionsrollen, verdiente Rollen, Rechte spiegeln)
9. Sicherheitsfundament (Owner-Schutz, Audit-Log, Backup, Session-Invalidierung)
10. Personality/Mood/Events-System
11. Bot Connect App (Android) + Bot Connect Dashboard
12. OAuth-Setup
13. Start / Stop / Logs
14. Umgesetzte Sicherheitsmaßnahmen
15. Unterschiede zum alten Bot
16. Den Bot erweitern
17. Testsuite (`tests/`, 972 Tests)
18. Offene Punkte (Stand 05.09.2026)
19. Chronik (03. bis 05.09.2026)

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

### Voraussetzung, die fast den Server lahmgelegt hätte

**Slash-Befehle brauchen ein Discord-Recht, Prefix-Befehle nicht.** Ein `>befehl` war für
Discord eine gewöhnliche Chatnachricht — dafür genügt „Nachrichten senden", das jeder hat.
Ein `/befehl` verlangt zusätzlich **„Anwendungsbefehle verwenden"**
(`USE_APPLICATION_COMMANDS`, Bit 31).

Auf diesem Server hatten `@everyone` und `Member` dieses Recht nicht, und 61 Kanäle
verboten es zusätzlich ausdrücklich. Das war jahrelang folgenlos — bis am 03.09.2026 `>`
abgeschaltet wurde. **Am 04.09. meldete ein Mitglied, dass es `/frage` nicht aufrufen
kann; tatsächlich konnte kein normales Mitglied irgendeinen Befehl mehr benutzen.**

Die Lehre für künftige Umstellungen dieser Art: Vor dem Abschalten der Prefix-Befehle
prüfen, ob die *Mitglieder* Slash-Befehle überhaupt benutzen dürfen — nicht nur, ob die
Befehle registriert sind und der Bot seine Rechte hat.

### Wer darf Botbefehle benutzen (Stand 04.09.2026)

Nach der Bereinigung gibt es **kein einziges Verbot** mehr auf dieses Recht. Wer Befehle
nutzen darf, ergibt sich aus zwei Dingen: den Rollenrechten und einer einzigen Erlaubnis
für Member in der Willkommens-Kategorie.

| Wer | Wo | Warum |
|---|---|---|
| Neuling (nur `@everyone`) | nirgends | Das Recht fehlt der Rolle. Wer mitreden will, akzeptiert erst die Regeln per 👍 und wird Member. |
| **Member** | nur die 5 Kanäle der Kategorie `\\Wilkommen\\` | Ausdrückliche Erlaubnis dort, sonst nichts |
| **Moderator, Manager** | überall | Recht auf Rollenebene, nichts entzieht es mehr |
| alle übrigen Rollen | nirgends | reine Dekoration, ohne Rechte |

Das ist der eigentliche Gewinn der Bereinigung: Eine neue Rolle bekommt Befehlsrechte an
**einer** Stelle in den Rolleneinstellungen — vorher hätte man 61 Kanäle nachpflegen müssen.

Zwei Rollen hatten das Recht, obwohl sie reine Dekoration sind (Supporter, Keimling 🌱) —
ihnen wurde es entzogen.

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

### Rollenvergabe (neu 03.09.2026)

| Route | Wer darf? | Zweck |
|---|---|---|
| `/roles-mapping` | `is_admin` | Zuordnung Berechtigung → Discord-Rolle |
| `/roles-mapping/save` | `is_admin` | Zuordnung speichern |
| `/roles-mapping/sync` | `is_admin` | Bestand abgleichen — erst Vorschau, dann Bestätigung |
| `/reaction-roles` | `manage_badword_filter` | Reaktionsrollen anlegen und bearbeiten |
| `/reaction-roles/add`, `/<id>/update`, `/<id>/delete` | dito | |
| `/earned-roles` | `manage_badword_filter` | Verdiente Rollen (Bedingung → Rolle) |
| `/earned-roles/add`, `/<id>/update`, `/<id>/delete` | dito | |

### Interner Endpunkt (`backend/internal_app.py`, Port 5052, NICHT getunnelt)

| Route | Berechtigung | Zweck |
|---|---|---|
| `/members` | Keine (Netzwerk-Isolation IST die Zugriffskontrolle) | Mitgliederliste fürs Dashboard-Dropdown |

---

## 4. Komplettes Datenbankschema (`bot/data/bot.db`)

**31 Tabellen** — 25 im Kern, 6 bringen die Module selbst mit (siehe
„Modultrennung“). Schema-Änderungen laufen ausnahmslos idempotent über
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

### Rollenvergabe (neu 03.09.2026)

| Tabelle | Inhalt |
|---|---|
| `permission_roles` | Zuordnung Backend-Recht → Discord-Rolle. `PRIMARY KEY` auf dem Recht verhindert strukturell zwei Rollen pro Recht; umgekehrt **darf** eine Rolle mehreren Rechten zugeordnet sein, deshalb kein `UNIQUE` auf `role_id`. |
| `permission_role_grants` | Welche Rolle der Bot wegen einer Berechtigung vergeben hat. **Der Merker, ohne den der erste Abgleich jedem die von Hand vergebene Rolle entzogen hätte.** |
| `reaction_roles` | Regeln „Emoji auf Nachricht → Rolle". Kanal und Rolle als **ID**, nicht als Name — Namen ändern sich, IDs nicht. `UNIQUE(message_id, emoji)`, weil ein Emoji auf einer Nachricht nur für eine Rolle stehen kann. |
| `earned_roles` | Regeln „Bedingung erfüllt → Rolle anbieten": Bedingungsart, Schwelle, Ankündigungstext, Kanal, ob automatisch vergeben wird. |
| `earned_role_grants` | Wem wurde eine verdiente Rolle schon angekündigt bzw. gegeben. Ohne das käme die Ankündigung bei **jeder** weiteren Quizantwort erneut. |

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

## 6. Vollständige Settings-Referenz (70 mit Voreinstellung)

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

**Seit dem 03.09.2026 ist die Modularisierung echt**, nicht mehr nur eine Menüstruktur:
Jedes Modul besteht aus einer Datei (`bot/modul_*.py`) und bringt Schema, Migrationen,
Achievements, Bedingungen, Abfragen und seinen Beitrag zur Mitglieder-Übersicht selbst
mit. `module.INSTALLIERT` sagt an einer Stelle, was aktiv ist; ein nachträglich
hinzugefügtes Modul legt seine Tabellen beim nächsten Start selbst an. Siehe Abschnitt
„Modultrennung".

| Navbar-Punkt | Enthält |
|---|---|
| **Spiele → Pflanzenquiz** | Übersicht, Settings, Fragen einreichen, verifizieren, Kategorien — Struktur erweiterbar für künftige weitere Spiele (jedes bekäme eigenes Leaderboard/Settings/Sub-Nav) |
| **XP & Level** | Eigenständiges Kern-Modul, da sowohl vom Quiz als auch von Nachrichten-XP gespeist |
| **Moderation → Automod** | Schimpfwort-/Spam-/Link-Filter |
| **Moderation → Aktionen & Überwachung** | Kick/Ban/Timeout, Überwachungs-Dashboard |
| **Moderation → Embed senden** | Ansagen als Embed in einen Kanal (vorher unter Admin-Werkzeuge) |
| **Moderation → DM senden** | Private Nachricht im Namen des Bots, z.B. Verwarnung (vorher unter Admin-Werkzeuge) |
| **Bot-Setup → Willkommen & Regeln** | Willkommensnachricht, Guten-Morgen-Nachricht, Verweis auf die Reaktionsrollen |
| **Bot-Setup → Giveaway starten** | Giveaway anlegen |
| **Bot-Setup → Reaktionsrollen** | Regeln „Emoji auf Nachricht → Rolle" (neu 03.09.2026) |
| **Bot-Setup → Verdiente Rollen** | Regeln „Bedingung erfüllt → Rolle anbieten" (neu 03.09.2026) |
| **Bot-Setup → Personality** | Haupt-Settings, Text-Varianten, Mood-Schwellen |
| **Admin-Werkzeuge** | Benutzerverwaltung, Rollen-Zuordnung, Audit-Log, App-Zugänge — Kernsystem, kein Modul |

**Umbenennung und Umsortierung (02.09.2026):** „Server-Setup" heißt jetzt **Bot-Setup**,
weil dort das Verhalten des *Bots* eingerichtet wird und nicht der Discord-Server.
Personality gehört sachlich dorthin und stand vorher als eigener Punkt daneben. Embed und
DM sind in der Praxis Moderationsmittel (Ansagen, Verwarnungen) und keine Bot-Verwaltung —
sie liegen deshalb unter Moderation. Unter Admin-Werkzeugen stehen jetzt nur noch
Benutzerverwaltung, Audit-Log und App-Zugänge, die vorher einzeln die Hauptleiste
verbreiterten.

---

## 8. Rollenvergabe (neu 03.09.2026)

Der Bot kann Discord-Rollen auf **drei** Wegen vergeben. Sie sind bewusst getrennt und
fassen sich gegenseitig nicht an — jeder merkt sich, was er selbst getan hat.

### Voraussetzung: die Hierarchie

Discord erlaubt einem Bot nur, Rollen **unterhalb seiner eigenen höchsten Rolle** zu
vergeben. Alle drei Wege prüfen das vorher und melden es verständlich, statt an einer
nichtssagenden Ablehnung zu scheitern. In den Auswahllisten im Backend sind zu hohe Rollen
gesperrt. Zum aktuellen Stand siehe Abschnitt „Rollenhierarchie".

### Weg 1 — Reaktionsrollen (`/reaction-roles`)

Wer auf eine bestimmte Nachricht mit einem bestimmten Emoji reagiert, bekommt eine Rolle.
Beliebig viele Regeln, jede mit Kanal, Nachricht, Emoji, Rolle, optionaler Direktnachricht
und dem Schalter, ob das Entfernen der Reaktion die Rolle zurücknimmt.

Bis zum 03.09.2026 gab es genau **eine** solche Regel, fest im Code verdrahtet als vier
Einstellungen. Sie ist beim ersten Start automatisch in die neue Tabelle umgezogen und
verhält sich unverändert. Nebenbei verschwand dabei eine stille Fehlerquelle: Die alte
Fassung suchte Kanal und Rolle über ihren **Namen** — eine Umbenennung hätte die
Rollenvergabe ohne Fehlermeldung abgeschaltet.

Beim Anlegen setzt der Bot die Reaktion selbst unter die Nachricht und prüft, ob es diese
überhaupt gibt.

**Reaktionsrollen sind exklusiv.** Eine Rolle, die über eine Reaktion vergeben wird, lässt
sich weder als verdiente Rolle noch in der Rechte-Zuordnung eintragen — die Auswahllisten
sperren sie mit Begründung, und beide Formulare weisen sie serverseitig ab.

Der Grund ist bei diesem Server konkret: **Die Mitglieder-Rolle ist das Tor.** Wer sie hat,
sieht alle Kanäle; sie entsteht ausschließlich durch das Akzeptieren der Regeln im
Regelkanal. Ein zweiter Vergabeweg wäre per Definition eine Umgehung — jemand bekäme
vollen Zugriff, ohne die Regeln gelesen zu haben.

Die Sperre gilt auch für **abgeschaltete** Regeln. Sonst liesse sie sich aufheben, indem
man die Reaktionsregel kurz deaktiviert, den Eintrag woanders anlegt und sie wieder
einschaltet. Wer eine Rolle wirklich auf mehreren Wegen vergeben will, entfernt die
Reaktionsregel — dann ist die Absicht ausdrücklich.

### Weg 2 — Verdiente Rollen (`/earned-roles`)

Bedingung erfüllt → der Bot meldet sich → die Person holt die Rolle per Knopf ab.

Sechs Bedingungen, alle auf Zahlen gestützt, die der Bot ohnehin führt: richtig
beantwortete Quizfragen, **beantwortete** Quizfragen (Teilnahme statt Treffsicherheit),
Level, XP, eingereichte Fragen, Tage auf dem Server. **Welche
davon zur Auswahl stehen, hängt an den installierten Modulen** — ohne Pflanzenquiz gibt es
keine Quiz-Bedingungen.

Der Abhol-Knopf trägt Regel **und** Nutzer-ID in seiner Kennung: Ohne diese Bindung könnte
sich jeder, der die Ankündigung im Kanal sieht, die Rolle holen. Er ist dauerhaft
(`timeout=None`, feste `custom_id`) und übersteht Neustarts — sonst wäre die Ankündigung
von gestern tot, der Knopf sähe aus wie immer und täte nichts. Beim Klick wird die
Bedingung **erneut** geprüft.

**Rangleiter oder Sammlung — pro Regel einstellbar.** „Rangleiter" nimmt beim Aufstieg die
vorherigen Stufen wieder ab, man trägt also immer nur die höchste. „Sammlung" (Standard)
lässt alle stehen, wie Abzeichen. Betroffen sind nur Regeln **derselben Bedingungsart** —
wer ein Jahr dabei ist, verliert seine Quiz-Auszeichnungen nicht. Und der Bot nimmt nur
weg, was er selbst vergeben hat.

Der Merker in `earned_role_grants` bleibt beim Entfernen bewusst stehen. Würde er gelöscht,
träfe die Bedingung beim nächsten Auslöser wieder zu, die Rolle käme zurück und würde
erneut entfernt — eine Schaukel ohne Ende.

**Höchstens eine Ankündigung pro Auslöser.** Wer schon lange dabei ist, erfüllt beim
ersten Auslöser mehrere Stufen gleichzeitig — am 04.09.2026 im Betrieb aufgefallen: eine
einzige Quizantwort ergab fünf Ankündigungen in zwei Sekunden, und dasselbe hätte jedes
bestehende Mitglied getroffen. Die Rollen werden alle vergeben, angekündigt wird
zusammengefasst: der Text der höchsten Stufe, darunter die übrigen in einer Zeile.

Ein Merker verhindert wiederholte Ankündigungen: Der Auslöser feuert bei jeder Quizantwort.
Vermerkt wird erst **nach** dem Senden — eine Ankündigung, die niemand gesehen hat, als
erledigt zu buchen, wäre der schlechteste Ausgang.

### Weg 3 — Rechte spiegeln (`/roles-mapping`)

Wer im Backend eine Berechtigung bekommt, bekommt die zugeordnete Discord-Rolle. Wird sie
entzogen, geht die Rolle wieder weg. Sofort bei jeder Änderung; für den **Bestand** gibt es
„Jetzt abgleichen" mit vorgeschalteter Vorschau.

**Die Regel, auf die es ankommt: Der Bot nimmt nur weg, was er selbst vergeben hat.**
Dafür `permission_role_grants`. Ohne diesen Merker hätte der erste Abgleich jedem die
Moderator-Rolle entzogen, der sie von Hand bekommen hat — auch dem Besitzer.

Unberührt bleiben: verdiente Rollen und Reaktionsrollen (eigene Zuständigkeiten), Rollen
ohne Zuordnung wie Member und Server Booster, alles über der Bot-Rolle, und der Bot-Owner.

Die Planung (`rollenabgleich.massnahmen()`) ist vom Ausführen getrennt und rein — sie
verändert ihre Eingaben nicht, sonst zeigte die Vorschau je nach Reihenfolge etwas anderes.

### Die Gegenrichtung — eine Rolle bringt Rechte mit

Wege 1 bis 3 vergeben Rollen. Seit dem 03.09.2026 geht es auch andersherum: Eine Rolle
kann eine **Berechtigung im Bot** mitbringen, ohne dass jemand sie einzeln vergibt.

Anlass war „Ideenschmied": Wer sich durch 100 Quizfragen gearbeitet hat, bekommt die Rolle
— und mit ihr das Recht, eigene Fragen einzureichen. Vorher durfte das jedes
Servermitglied.

Eingestellt wird das auf derselben Seite wie die Zuordnung, aber in einem **eigenen
Formular**. Beides zusammenzulegen wäre kürzer, aber man könnte beim Lesen nicht mehr
unterscheiden, welche Richtung gemeint ist.

**`is_admin` lässt sich nicht über eine Rolle vergeben.** Sonst könnte jeder, der die Rolle
in Discord vergeben darf, volle Backend-Rechte verteilen — an der Benutzerverwaltung
vorbei, wo so etwas einzeln und nachvollziehbar passiert.

Technisch: `has_permission()` nimmt seit dem Umbau optional die Rollen-IDs entgegen. Die
Funktion kennt Discord nicht und soll es nicht — wer die Rollen kennt (Backend, Cogs),
reicht sie herein; wer nicht, bekommt weiterhin das Verhalten von vorher.

**Rechte können zu einem Modul gehören.** `submit_questions` gibt es nur mit dem
Pflanzenquiz — ohne das Modul steht es gar nicht erst zur Vergabe, weil es dann nichts
gäbe, wofür es gilt.

### Grundausstattung aus der Rolle, Feinschliff pro Person (05.09.2026)

Berechtigungen entstehen auf **zwei Ebenen**. Die Rolle liefert eine
**Grundausstattung**: Was unter „Rolle bringt Berechtigung mit" eingestellt ist,
bekommt jeder, der die Rolle auf dem Server erhält. Darauf setzt der
**Feinschliff pro Person** — einzelne Rechte lassen sich zusätzlich vergeben oder
gezielt entziehen, und diese Abweichung bleibt bestehen.

Patrick am 05.09.2026: „wenn man auf dem server zum Moderator gemacht wird hat
man erstmal alle Moderatoren rechte die über die rechteverwaltung eingestellt
wurden aber man kann auch einzelne rechte vergeben und entziehen." Der Anlass
davor: „es gibt moderatoren die sich um den schimpfwortfilter kümmern oder welche
die kicken können.. manche können beides andere moderatoren können was anderes."

Die Grundausstattung der Moderator-Rolle sind vier Rechte:
`manage_badword_filter`, `manage_categories`, `moderate_members`,
`verify_questions`. `submit_questions` gehört nicht dazu — das ist das Recht des
**Ideenschmieds**, und diese Rolle bringt es weiterhin allein mit.

#### Bewusst entzogene Rechte

Der Abgleich vergab bis zu diesem Tag stur alles, was die Rolle mitbringt und die
Person noch nicht hat. Ein gezielter Entzug war damit beim nächsten Lauf wieder
rückgängig gemacht, und die zweite Ebene existierte praktisch nicht.

Dafür gibt es in `user_permissions` die Spalte **`ausnahme`**. Eine Zeile mit
`ausnahme = 1` heißt: Die Person hat dieses Recht nicht, obwohl ihre Rolle es
hergäbe — und der Abgleich zieht es nicht nach. Bewusst eine Spalte und keine
zweite Tabelle, aus demselben Grund wie bei `granted_by`: Zwei Tabellen könnten
auseinanderlaufen, und dann wüsste niemand, welche recht hat.

| Was geschieht | Ergebnis |
|---|---|
| Entzug in der Benutzerverwaltung oder über `/revoke` | `ausnahme = 1`, das Recht kommt nicht zurück |
| Abwählen im Formular auf `/users` | dasselbe — jedes entfernte Häkchen wird zur Ausnahme |
| Erneutes Vergeben | `ausnahme = 0`, das Recht gilt wieder |
| Der Abgleich räumt auf (`bewusst=False`) | Zeile wird gelöscht, **keine** Ausnahme |
| Die Rolle fällt ganz weg | Ausnahmen werden mit weggeräumt |

Der letzte Fall ist wichtig: Ohne die Rolle gäbe es das Recht ohnehin nicht, die
Ausnahme bedeutet also nichts mehr. Bliebe sie stehen, würde eine Entscheidung
von damals die Person auch dann noch einschränken, wenn sie die Rolle irgendwann
neu bekommt — ohne dass irgendwo steht warum.

Dass der Abgleich selbst keine Ausnahmen erzeugt, ist der zweite wichtige Punkt.
Er räumt nur auf, was keine Grundlage mehr hat; daraus eine dauerhafte Sperre zu
machen wäre falsch.

**Sichtbar ist das in der Benutzerverwaltung:** Ein bewusst entzogenes Recht
steht durchgestrichen neben den vorhandenen, mit `↺` zum Wiedervergeben. Ohne
diese Anzeige wäre die Funktion schlimmer als ihr Fehlen — wer sich wundert,
warum ein Moderator etwas nicht darf, fände die Rolle korrekt gesetzt und suchte
den Fehler im Code.

#### Was ein einzeln vergebenes Recht am Leben hält

Fast jedes Recht wird von *irgendeiner* Rolle geführt, und die Regel vom
Vormittag lautet: Ein rollengebundenes Recht gilt nur, solange eine Rolle es
deckt. Ein zusätzlich vergebenes Recht wäre damit beim nächsten Abgleich weg.

Geschützt ist deshalb, was ein Mensch eingetragen hat **und** dessen zugeordnete
Rolle die Person gerade trägt. Wer `send_dm` bekommt, erhält die
Moderator-Auszeichnung, und solange er sie trägt, bleibt das Recht. Dass dabei
nicht nur vom Bot gesetzte Rollen zählen, ist Absicht: doc_loomis und sven tragen
die Moderator-Rolle, seit ein Mensch sie ihnen gab.

#### Vier Fehler, die zusammen eine Berechtigung vernichteten (05.09.2026)

Patrick gab jemandem nachträglich das Recht „Giveaway starten" — danach war die
Person aus der Benutzerverwaltung verschwunden, mitsamt ihrem bisherigen Recht.
Der Audit-Log zeigt die Kette in zwei Sekunden:

```
19:34:22  set_user_permissions   create_giveaway
19:34:22  revoke_permission      create_giveaway
19:34:23  revoke_permission      manage_badword_filter
19:34:23  rechtesync             verliert: create_giveaway
19:34:23  rollen_angeglichen     -Moderator
```

Vier Ursachen wirkten zusammen:

**1. Das Formular ersetzte, statt zu ergänzen.** Die Häkchen auf `/users` waren
nie mit den vorhandenen Rechten vorbelegt, das Speichern setzte aber „genau
diese". Wer ein Recht nachtrug, löschte damit stillschweigend alle anderen. Das
Formular **ergänzt** jetzt; entzogen wird gezielt über das ✕ am einzelnen Recht.

**2. Ein Recht ohne zugeordnete Rolle vernichtete sich selbst.**
`create_giveaway` stand in keiner Zuordnung. Wer es bekam, löste damit keine
Rolle aus, verlor also die Moderator-Auszeichnung — und weil die Manager-Rolle
dasselbe Recht führt, galt es als rollenverwaltet und wurde sofort wieder
entzogen. Ein Recht, das keine Rolle repräsentiert, kann auch an keiner hängen;
es zählt jetzt als reine Einzelvergabe und bleibt unangetastet.

**3. Modul-Rechte fehlten in der Benutzerverwaltung.** `submit_questions` kommt
aus dem Quizmodul und stand deshalb weder in `ALL_PERMISSIONS` noch in
`PERMISSION_LABELS`. In der Liste stand der rohe Schlüssel, das ✕ daneben tat
nichts, und vergeben ließ es sich über die Seite gar nicht. Die Seite benutzt
jetzt `alle_berechtigungen()` und die zusammengeführten Bezeichnungen.

**4. Der Start glich nur eine Richtung ab.** `on_ready` zog ausschließlich
`Rolle → Recht` nach. Eine Unstimmigkeit löste der Bot damit immer zerstörend
auf: Wer ein Recht hatte, dessen Rolle fehlte, verlor das Recht — statt die
Rolle zu bekommen. Seit dem 05.09. läuft **zuerst** `Recht → Rolle`
(`Rechtesync.rollen_nachziehen`), und erst danach die Ableitung.

Dieser Startlauf **vergibt nur**, er entzieht keine Rollen. Das Entziehen läuft
im Backend bei jeder Rechteänderung und über „Bestand angleichen" — beim Start
eine Rolle wegzunehmen, die vielleicht gerade von Hand vergeben wurde, wäre
derselbe Fehler in Grün.

#### Der Merker steht vor der Vergabe

Beim Reparieren fiel ein Wettlauf auf, sichtbar im Log:

```
21:43:40  synthek_flo (Rollenänderung): +manage_categories, +moderate_members, +verify_questions
21:43:40  synthek_flo: +Moderator (Recht vorhanden, Rolle fehlte)
21:43:41  synthek_flo (Abgleich nach Neustart): -manage_categories, -moderate_members, -verify_questions
```

Discord meldet eine Rollenänderung sofort zurück. Stand `merke_rollenvergabe()`
hinter `add_roles()`, lief `on_member_update` los, bevor die Zeile geschrieben
war — der Listener hielt die frisch vergebene Rolle für von Hand verliehen und
leitete ihre ganze Grundausstattung ab. Eine Sekunde später nahm der
Startabgleich alles wieder weg. Das Ergebnis stimmte, der Weg dorthin nicht.

Der Merker wird deshalb **vor** der Vergabe geschrieben und bei einem Fehlschlag
wieder entfernt — in `rechtesync.py` wie in `app.py`.

#### Zeitstempel: gespeichert in UTC, angezeigt in Ortszeit (06.09.2026)

SQLite schreibt mit `datetime('now')` **UTC**. Alle sieben Zeitspalten der
Datenbank entstehen so: `timestamp`, `granted_at`, `created_at`, `unlocked_at`,
`vergeben_at`, `added_at`, `updated_at`.

Angezeigt wurden sie roh — im Sommer also zwei Stunden neben der Systemuhr. Beim
Suchen eines Fehlers am 06.09.2026 zeigte der Audit-Log für ein Ereignis 21:29,
das Systemlog 23:29; es sah nach zwei verschiedenen Vorgängen aus und kostete
Zeit, bis der Groschen fiel.

Gespeichert bleibt UTC — das ist eindeutig und übersteht jeden Wechsel der
Zeitzone. Umgerechnet wird erst bei der Anzeige, über den Jinja-Filter
`lokalzeit` in `backend/app.py`. Ein unbekanntes Format wird bewusst **roh
durchgereicht** statt verschluckt: Eine leere Zelle erklärt nichts.

`tests/test_zeitanzeige.py` durchsucht alle Vorlagen nach Ausgaben dieser sieben
Spalten und schlägt an, sobald eine ohne `| lokalzeit` auskommt. Damit fällt eine
neue Seite auf, die den Fehler wiederholt.

#### Beide Richtungen, für jede Rolle

„wenn ich im bot ein recht gebe muss es auf dem server auch konsistent gesetzt
und entzogen werden wenn ich es im Bot wieder entferne.. genauso muss es
andersrum auch laufen." Das gilt nicht nur für Moderator. Für den Ideenschmied
heißt es: Wer `submit_questions` im Backend bekommt, erhält die Rolle auf dem
Server; wer die Rolle verliert, verliert das Recht.

**`send_dm` und `create_embed` sind Admin-Funktionen.** Mit ihnen tritt man im
Namen des Bots an Mitglieder heran, deshalb liegen sie standardmäßig beim
Besitzer und gehören nicht zur Grundausstattung. Weitergegeben werden sie einzeln
im Backend — und sie lösen die Moderator-Auszeichnung aus, damit erkennbar
bleibt, wer im Namen des Bots sprechen kann.

### Die Übersicht „Wie Rollen vergeben werden" (05.09.2026)

Vier Wege vergeben Rollen, ein fünfter vergibt Rechte direkt an eine Person, und alle
werden an unterschiedlichen Stellen eingestellt. Jede Stelle für sich ist verständlich;
zusammen ergaben sie einen Zustand, den niemand mehr überblickte. Patrick am 04.09.2026:
„ich kann an mehreren stellen im bot rechte und rollendinge einstellen die irgendwie
unabhängig voneinander laufen."

Oben auf `/roles-mapping` steht deshalb eine Übersicht: eine Zeile je Rolle, die überhaupt
irgendwo vorkommt, mit allen Wegen, die sie betreffen, und den Rechten, die sie mitbringt.
Rollen ohne jeden Eintrag bleiben weg — der Server hat siebzehn davon, und leere Zeilen
hätten die wenigen interessanten versteckt.

Darüber stehen die **Befunde**, also die Stellen, an denen sich zwei Wege widersprechen.
Sechs Arten werden geprüft:

| Befund | Schwere | Was er bedeutet |
|---|---|---|
| Rückweg fehlt | hoch / mittel | Ein Recht holt eine Rolle, die dieses Recht nicht mitbringt |
| `is_admin` über eine Rolle | hoch | Wer die Rolle vergeben darf, verteilt volle Backend-Rechte |
| Rolle über dem Bot | hoch | Die Zuordnung ist eingestellt, greift aber nie |
| Rolle nicht mehr vorhanden | mittel | Der Eintrag zeigt ins Leere |
| Mehrere Vergabewege | mittel | Die Wege wissen nichts voneinander und überschreiben sich |
| Kette | Hinweis | Eine automatisch vergebene Rolle bringt Backend-Rechte mit |

Der erste Befund ist der Grund, warum es die Seite gibt. Er trat beim ersten Lauf sofort
auf: `send_dm` zeigte auf die Moderator-Rolle, die Moderator-Rolle brachte `send_dm` aber
nicht mit. Wer das Recht bekam, erhielt die Rolle — und beim nächsten Rechteabgleich nahm
der Bot das Recht wieder weg, weil keine der Rollen es deckte. Übrig blieb eine Rolle ohne
die passende Berechtigung, also genau der Zustand, den die ganze Umstellung ausschließen
sollte. Ohne Übersicht fällt so etwas erst auf, wenn sich jemand beschwert.

Die Schwere unterscheidet dabei, ob der Widerspruch im Betrieb wirklich zuschlägt: Nur
wenn das Recht an **irgendeiner** Rolle hängt, holt der Abgleich es zurück. Hängt es an
keiner, bleibt es bestehen und die Meldung ist eine Stufe niedriger.

Die Logik steht in `bot/vergabewege.py` und ist bewusst rein rechnend — keine Datenbank,
kein discord.py, kein Schreibzugriff. Eine Warnung kann dadurch nie versehentlich etwas
verändern, und die 40 Tests in `tests/test_vergabewege.py` laufen ohne Server und ohne
Discord-Verbindung.

### Was die Benutzerverwaltung zeigt

`/users` stellt Backend-Rechte und tatsächliche Discord-Rollen gegenüber und unterscheidet
dabei drei Fälle: **fehlt** (der Bot kann es), **nur von Hand** (Rolle steht über ihm) und
**ohne passendes Recht**. Der letzte Fall ist ausdrücklich kein Vorwurf — eine Rolle kann
aus ganz anderen Gründen vergeben worden sein. Darunter eine zweite Tabelle für Personen,
die eine zugeordnete Rolle tragen, ohne im Backend zu stehen; die tauchen in der Hauptliste
gar nicht auf.

---

### Modultrennung (Grundsatz seit 03.09.2026)

Patrick verfolgt das Ziel, einzelne Funktionsbereiche später als kostenpflichtige Module
anzubieten. Jedes Modul muss sich deshalb **einzeln installieren lassen**, und für jede
Änderung gilt: Erst klären, zu welchem Modul eine Funktion gehört, dann dort ablegen.

**Ein Modul ist eine Datei** und bringt alles mit, was nur es betrifft:

| Datei | Rolle |
|---|---|
| `bot/module.py` | Die Registratur — Schema anwenden, Ereignis-Kontext und Übersichtsbeiträge einsammeln. Kennt kein einziges Modul beim Namen. |
| `bot/modul_quiz.py` | Pflanzenquiz: fünf Tabellen, Migrationen, sechs Achievements, zwei Bedingungen, 25 Abfragefunktionen. |
| `bot/modul_xp_level.py` | XP & Level: `user_xp`, zwei Achievements, zwei Bedingungen, `award_xp()` und die Levelberechnung. |
| `bot/modul_kern.py` | Was ohne Zusatzmodul funktioniert. Kein eigenes Schema — die gemeinsamen Tabellen bleiben zentral. |
| `bot/achievements.py`, `bot/verdiente_rollen.py` | Nur Mechanik: Registratur, Datenklassen, Kandidatensuche. Kennen keine Inhalte. |
| `bot/database.py` | Nur noch Kern: Einstellungen, Audit-Log, Berechtigungen, Rollenregeln. **Kein SQL mehr auf eine Modultabelle.** |
| `bot/schema_helfer.py` | `ensure_column()`, bewusst ohne jede Abhängigkeit ausser `sqlite3`. |

**`module.INSTALLIERT`** sagt an einer Stelle, was aktiv ist. Sobald Module tatsächlich
einzeln verkauft werden, kommt die Liste aus der Konfiguration.

### Die Datenbank baut sich selbst auf

Nachgewiesen mit einer Installation „nur Kern": Dann entstehen `questions`, `players`,
`user_stats`, `user_xp` und `categories` gar nicht erst, es gibt keine Achievements, und
als Bedingung für verdiente Rollen steht nur „Tage auf dem Server" zur Auswahl.

Kommt das Quizmodul später dazu, legt der **nächste Start** die fünf Tabellen an, führt die
Migrationen aus (`questions.category_id`, die Streak-Spalten, der Index) und trägt die
sechs Achievements ein. Bestehende Daten bleiben unberührt.

Genau daran ist der erste Versuch gescheitert und hat dabei einen zweiten Riss offengelegt:
`init_db()` enthielt Migrationen wie `_ensure_column(conn, "questions", …)`, die ohne das
Modul auf Tabellen zugriffen, die es nicht gibt. Migrationen gehören deshalb ebenfalls zum
Modul.

### Drei Entwurfsentscheidungen

**Die Bedingungen sind reine Funktionen** über ein Kontext-Dictionary, ohne
Datenbankzugriff. Vorher zählte die Quiz-Bedingung als Nebenwirkung einen Zähler hoch —
eine Prüfung veränderte also Daten, was beim Lesen niemand erwartet.

**Jede Bedingung bringt ihre eigene Abfrage mit.** Ein zentraler Sammler hätte `user_stats`
und `questions` gekannt, also Tabellen aus einem Modul, das fehlen kann.

**Die Moduldateien laden sich ohne die Laufzeitumgebung des Bots.** Sie holen `database`
und `config` erst beim Aufruf über `_conn()`, `_db()` und `_cfg()`, nie auf oberster Ebene.
Sonst wäre schon das Beschreiben eines Moduls an `dotenv` und einen gesetzten
`DISCORD_TOKEN` gebunden — woran die CI gescheitert ist, während lokal alles grün war. Zwei
Tests bewachen das.

### Zwei Sonderfälle

`get_user_overview()` fragte drei Tabellen aus zwei Modulen direkt ab. Jetzt tragen die
Module bei (`Modul.nutzer_uebersicht`), der Kern führt nur zusammen, und jedes Modul liefert
Standardwerte mit — damit die Seite ohne ein Modul nicht jeden Wert einzeln absichern muss.

`check_and_update_milestone()` stützt sich auf richtige Quizantworten. Ohne das Quizmodul
gibt es solche Meilensteine schlicht nicht — die Funktion gibt dann `None` zurück, statt zu
scheitern.

Abgesichert durch `tests/test_module.py` und `tests/test_achievements_module.py`.

---

## 9. Sicherheitsfundament

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

## 10. Personality/Mood/Events-System

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

## 11. Bot Connect App (Android) + Bot Connect Dashboard

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

## 12. OAuth-Setup

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

## 13. Start / Stop / Logs

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

## 14. Umgesetzte Sicherheitsmaßnahmen

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

## 15. Unterschiede zum alten Bot

Unverändert gegenüber der Vorversion (siehe dort für die volle Liste: Cogs statt
Monolith, Hybrid-Commands, Buttons statt Chattext, getrenntes XP-System,
DB-persistierte Giveaways, OAuth statt eigenem Login, granulare Flags, Backup+Audit-Log,
Mood-/Personality-System, ein Prozess statt zwei, waitress+nginx). **Zusätzlich neu:**
Git-Versionsverlauf (vorher keiner), Android-App + lokales Dashboard statt reinem
Termux/CLI-Zugriff, modernisierter Schimpfwortfilter (Homoglyphen/Teil-Obfuskation statt
reiner Wortliste).

---

## 16. Den Bot erweitern

Unverändert gegenüber der Vorversion (neues Cog in `bot/cogs/`, in `load_cogs()`
eintragen; neue Settings über `config.DEFAULT_SETTINGS` + `database.get_setting()`/
`set_setting()`; neuer Laufzeit-Zustand über `database.get_bot_state()`/`set_bot_state()`).
**Für ein neues Modul im Menüsinn** (Abschnitt 7): eigener Navbar-Punkt oder Sub-Nav nach
dem Muster von `/games/quiz` (`_quiz_subnav.html`) bzw. `/moderation`
(`_moderation_subnav.html`), inkl. der `with context`-Lektion bei Jinja-Makro-Imports
(Makros erben den Template-Kontext beim Import standardmäßig NICHT — `has_permission()`
wäre sonst `undefined`).

---

## 17. Testsuite (`tests/`, 972 Tests)

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
| `test_verdiente_rollen.py` | 66 | Verdiente Rollen: Schwellen, Abhol-Knopf (an eine Person gebunden, übersteht Neustarts) und der Merker gegen wiederholte Ankündigungen |
| `test_reaktionsrollen.py` | 20 | Reaktionsrollen: Schema-Zusagen, Umzug der alten Regel, und dass Kanal und Rolle über IDs statt Namen angesprochen werden |
| `test_rollenrechte.py` | 15 | Eine Rolle bringt Rechte mit: getrennte Tabellen für beide Richtungen, kein `is_admin` über Rollen, Befehl **und** Webformular abgesichert |
| `test_rollenabgleich.py` | 31 | Abgleich Backend-Rechte gegen Discord-Rollen; insbesondere dass nicht zugeordnete Rollen nie als überzählig gelten und dass eine vom Bot vergebene Rolle mit dem letzten zugeordneten Recht wieder verschwindet |
| `test_nutzertexte.py` | 109 | Oberflächentexte enthalten keine Entwicklerhistorie, keinen Jargon und keine Verweise auf das alte Befehlspräfix |
| `test_csrf_coverage.py` | 81 | Jedes POST-Formular und jeder `fetch`-POST trägt ein CSRF-Token — auf **beiden** zulässigen Wegen (verstecktes Feld oder `X-CSRF-Token`-Header) |
| `test_testbox.py` | 41 | Dass die Testbox beide Filter prüft und dabei **nichts** verändert — die Route wird auf `log_badword_violation`, `delete` und `set_setting` durchsucht — dazu die Wortstufen und dass eine erlaubte Wendung sich nicht zum Durchschmuggeln missbrauchen lässt |
| `test_tunnel_access.py` | 14 | Widerruf von Iroh-Zugängen, insbesondere das Fail-Open-Verhalten bei fehlendem sudo |
| `test_moderation_limits.py` | 33 | Timeout-Deckelung auf 28 Tage, DM-Länge, SQL-Spalten-Whitelists |
| `test_linkfilter.py` | 33 | Die Reihenfolge der Linkfilter-Regeln — insbesondere dass die Sperrliste vor der Erlaubtliste greift, dass Unterdomains eingeschlossen sind und `notexample.com` nicht unter `example.com` fällt |
| `test_moderationsmeldung.py` | 17 | Dass Kick, Bann, Entbannung und Timeout gemeldet werden — erst nach dem Erfolg, und dass ein Fehlschlag beim Melden die Aktion nicht abbricht |
| `test_moderation_hierarchy.py` | 7 | Rollenhierarchie als eigene Schranke neben den Berechtigungen |
| `test_android_build.py` | 4 | Der `cargo update`-Schritt im Build-Workflow, ohne den die APK gegen die kaputte iroh-Version gebaut wird |
| `test_hilfe.py` | 39 | Rechtefilterung in `/help` mit gestellten Berechtigungen, Modulgliederung, Übergangshinweis für `>` |
| `test_achievements_konfiguration.py` | 38 | Einstellbare Achievements: Schwellen und Zeitspannen samt ihrer Grenzen, unbrauchbare Werte aus dem Backend, und dass nur selbst angelegte gelöscht werden können |
| `test_rechteabgleich.py` | 69 | Die Gegenrichtung Rolle → Recht: dass `is_admin` nie über eine Rolle läuft, der Besitzer nie beschnitten wird, dass eine Rolle, die der Bot wegen eines Rechts vergeben hat, nicht zurückwirkt — und dass die Grundausstattung einer Rolle greift, ein bewusst entzogenes Recht aber nicht nachgezogen wird |
| `test_vergabewege.py` | 42 | Die fünf Vergabewege nebeneinander: dass ein Recht, das eine Rolle holt, von dieser Rolle auch zurückkommt, dass `is_admin` nie über eine Rolle läuft, dass Einträge auf gelöschte oder für den Bot unerreichbare Rollen auffallen und dass eine automatisch vergebene Rolle mit Backend-Rechten sichtbar wird |
| `test_zeitanzeige.py` | 5 | Dass jeder angezeigte Zeitstempel durch den `lokalzeit`-Filter geht — SQLite schreibt UTC, und ein Audit-Log, das zwei Stunden neben der Systemuhr liegt, lässt eine Fehlersuche ins Leere laufen |
| `test_verstoesse.py` | 18 | Verstöße zurücksetzen (Zählung fällt, Historie bleibt) und endgültig löschen; insbesondere, dass die Reset-Grenze beim Löschen mit aufgeräumt wird — sonst würden spätere Verstöße stillschweigend nicht mehr zählen |
| `test_interaktion.py` | 51 | Discords Drei-Sekunden-Frist: die Vorab-Bestätigung je Befehl, und eine Wache, dass die angegebene Antwortart zu dem passt, was der Befehl tatsächlich sendet |
| `test_automod_seite.py` | 17 | Dass die vier Reiter der Automod-Seite vollständig sind, keine Karte beim Umbau verlorenging und jede in genau einem Reiter liegt |
| `test_befehlsfehler.py` | 27 | Jeder absehbare Befehlsfehler sagt etwas; Prüfungen antworten nicht mehr selbst |
| `test_achievements_module.py` | 43 | Modultrennung der Achievements und alle Auslöseschwellen; prüft ausdrücklich, dass `database.py` keinen Achievement-Namen mehr kennt |
| `test_morning_greeting.py` | 38 | Zeitlogik der täglichen Guten-Morgen-Nachricht: Fenster, Tagesmerker gegen doppelte Grüße nach einem Neustart, Randuhrzeiten um Mitternacht, unbrauchbare Eingaben |
| `test_doku.py` | 4 | Die Testzahlen in dieser Tabelle stimmen mit der Wirklichkeit überein — eine Doku mit falschen Zahlen ist schlimmer als eine ohne |
| `test_module.py` | 26 | Module bringen ihr Schema selbst mit: ohne Modul keine Tabellen, nachträgliche Installation legt sie an, bestehende Daten bleiben unberührt |
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

### Linkfilter: die Sperrliste (06.09.2026)

Aufgabe (Patrick, 04.09.2026): „der Linkfilter sollte nicht nur white sondern
auch blacklisting können und mehrere einstellungen haben."

Bis dahin gab es drei Regeln — alles blockieren, Erlaubtliste, nur neue
Mitglieder — aber keine Möglichkeit, **eine einzelne Domain** zu verbieten, ohne
gleich alle Links zu sperren. `grep -n "blacklist" bot/ backend/` fand keinen
Treffer.

#### Die Reihenfolge der Regeln

Zuerst greifen die **Ausnahmen für Rollen und Kanäle**: Sie sagen „diese Person"
oder „dieser Kanal" ist außen vor, und das ist eine Frage des Kontexts, nicht des
Links. Danach entscheidet `bot/linkfilter.py` in dieser Reihenfolge:

| # | Regel | Wirkung |
|---|---|---|
| 1 | **Sperrliste** | Die Domain ist gesperrt — auch wenn sonst alles erlaubt wäre |
| 2 | Alles blockieren | Jeder Link ist ein Verstoß |
| 3 | Erlaubtliste | Ein Verstoß, außer die Domain steht darauf |
| 4 | Neue Mitglieder | Ein Verstoß, wenn der Autor noch nicht lange dabei ist |

**Die Sperrliste steht bewusst vorn.** Wer eine Domain ausdrücklich verbietet,
meint es. Stünde die Erlaubtliste davor, wäre die Sperre wirkungslos, sobald
jemand dieselbe Domain versehentlich in beide Listen einträgt.

Regel 4 gilt unabhängig von der Domain — sonst könnten neue Mitglieder über eine
erlaubte Domain doch Links posten, und die Regel wäre keine.

#### Schreibweisen

`example.com` deckt auch alle Unterdomains, also `cdn.example.com`. Die
umgekehrte Richtung gilt nicht. Geprüft wird dabei auf den Punkt, nicht auf den
bloßen Textanfang: `notexample.com` fällt **nicht** unter `example.com`.

`*.example.com` wird angenommen und auf `example.com` reduziert. Die
Schreibweise liegt nahe, und ein Eintrag, der stillschweigend nie greift, ist bei
einer Sperrliste das Schlimmste, was passieren kann. Auch eine ganze Adresse
(`https://www.example.com/pfad`) wird beim Eintragen auf die Domain reduziert.

#### Warum ein eigenes Modul

Die Entscheidung steckte vorher mitten im Cog, verwoben mit dem Löschen der
Nachricht, dem Verwarnen und dem Timeout — prüfbar nur, indem man den Bot
startete und eine Nachricht schrieb. In `bot/linkfilter.py` steht jetzt nur das
Urteil: kein discord.py, keine Datenbank, kein Schreibzugriff.

Das zahlt sich zweimal aus. Die 33 Tests in `tests/test_linkfilter.py` laufen
ohne Server, und die für Teil 2 geplante **Testbox im Backend** („Text eingeben,
sehen ob der Filter anschlägt und warum") kann dieselbe Entscheidung benutzen,
ohne dabei etwas zu löschen. `Entscheidung.begruendung` liefert den Satz dazu.

Im Chat bekommt der Betroffene die Regel **nicht** genannt — das wäre eine
Anleitung zum Umgehen. Die Begründung ist für das Backend gedacht.


### Die Automod-Seite hat Reiter (06.09.2026)

Die Seite trug vier Filter, ihre Einstellungen, die Wortliste, das Zurücksetzen
**und** die Verstoßhistorie auf einer einzigen Seite — über vierhundert Zeilen
Formular am Stück. Dass Patrick eine vorhandene Einstellung übersah
(`link_filter_new_member_days`, „wie lange zählt jemand als neuer User"), war
dafür ein gutes Indiz.

Jetzt gibt es vier Reiter: **Schimpfwörter**, **Links**, **Spam**, **Verstöße**.
Einstellungen und zugehörige Listen liegen jeweils beisammen — der
Schimpfwortfilter mit seiner Wortliste, der Linkfilter mit Erlaubt- und
Sperrliste, das Zurücksetzen mit der Historie. Wer eine Zählung zurückgesetzt
hat, will danach sehen, was noch dasteht.

Der zuletzt geöffnete Reiter wird im Browser gemerkt. Ohne das landet man nach
jeder Änderung wieder beim ersten — jede Aktion leitet auf dieselbe Seite
zurück. Der Zugriff auf den Speicher ist abgesichert: In privaten Fenstern und
bei blockierten Site-Daten wirft er, und ohne `try`/`catch` bliebe die ganze
Seite ohne Javascript.

`tests/test_automod_seite.py` prüft, dass jede der sechs Karten noch da ist und
in **genau einem** Reiter liegt — sonst steht beim nächsten Umbau etwas doppelt
oder fällt heraus.

### Schimpfwortfilter: Testbox und Wortliste (06.09.2026)

#### Die Testbox

Bis dahin sah niemand, was die Filter tun — erst wenn sich jemand beschwerte,
merkte man, dass ein Wort zu scharf eingestellt war. Über den Reitern auf
`/moderation` steht jetzt **„🧪 Filter ausprobieren"**: Text eingeben, und beide
Filter melden mit den aktuellen Einstellungen, ob und warum sie anschlagen.

Es ist ein **Trockenlauf**. Nichts wird gelöscht, nichts gezählt, niemand
verwarnt — `tests/test_testbox.py` prüft das ausdrücklich, indem es die Route
nach `log_badword_violation`, `message.delete` und `set_setting` durchsucht. Ein
Probelauf, der Verstöße zählt, wäre schlimmer als keiner.

Beim Linkfilter wird der **ungünstigste Fall** angenommen: Ist die Regel für
neue Mitglieder aktiv, tut die Box so, als wäre der Schreiber neu. Sonst sähe
man nie, ob die Regel greift. Rollen- und Kanal-Ausnahmen bleiben außen vor —
sie hängen an einer Person und einem Ort, nicht am Text.

Damit die Box überhaupt möglich war, musste die Worterkennung aus
`cogs/moderation.py` heraus. Sie liegt jetzt in `bot/wortfilter.py`, nach
demselben Muster wie `linkfilter.py`: kein discord.py, keine Datenbank, kein
Schreibzugriff. Der Bot importiert von dort, das Backend ebenso — beide urteilen
also garantiert gleich.

#### Wortstufen

Bis zum 06.09.2026 war jedes Wort gleich schlimm: Ein derber Fluch löste
denselben Timeout aus wie eine Beleidigung, die einen Rauswurf rechtfertigt.
Patrick auf die Frage, ob Stufen den Pflegeaufwand wert sind: ja, mit je eigener
Reaktion.

Jedes Wort trägt jetzt eine Stufe — 🟢 **leicht**, 🟡 **mittel**, 🔴 **schwer** —
und je Stufe wird eingestellt, was passiert: nur löschen, löschen + verwarnen,
oder zusätzlich Timeout ab der Verstoß-Schwelle. Die Stufe steht direkt neben dem
Wort in der Liste und speichert beim Umstellen sofort.

**Der Umstieg ändert für kein einziges Wort etwas.** Alle 267 vorhandenen Wörter
bekamen „mittel", und `database._migriere_wortstufen()` setzt die Reaktion für
„mittel" einmalig auf den bisherigen Wert von `badword_filter_action`. Bei einem
Filter, der Nachrichten löscht und Leute stummschaltet, ist eine stille
Verhaltensänderung das Letzte, was man will. `badword_filter_action` bleibt als
Migrationsquelle stehen, so wie `link_filter_mode` beim Linkfilter.

##### Wie die 267 Wörter eingeteilt sind

| Stufe | Wörter | Maßstab | Reaktion |
|---|---:|---|---|
| 🟢 leicht | 79 | mildes Fluchen ohne Adressat, harmlose Neckerei, anatomische Begriffe | nur löschen |
| 🟡 mittel | 143 | gezielte Beleidigungen, derbe Vulgärsprache | löschen + verwarnen + Timeout |
| 🔴 schwer | 45 | trifft eine Person für das, was sie **ist** — Herkunft, Hautfarbe, sexuelle Orientierung, Geschlecht, Behinderung; dazu sexualisierte Gewalt | löschen + verwarnen + Timeout |

Die Einteilung liegt nachlesbar in `~/Desktop/pläne/wortlisten/stufen.py`.
Verschleierte Schreibweisen (`f0tze`, `hu$o`) stehen bei ihrer Grundform.

##### Das Punktesystem

Bis zum 06.09.2026 zählte der Filter nur, **wie oft** jemand angeeckt ist. Drei
„scheisse" wogen damit genauso schwer wie drei rassistische Beschimpfungen.
Patrick: „man soll die Punkte einstellen können. machen wir 5. ein schwerer
verstoß gibt 3 Punkte Mittel 2 und leicht 1 Punkt."

| Stufe | Punkte | Sichtbare Reaktion |
|---|---:|---|
| 🟢 leicht | 1 | nur löschen |
| 🟡 mittel | 2 | löschen + Verwarnung |
| 🔴 schwer | 3 | löschen + Verwarnung |

**Timeout ab 5 Punkten**, für 10 Minuten. Alle vier Werte sind im Backend
einstellbar; 0 Punkte sind erlaubt und bedeuten „zählt gar nicht mit".

Wie sich das auswirkt:

| Verlauf | Konto | |
|---|---|---|
| schwer, schwer | 3 → 6 | Timeout schon beim **zweiten** |
| mittel, mittel, leicht | 2 → 4 → 5 | Timeout beim dritten |
| leicht ×5 | 1 → 2 → 3 → 4 → 5 | Timeout beim fünften |
| schwer, mittel | 3 → 5 | Timeout beim zweiten |

**Der Timeout hängt am Konto, nicht an der Reaktion der einzelnen Stufe.** Auch
ein leichter Verstoß, der nur gelöscht wird, bringt seinen Punkt und kann das
Fass zum Überlaufen bringen. Die Prüfung steht deshalb hinter der Warnung, aber
**vor** jedem vorzeitigen Ausstieg — genau das sichert ein Test ab, weil es beim
ersten Anlauf falsch herum stand.

Die Stufe wandert dafür in die Verstoß-Historie (`badword_violations.stufe`).
Verstöße von vor der Umstellung haben keine und zählen als „mittel" — das war
bis dahin die einzige Einstufung. Punkte folgen demselben Zeitfenster wie die
Zählung: dieselbe 30-Tage-Verjährung, derselbe manuelle Reset. Sonst räumte ein
Reset die Verstöße weg, während das Punktekonto stehen bliebe.

Die Timeout-Meldung im Kanal nennt jetzt **Punkte** statt Verstöße — sonst
wundert sich jemand, warum er nach zwei Nachrichten stumm ist.

Die frühere Einstellung „Verstöße bis Timeout" (`badword_timeout_threshold`, 3)
ist damit gegenstandslos und wurde am 06.09.2026 entfernt — aus der Oberfläche,
aus den Voreinstellungen und aus der Datenbank. Ein Feld, an dem man dreht ohne
dass etwas passiert, ist schlimmer als keines. Der **Linkfilter** behält seine
eigene Schwelle (`link_filter_timeout_threshold`); er zählt weiterhin Verstöße,
nicht Punkte.

#### Erlaubte Wendungen

Die zweite benannte Schwäche war der fehlende Kontext. Dafür gibt es jetzt
`badword_ausnahmen`: ganze Wendungen, innerhalb derer ein Treffer nicht zählt.

**Entscheidend ist das Wort „innerhalb".** Ein Treffer wird nur dann verworfen,
wenn er *in* der Wendung liegt — nicht schon, wenn die Wendung irgendwo in der
Nachricht steht. Sonst ließe sich jede Beleidigung durch Anhängen eines
harmlosen Satzes durchschmuggeln. `tests/test_testbox.py` sichert genau das:
„analyse der bodenprobe" entschärft `anal`, aber „analyse der bodenprobe du
anal" bleibt ein Verstoß.

Ausnahmen greifen **nur im Wortgrenzen-Durchlauf**. Wer `a.n.a.l` schreibt,
verschleiert absichtlich und meint keine Bodenprobe — dort wäre eine Ausnahme
nur ein Schlupfloch.

#### Die Wortliste

Auf Patricks Wunsch wurden drei öffentliche deutsche Listen heruntergeladen
(LDNOOBW, chucknorris-io, washyourmouthoutwithsoap) und **von Hand** sortiert.
Die Sortierung liegt nachvollziehbar in
`~/Desktop/pläne/wortlisten/sortierung.py`.

Von 164 neuen Wörtern wurden **70 übernommen** und 94 verworfen:

| Gruppe | Anzahl | Warum |
|---|---|---|
| übernommen | 70 | eindeutige Beleidigungen, Vulgärsprache, explizit Sexuelles |
| harmlos | 60 | würden echte Nachrichten löschen |
| fremdsprachig | 25 | Reste der maschinellen Übersetzung, greifen nie |
| unsicher | 10 | anatomisch/medizinisch plus `nazi` — Patricks Entscheidung |

Die verworfenen sind der interessante Teil. **`samen`** stand in einer der
Listen — in einer Grow-Gruppe undenkbar. Ebenso `rosette` (Blattrosette),
`stechen` (Stecklinge), `abstand` (Pflanzabstand), `nippel` (am
Bewässerungsschlauch), `mist` (Dünger), `geil` (dort schlicht ein Lob) und
`hündin` (auf dem Server wird über Haustiere geredet).

Gegengeprüft mit vierzehn typischen Sätzen aus der Gruppe: **kein einziger
Fehlalarm**, während „du bist ein arschloch" zuverlässig erkannt wird. Der
Wortgrenzen-Abgleich trägt dabei die Hauptlast — `mist` in `Pferdemist` schlägt
nicht an.


### Moderationsaktionen werden gemeldet (06.09.2026)

Patrick: „kick ban habe ich schon getestet das hat funktioniert sollte aber auch
angekündigt werden in einem kanal und den sollte man einstellen können."

Bis dahin landeten Kick, Bann, Entbannung und Timeout nur im Audit-Log des Bots
— man musste also wissen, dass etwas passiert ist, um es nachzusehen.

Der Kanal wird unter **Moderation → Aktionen & Überwachung** eingestellt
(`moderation_log_channel_id`). **Leer heißt: keine Meldung** — wer nichts
einstellt, bekommt auch nichts. Empfohlen ist ein Kanal, den nur das Team sieht:
Wer wen warum gekickt hat, geht nicht jeden etwas an.

Die Meldung ist ein Embed mit Betroffenem, Moderator, Grund und je nach Aktion
einem Zusatz — beim Bann der gelöschte Nachrichtenzeitraum, beim Timeout die
Dauer. Beides ist sonst nirgends im Kanal zu sehen.

Drei Feinheiten, die im Code festgehalten sind:

**Die Meldung kommt erst nach dem Erfolg.** Sie steht im `else`-Zweig hinter
`except discord.Forbidden` — eine Meldung über einen Kick, der an Discords
Rollenhierarchie gescheitert ist, wäre schlimmer als keine.

**Ein Fehlschlag beim Melden bricht die Aktion nicht ab.** Gekickt ist gekickt,
auch wenn der Meldekanal gelöscht wurde; es gibt dann nur eine Warnung im
Backend.

**Der Betroffene wird über `bot.get_user()` aufgelöst, nicht `get_member()`.**
Nach einem Kick oder Bann ist die Person kein Mitglied mehr — `get_member`
liefert dann nichts, `get_user` meist noch den Namen. Der Moderator wiederum
wird **vor** dem Sprung auf den Bot-Thread aus der Sitzung gelesen: Dort gibt es
keinen Flask-Kontext mehr.


## 18. Offene Punkte (Stand 06.09.2026)

### Warten auf eine Entscheidung von Patrick

- **Der Import öffentlicher Wortlisten hat keine Oberfläche.** Die drei Listen
  wurden am 06.09.2026 einmalig heruntergeladen, von Hand sortiert und
  übernommen (siehe „Schimpfwortfilter: Testbox und Wortliste"). Ein Werkzeug im
  Backend, das man einmal benutzt und das danach gepflegt werden will, wurde
  bewusst nicht gebaut — auf Nachfrage kommt es.
- **`create_giveaway` und `manage_game_settings` hängen an der Manager-Rolle**,
  die der Bot nicht vergeben kann. Das ist so gewollt (Patrick, 06.09.2026:
  „der bot hat nicht das recht einen Manager zu ernennen aber die rechte kann
  ein Moderator trotzdem haben") — es bleibt hier nur stehen, damit sich niemand
  über den Hinweis in der Übersicht wundert.

### Nicht geprüft, weil nur im echten Betrieb prüfbar

- **Live-Test der Moderationsaktionen** (Kick/Ban/Timeout) gegen den echten
  Server: bisher nur mit gestellten Discord-Objekten und DB-Kopien verifiziert.
- **Die Guten-Morgen-Nachricht ist eingerichtet, aber nie ausgelöst worden** —
  der erste echte Durchlauf steht noch aus.
- **Die große Sichtprüfung** des Backends steht aus; die Liste dazu liegt in
  `~/Desktop/pläne/sichtpruefung.md`.

### Bewusst nicht umgesetzt

- **Echter Online/Idle/DND-Status** in der Überwachungs-Übersicht: aktuell
  Näherung über Nachrichtenaktivität statt echtem Discord-Presence-Status.
  Bräuchte den privilegierten Presence-Intent, manuell im Discord Developer
  Portal freizuschalten.
- **Mycodo mit dem Bot verbinden.** Patrick am 03.09.2026 ausdrücklich
  zurückgestellt.
- **Frei erfindbare Achievement-Bedingungen im Backend.** Ein Achievement
  braucht ein Ereignis, das es auslöst. Eine im Backend zusammengeklickte
  Bedingung für ein Ereignis, das der Bot nicht kennt, würde nie fallen, und
  niemand könnte sehen warum. Die Bedingungs*arten* bleiben deshalb im Code, nur
  ihre Werte sind einstellbar (siehe Abschnitt „Achievements einstellbar").

### Seit dem 03.09.2026 erledigt

Der Vollständigkeit halber, weil diese Punkte hier lange als offen standen:

| War offen | Erledigt durch |
|---|---|
| **Linkfilter ohne Sperrliste** | `link_blacklist` plus vierte Regel, die vor allen anderen greift (06.09.2026) |
| **Schimpfwortfilter „gefällt mir so noch nicht"** | Wortstufen mit je eigener Reaktion, erlaubte Wendungen, Testbox; Wortliste von 187 auf 267 kuratiert (06.09.2026) |
| **Automod-Seite zu voll** | Vier Reiter: Schimpfwörter, Links, Spam, Verstöße (06.09.2026) |
| **Rollenreihenfolge stand auf dem Kopf** | Am 06.09.2026 auf Patricks Auftrag umsortiert: Keimling 🌱 ganz unten, jede Leiter aufsteigend, alle elf weiterhin unter der Bot-Rolle |
| **Sammlung oder Rangleiter?** | Entschieden am 06.09.2026: Es bleibt bei **Sammlung** — jede erreichte Stufe wird weiter getragen |
| **Guten-Morgen-Nachricht nie ausgelöst** | Lief am 06.09.2026 um 08:00; sie protokolliert seitdem auch den Erfolgsfall |
| Kein einziger Slash-Befehl registriert | `bot.tree.sync()` mit Änderungserkennung; 16 Befehle registriert, `>` abgeschaltet |
| Keine Fehlerbehandlung für Befehle | Zentraler `on_command_error` plus `tree.on_error`, Meldungen nur für den Aufrufer |
| Moderationsgründe ohne 512-Zeichen-Grenze | Kürzung nach **URL-kodierter** Länge; das eigene Audit-Log behält den vollen Text |
| Bild-/Link-Felder im Embed ungeprüft | Formatprüfung, die das betroffene Feld beim Namen nennt |
| `ERR_TIMED_OUT` in der Android-App | am 02.09.2026 gelöst, Analyse in `Fernzugriff-Setup-Anleitung.md` §9.1 |
| **Die Rollen-Zuordnung (Recht → Rolle) war leer** | Fünf Rechte hängen jetzt an der Moderator-Rolle; beide Richtungen laufen automatisch (05.09.2026) |
| **Rolle und Berechtigung liefen auseinander** | `cogs/rechtesync.py` — Listener auf Rollenänderungen plus Abgleich nach jedem Start |
| **Achievements nur im Code änderbar** | Bedingungsart im Code, Wert in der Datenbank; einstellbar unter Admin-Werkzeuge → XP & Level |
| **`/frage` antwortete „Die Anwendung reagiert nicht"** | Vorab-Bestätigung für alle Befehle, zentral in `main.py` |
| **„Verstöße zurücksetzen" schien wirkungslos** | Rückmeldung plus echtes Löschen, einzeln und ganze Historien |
| **App-Zugang „ging manchmal nicht"** | Nicht routbares IPv6 auf dem Pi abgeschaltet, siehe `Fernzugriff-Setup-Anleitung.md` §10 |

---

## 19. Chronik (03. bis 05.09.2026)

Ein außergewöhnlich dichter Tag — hier die Reihenfolge, weil einige Entscheidungen nur im
Zusammenhang verständlich sind.

| Was | Warum es kam |
|---|---|
| Logging repariert (`PYTHONUNBUFFERED`) | Der Dienst schrieb in 24 Stunden **null** eigene Zeilen ins Journal |
| `gumo`, `hallo`, `bothelp` entfernt | `gumo` war defekt: die Textdatei enthielt Python-Quelltext |
| Tägliche Guten-Morgen-Nachricht | Ersatz für `gumo`, im Backend pflegbar |
| Slash-Befehle registriert, `>` abgeschaltet | 17 Befehle waren gebaut, aber bei Discord unbekannt |
| `/help` neu gebaut | Die Vorgänger zeigten **jedem alles**, auch die Adminbefehle |
| Zentrale Fehlerbehandlung | Falsch benutzte Befehle blieben stumm |
| Module getrennt (Achievements, Bedingungen, Schema, Abfragen) | Ziel: Module später einzeln verkaufen und installieren |
| Rollenvergabe in drei Wegen | Reaktion, Verdienst, Berechtigung |
| Bot-Rolle über Moderator gezogen | Voraussetzung dafür, dass der Bot Moderator vergeben kann — mit der bewussten Folge, dass Moderatoren nicht mehr vor ihm geschützt sind |

Tests: von 31 auf 594.

### 04.09.2026

| Was | Warum es kam |
|---|---|
| Ankündigungen zusammengefasst | Eine Quizantwort ergab fünf Ankündigungen in zwei Sekunden — hätte jedes bestehende Mitglied getroffen |
| Ankündigungskanal sichtbar und änderbar | Er war nur beim Anlegen einstellbar und in der Liste unsichtbar |
| Rangleiter oder Sammlung, pro Regel | Frage von Patrick, ob Rollen beim Aufstieg verschwinden sollen |
| Bedingung „beantwortete Quizfragen" | Für Ideenschmied sinnvoller als eingereichte — davon gibt es serverweit nur 17 |
| Rolle bringt Berechtigung mit | Ideenschmied darf eigene Fragen einreichen |
| Reaktionsrollen für andere Wege gesperrt | Member ist das Tor zum Server und darf nur über die Regel-Zustimmung entstehen |
| Botbefehl-Rechte auf dem Server bereinigt | Nach dem Abschalten von `>` konnte kein normales Mitglied mehr Befehle nutzen |
| Vorab-Bestätigung für alle Befehle | `/frage` lief in Discords Drei-Sekunden-Frist (Fehler 10062); die Bestätigung kam im ganzen Projekt vorher kein einziges Mal vor |
| Verstöße löschbar, Zurücksetzen meldet sich | Der Knopf arbeitete stillschweigend und war dadurch von einem kaputten nicht zu unterscheiden |
| „XP & Level" unter Admin-Werkzeuge | Die Seite stand für jedes Mitglied in der Hauptleiste und sagte den meisten nur, dass sie hier nichts einstellen können |

### Die Drei-Sekunden-Frist

Der wichtigste Einzelbefund des Tages, weil er alle Befehle betraf und nicht nur
den gemeldeten:

Discord verlangt binnen **drei Sekunden** ein Lebenszeichen auf einen Befehl.
Verstreicht sie, ist der Sitzungsschlüssel verfallen — und zwar endgültig, auch
für die Fehlermeldung. Deshalb sah der Nutzer ausschließlich Discords eigenen
Text „Die Anwendung reagiert nicht", während im Log ein `NotFound: 404 (error
code: 10062)` stand.

Gemessen brauchte `/frage` selbst nur rund 7 ms, der zusätzliche Rollen-Check pro
Nachricht rund 22 ms. Die Frist riss also nicht an der eigenen Arbeit des
Befehls, sondern an dem, was währenddessen sonst auf dem Pi lief. Genau deshalb
ist die Vorab-Bestätigung die richtige Antwort und nicht das Optimieren
einzelner Abfragen: Sie verlängert die Frist auf fünfzehn Minuten und schützt
jeden Befehl, statt einen schneller zu machen.

Die Antwortart (`öffentlich`, `vertraulich`, `manuell`) steht dabei am Befehl
selbst im `extras`-Feld, weil sich nachträglich nicht mehr ändern lässt, ob eine
Antwort öffentlich wird. `tests/test_interaktion.py` wacht darüber, dass die
Angabe zu dem passt, was der Befehl tatsächlich sendet.

### Rollen und Rechte in beide Richtungen (05.09.2026)

Bis zu diesem Tag lief nur eine Richtung, und die auch nur halb:

| Richtung | vorher | jetzt |
|---|---|---|
| Recht → Rolle | bei jeder Rechteänderung im Backend (`_rollen_nachziehen`), plus Knopf für alle | unverändert |
| Rolle → Recht | **gab es nicht** | `cogs/rechtesync.py`, bei jeder Rollenänderung und einmal nach dem Start |

Sichtbar wurde die Lücke an drei Personen: Zwei trugen die Moderator-Rolle ohne
jede Berechtigung, einer hatte sämtliche Moderationsrechte, ohne die Rolle zu
tragen. Die Benutzerverwaltung wies auf beides hin — behoben wurde es nie, weil
niemand den Knopf drückte und die Gegenrichtung fehlte.

**Drei Sicherungen** machen den Unterschied zwischen brauchbar und gefährlich:

1. **`is_admin` läuft nie über eine Rolle**, weder hin noch zurück. Es ist der
   Generalschlüssel; ein versehentlicher Rollenklick dürfte niemandem die volle
   Kontrolle geben, und ein versehentlich entfernter dürfte nicht den letzten
   Admin aussperren.
2. **Ein Recht, das an einer Rolle hängt, gilt nur mit dieser Rolle.**
   Rechte, die in keiner einzigen Rollenzuordnung vorkommen, bleiben dagegen
   unangetastet — ein Admin kann also weiterhin jemandem gezielt etwas geben,
   ohne ihm eine Rolle zu verleihen.

   Diese Regel löste am 05.09.2026 eine vorsichtigere ab: *„Der Bot nimmt nur
   weg, was er selbst gegeben hat"*, erkennbar an `granted_by = rolle:<ID>`.
   Für den ERSTEN Durchlauf war sie richtig — ohne sie hätte er jedem seine von
   Hand vergebenen Rechte genommen, bevor überhaupt jemand nachsehen konnte, ob
   das passt. Danach stand sie der Absicht im Weg: Als Patrick einem Mitglied
   die Moderator-Rolle entzog, blieben sieben Moderationsrechte stehen, weil er
   sie seinerzeit selbst eingetragen hatte. Seine Ansage: „die rechte die er nur
   als moderator hat müssen entzogen werden."

   Wichtig zu wissen: Betroffen sind **alle** Rechte, die irgendeiner Rolle
   zugeordnet sind — nicht nur die der gerade verlorenen. Wer die
   Moderator-Rolle verliert und auch keine andere Rolle trägt, verliert damit
   auch Rechte, die eigentlich zum Manager gehören. Das ist gewollt: Ohne
   passende Rolle soll keines dieser Rechte bestehen bleiben.
3. **Der Besitzer wird nie beschnitten.** Er soll sich nicht durch eine
   Rollenänderung aus seinem eigenen Backend aussperren können.

Bewusst dieselbe Spalte statt einer eigenen Merker-Tabelle: Zwei Tabellen
könnten auseinanderlaufen, und dann wüsste niemand, welche recht hat.

### Achievements einstellbar (05.09.2026)

Vorher steckte jede Auslösebedingung in einer Lambda-Funktion im Modulcode:

```python
Achievement("QUIZ_10", "Wissbegierig", ..., _mindestens_richtig(10))
```

Die Schwelle war damit Teil des Programms — für „ab wie vielen Fragen gibt es
das?" brauchte es einen Codeeintrag und einen Neustart.

Jetzt ist die Bedingung zweigeteilt:

* Die **Art** (`achievements.Bedingungstyp`) steht im Code und gehört einem
  Modul. Sie weiß, welche Zahl ein Ereignis überhaupt mitliefert — das lässt
  sich nicht sinnvoll im Backend zusammenklicken.
* Der **Wert** steht in der Tabelle `achievements` und ist unter
  Admin-Werkzeuge → XP & Level → Achievements einstellen änderbar.

Dasselbe Muster wie bei den verdienten Rollen (`verdiente_rollen.Bedingungsart`).
Zwei verschiedene Denkweisen für dasselbe Problem wären der Anfang vom
Auseinanderlaufen.

Vier Bedingungsarten bringen die Module heute mit: richtig beantwortete
Quizfragen, Quizfrage zu einer bestimmten Uhrzeit, selbst eingereichte Fragen
(alle drei aus dem Quiz-Modul) und erreichtes Level (aus XP & Level).

**Anlegen und Löschen:** Neue Achievements lassen sich im Backend anlegen; sie
tragen `eigenbau = 1`. Nur diese lassen sich auch wieder löschen — ein
Achievement aus dem Modulcode käme beim nächsten Start ohnehin zurück, das
Löschen sähe aus wie ein Fehler. Solche werden stattdessen deaktiviert, und wer
sie hat, behält sie.

**Warum die Voreinstellungen im Code bleiben:** Ein frisch installiertes Modul
soll sofort brauchbar sein. Die Definitionen werden beim Start per
`INSERT OR IGNORE` übernommen, und das nachträgliche Füllen der
Bedingungsfelder greift ausdrücklich nur dort, wo noch nichts steht — sonst
spränge eine im Backend geänderte Schwelle bei jedem Neustart zurück.

**Meine Achievements** (`/achievements`) zeigt seit diesem Tag nur noch, was
jemand tatsächlich hat. Vorher stand dort die vollständige Liste samt der
verschlossenen; auf einer Seite dieses Namens ist das eher Katalog als Vitrine.
Wie viele es insgesamt gibt, bleibt als bloße Zahl stehen — das verrät keines
und gibt trotzdem ein Ziel.

### Der Tunnel hing an IPv6 (05.09.2026)

Symptom: Die Android-App erreichte das Backend „manchmal nicht", und wenn doch,
mit langen Ladezeiten. Die Diagnose der App zeigte, dass das Handy den Pi-Knoten
`d2c93fd899…` nicht auflösen konnte.

Der Fehler lag weder am Bot noch an der App. Gemessen auf dem Pi:

| Prüfung | vorher | nachher |
|---|---|---|
| Backend direkt (`127.0.0.1:5000`) | 0,6 ms | unverändert |
| DNS-Auflösung | 2–3 s | 9–13 ms |
| Relay über IPv6 | scheitert nach 15 ms | wird nicht mehr versucht |
| Relay über IPv4 | 200 in 0,39 s | 200 in 0,28 s |
| Tunnel-Neustarts | 9 pro Stunde | 0 |

**Ursache:** Der Pi hatte als einzige IPv6-Adresse eine ULA aus dem
`fd00::/8`-Bereich (`fdc9:…`). Die gilt im Hausnetz, ist im Internet aber nicht
routbar. Der Pi meldete sie trotzdem als gültige Adresse und versuchte jede
Verbindung zuerst darüber. Jeder Versuch war zum Scheitern verurteilt und kostete
Zeit — daher die Ladezeiten.

Für den dumbpipe-Tunnel war es gravierender: Ohne Relay-Verbindung
(`Failed to connect to the home relay` bei jedem Start) veröffentlichte er seine
Adresse nicht im pkarr-DNS. Das Handy suchte dann einen Knoten, der sich nirgends
eingetragen hatte — und fand ihn in den Lücken zwischen den Neustarts nicht.

**Behebung:** IPv6 auf dem Pi abgeschaltet, `/etc/sysctl.d/99-ipv6-aus.conf`.
`lo` bleibt bewusst aktiv, weil manche Dienste auf `::1` binden. Rückgängig zu
machen, indem man die Datei löscht und `sudo sysctl --system` ausführt.

Die Knoten-Kennung des Tunnels ist über Neustarts hinweg stabil — bereits
verteilte Tickets in der App bleiben nach so einem Eingriff gültig und müssen
nicht neu erzeugt werden.

**Merkposten für ähnliche Fälle:** Dass die Verbindung „manchmal" funktioniert,
war der entscheidende Hinweis. Ein durchgehend defekter Dienst sieht anders aus
als einer, der sich alle 60 bis 90 Sekunden neu startet.
