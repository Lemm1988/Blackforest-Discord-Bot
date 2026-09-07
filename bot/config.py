"""Lädt Konfiguration aus der .env-Datei und stellt sie als Konstanten bereit."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
PROJECT_ROOT = BASE_DIR.parent

load_dotenv(BASE_DIR / ".env")

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "CHANGE_ME")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "CHANGE_ME")
BOOTSTRAP_ADMIN_DISCORD_ID = os.environ.get("BOOTSTRAP_ADMIN_DISCORD_ID", "CHANGE_ME")
# Der unveraenderliche Bot-Owner (Spec 2: Owner-Schutz). Bewusst eine reine
# .env-Konstante analog zu BOOTSTRAP_ADMIN_DISCORD_ID, NICHT in der DB -
# dadurch kann der Owner niemals ueber die Web-UI oder Bot-Commands
# veraendert/entfernt werden, weil diese die .env-Datei gar nicht anfassen.
BOT_OWNER_ID = os.environ.get("BOT_OWNER_ID", "")

OAUTH_CONFIGURED = DISCORD_CLIENT_ID != "CHANGE_ME" and DISCORD_CLIENT_SECRET != "CHANGE_ME"

FLASK_HOST = "127.0.0.1"
INTERNAL_HOST = "127.0.0.1"
INTERNAL_PORT = 5001  # waitress bindet hier nur lokal, nginx macht TLS-Terminierung nach aussen auf FLASK_PORT
FLASK_PORT = 5000  # der Port, auf dem nginx tatsaechlich lauscht (siehe /etc/nginx/conf.d/blackforestbot.conf)

# NUR fuer das lokale Bot Connect Dashboard (Mitgliederliste), siehe
# backend/internal_app.py fuer die ausfuehrliche Sicherheitsbegruendung: dieser
# Port hat bewusst KEINEN nginx-Eintrag und wird NIE getunnelt (anders als
# INTERNAL_PORT/5001, das ueber nginx+dumbpipe-Tunnel doch aus dem Internet
# erreichbar ist, obwohl es nur an 127.0.0.1 bindet).
INTERNAL_MEMBERS_PORT = 5052

# Der Port, den der BROWSER tatsaechlich in der Adressleiste sieht. Das ist NICHT
# zwingend FLASK_PORT: Beim Zugriff ueber den Iroh/dumbpipe-Tunnel (siehe
# connect_discordbot.sh) mappt der Client einen eigenen lokalen Port (aktuell 8500)
# auf FLASK_PORT (5000) auf dem Pi. Discord leitet nach dem Login exakt zur
# registrierten Redirect-URI zurueck, die vom BROWSER aus erreichbar sein muss -
# daher muss hier der Tunnel-Port stehen, nicht FLASK_PORT. Aenderbar per .env,
# falls sich der lokale Tunnel-Port je aendert.
OAUTH_REDIRECT_PORT = os.environ.get("OAUTH_REDIRECT_PORT", "8500")
REDIRECT_URI = f"https://{FLASK_HOST}:{OAUTH_REDIRECT_PORT}/callback"

FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")  # wird in main.py generiert falls leer

# Das frühere Befehlspräfix. Seit dem 03.09.2026 werden damit KEINE Befehle mehr
# ausgelöst - der Bot hört nur noch auf Slash-Befehle. Der Wert bleibt stehen,
# weil der Bot Nachrichten wie ">xp" weiterhin ERKENNT, um freundlich auf die
# neue Schreibweise hinzuweisen (siehe cogs/hilfe.py). Sobald sich niemand mehr
# vertippt, kann der Hinweis und damit auch diese Zeile weg.
ALTES_PRAEFIX = ">"

DATA_DIR = BASE_DIR / "data"
ZITATE_FILE = DATA_DIR / "zitate.txt"
BADWORDS_FILE = DATA_DIR / "badwords.txt"

# Standardwerte für konfigurierbare Bot-Settings (Erststart, danach in DB änderbar über settings-Tabelle)
DEFAULT_SETTINGS = {
    # Kanal fuer die Meldung manueller Moderationsaktionen (Kick, Bann,
    # Entbannung, Timeout). Leer = keine Meldung. Bewusst ein eigener Kanal und
    # nicht der Willkommenskanal: Wer wen warum gekickt hat, geht das Team an
    # und nicht jeden Neuzugang.
    "moderation_log_channel_id": "",
    "welcome_channel_id": "1239923799420567595",

    # --- Taegliche Guten-Morgen-Nachricht (03.09.2026) ---
    # Ersetzt den frueheren >gumo-Befehl: Statt dass jemand den Bot danach
    # fragen muss, gruesst er von selbst einmal am Tag. Die Texte liegen im
    # Textpool "morning" (Tabelle personality_texts) und sind unter
    # Bot-Setup -> Personality -> Texte pflegbar.
    #
    # BEWUSST unabhaengig von der Stimmung des Bots (Vorgabe Patrick,
    # 03.09.2026) - eine flache Liste, jeder Text gleich wahrscheinlich.
    "morning_greeting_enabled": "0",
    # Uhrzeit in lokaler Zeit (Pi laeuft auf Europe/Berlin, Sommer-/Winterzeit
    # erledigt das System). Format HH:MM.
    "morning_greeting_time": "07:00",
    # Leer = der Bot grueesst NIRGENDWO. Bewusst kein Rueckfall auf einen anderen
    # Kanal - dieselbe Regel wie bei announcement_channel_id, damit der Bot nie
    # unerwartet irgendwo auftaucht.
    "morning_greeting_channel_id": "",
    # Nachlauffenster in Minuten: War der Pi zur eingestellten Zeit aus, wird nur
    # innerhalb dieses Fensters nachgeholt. Danach faellt der Gruss fuer den Tag
    # aus - eine verpasste Begruessung faellt niemandem auf, ein "Guten Morgen"
    # um 15 Uhr schon.
    "morning_greeting_grace_minutes": "60",
    # An/Aus-Schalter fuer die Willkommensnachricht bzw. die Regel-Reaktion
    # (Rollenvergabe per 👍) - jede Hintergrund-Funktion des Bots soll einzeln
    # abschaltbar sein, nicht nur die grossen Features wie Quiz/Automod.
    "welcome_message_enabled": "1",
    # Schimpfwortfilter-Verhalten, einstellbar über die Backend-Seite "/moderation".
    "badword_filter_enabled": "1",
    # Eine Reaktion je Wortstufe (06.09.2026). badword_filter_action bleibt als
    # einmalige Migrationsquelle stehen (siehe database._migriere_wortstufen).
    # Punktesystem statt blosser Zaehlung (06.09.2026): Drei "scheisse" wiegen
    # nicht dasselbe wie drei rassistische Beschimpfungen.
    "badword_punkte_leicht": "1",
    "badword_punkte_mittel": "2",
    "badword_punkte_schwer": "3",
    "badword_punkte_schwelle": "5",
    "badword_action_leicht": "delete_only",
    "badword_action_mittel": "delete_warn",
    "badword_action_schwer": "delete_warn_timeout",
    "badword_filter_action": "delete_warn",  # delete_only | delete_warn | delete_warn_timeout
    "badword_timeout_duration_minutes": "10",
    "badword_violation_expiry_mode": "auto_30d",  # auto_30d | manual_only - gilt einheitlich
        # für Schimpfwort- UND Link-Verstöße (bewusst EIN gemeinsamer Verjährungs-Modus
        # statt drei fast identischer Einstellungen, siehe Automod-Seite).
    # Steuert Homoglyphen-Auflösung (kyrillisch/griechisch -> lateinisch) UND die
    # Pro-Token-Teil-Obfuskations-Erkennung ("sch.eiss") - beides zusammen unter
    # EINEM Schalter, da beides "riskantere" (nicht so hart wortgrenzen-
    # geschützte) Erkennungsschritte sind. Leetspeak/Wiederholungs-Reduktion/
    # vollständig auseinandergezogene Schreibweisen bleiben davon unabhängig
    # immer aktiv (waren schon vor der Modernisierung Teil des Filters).
    # Default AN, da explizit als Modernisierung gewünscht - bei zu vielen
    # Fehlalarmen kann ein Admin hier gezielt zurückschalten.
    "badword_aggressive_normalization_enabled": "1",
    # Pflanzenquiz-Verhalten, einstellbar über die Backend-Seite "/games/quiz".
    # game_quiz_enabled: An/Aus-Schalter fuer das Spiel selbst (Spec 1, Phase B1) -
    # bei "0" antwortet der >frage-Befehl nur noch mit einem Hinweis, alle anderen
    # Befehle (gamescore/xp/leaderboard/achievements) bleiben unabhaengig davon
    # nutzbar, da sie nur bestehende Staende ANZEIGEN statt das Spiel zu SPIELEN.
    "game_quiz_enabled": "1",
    "quiz_points_correct": "50",
    "quiz_answer_timeout_seconds": "30",
    "quiz_cooldown_seconds": "5",
    # Spam-/Flutschutz: löst SOFORT aus, sobald innerhalb des Zeitfensters mehr als
    # spam_message_threshold Nachrichten desselben Users eingehen (kein zusätzlicher
    # "Verstöße über Zeit ansammeln"-Schwellwert wie bei Schimpfwort/Link nötig - ein
    # einzelner Flut-Ausbruch ist bereits für sich genommen der Ernstfall).
    "spam_filter_enabled": "1",
    "spam_message_threshold": "5",
    "spam_time_window_seconds": "5",
    "spam_filter_action": "delete_warn_timeout",  # delete_only | delete_warn | delete_warn_timeout
    "spam_timeout_duration_minutes": "10",
    # Link-Filter: standardmäßig AUS (mehr potenzielle Fehlalarme als der Spam-Schutz).
    # Nutzt wie der Schimpfwortfilter eine Verstöße-über-Zeit-Schwelle vor dem Timeout
    # (ein einzelner geposteter Link ist eher ein Versehen als akuter Spam).
    "link_filter_enabled": "0",
    # link_filter_mode: NUR NOCH als einmalige Migrationsquelle genutzt (siehe
    # database._migrate_link_filter_mode()) - der Link-Filter selbst liest
    # dieses Setting nicht mehr, seit die drei folgenden Regeln unabhaengig
    # voneinander kombinierbar sind statt eines exklusiven Modus.
    "link_filter_mode": "block_all",  # block_all | whitelist | new_members_only
    "link_filter_block_all_enabled": "1",
    "link_filter_whitelist_enabled": "0",
    "link_filter_new_members_enabled": "0",
    "link_filter_new_member_days": "3",
    "link_filter_action": "delete_warn",  # delete_only | delete_warn | delete_warn_timeout
    "link_filter_timeout_threshold": "3",
    "link_filter_timeout_duration_minutes": "10",
    # XP/Level-System (Spec 1): bewusst getrennt vom Quiz-Punktesystem
    # (players.score bleibt der reine Quiz-Highscore). Levelkurve konfigurierbar
    # ueber zwei Zahlen statt einer festen Tabelle - siehe database._xp_threshold()
    # fuer die genaue Formel und eine Herleitung der Default-Werte.
    "xp_level_base": "100",
    "xp_level_growth": "50",
    # Cooldown gilt nur fuer generische/zukuenftige XP-Quellen (z.B. Nachrichten-
    # aktivitaet, noch nicht angebunden) - der Quiz-Erfolg umgeht ihn bewusst,
    # weil quiz_cooldown_seconds das Spam-Problem fuer diese Quelle bereits regelt.
    "xp_cooldown_seconds": "60",
    "xp_per_quiz": "10",
    # Ankuendigungen fuer Levelaufstieg/Achievement-Freischaltung (Spec 1: kurze,
    # sparsam eingesetzte Community-Reaktionen). Leerer announcement_channel_id
    # faellt auf welcome_channel_id zurueck, damit kein zwingend neuer Kanal
    # eingerichtet werden muss, um das Feature zu nutzen.
    "announcement_channel_id": "",
    # Ein eigener Zielkanal je Art von Ankuendigung (Patrick, 07.09.2026: "man
    # soll immer überall einstellen können wohin der bot postet"). Leer heisst
    # hier NICHT "gar nicht posten", sondern "nimm den allgemeinen Kanal
    # darueber" - sonst muesste man fuenf Felder ausfuellen, um den Zustand von
    # vorher zu bekommen.
    "levelup_channel_id": "",
    "achievement_channel_id": "",
    "record_channel_id": "",
    "milestone_channel_id": "",
    "streak_channel_id": "",
    # Die Rekordpruefung lief bis zum 07.09.2026 bei jedem Herzschlag, also
    # jede Minute - und meldete jeden neuen Hoechststand sofort. An einem
    # regen Tag wurde daraus eine Nachrichtenflut. Jetzt einmal taeglich fuer
    # den Tag, der gerade zu Ende ging.
    "server_record_check_time": "00:00",
    # Grosszuegig, damit die Pruefung nachgeholt wird, wenn der Pi um
    # Mitternacht aus war. Ein Rekord, der morgens um sechs gemeldet wird, ist
    # immer noch richtig.
    "server_record_grace_minutes": "720",
    "announcements_enabled": "1",
    # Nachrichten-XP (Spec 1): bewusst standardmaessig AUS, da das eine spuerbare
    # Verhaltensaenderung fuer den ganzen Server ist - der Admin soll es bewusst
    # aktivieren. xp_per_message klein halten, da es potenziell sehr oft pro Tag
    # ausgeloest wird. daily_xp_limit begrenzt die aus Nachrichten gewonnene XP
    # pro Tag und User (verhindert stundenlanges Chatten als XP-Farming), gilt
    # NICHT fuer Quiz-/Achievement-XP.
    "message_xp_enabled": "0",
    "xp_per_message": "2",
    "daily_xp_limit": "200",
    # Personality/Mood (Spec 1, Phasen 2+8). Haeufigkeit, Ort und Ton sind
    # bewusst NICHT fest einprogrammiert, sondern ausschliesslich ueber diese
    # Settings steuerbar (siehe Backend-Seite "/personality") - alle Werte hier
    # sind nur sinnvolle DEFAULTS, keine Festwerte. Mood selbst ist ein rein
    # deterministischer interner Zustand (siehe bot/mood.py), KEINE echte
    # Emotion/KI.
    "personality_enabled": "1",
    "mood_enabled": "1",
    "mood_update_interval_minutes": "30",
    "mood_night_start_hour": "0",
    "mood_night_end_hour": "6",
    # Mood-Erkennungsschwellen (Spec 1, Phase B3) - urspruenglich in Phase 9
    # bewusst hartcodiert ("interne Erkennungsparameter, keine sichtbaren
    # Verhaltens-Stellschrauben"), auf ausdruecklichen Wunsch nachtraeglich doch
    # als Settings exponiert. Defaults entsprechen exakt den bisherigen
    # hartcodierten Werten aus bot/mood.py - keine Verhaltensaenderung bei
    # unveraendert gelassenen Settings.
    "mood_activity_window_minutes": "15",
    "mood_excited_active_user_threshold": "5",
    "mood_bored_inactivity_minutes": "30",
    "mood_violation_window_minutes": "60",
    "mood_annoyed_violation_threshold": "3",
    # Wahrscheinlichkeit in Prozent (0-100), dass eine bestehende Bot-Antwort
    # mood-gefaerbt statt neutral ausfaellt, statt IMMER gefaerbt zu sein.
    "personality_response_frequency": "50",
    "personality_humor_level": "medium",  # low | medium | high
    # status_only: keine spontanen Nachrichten, nur gefaerbte Antworten +
    # rotierender Status (sicherste Option). mention_only: als Enum-Wert bereits
    # vorbereitet, hat in dieser Phase aber noch KEIN implementiertes Verhalten
    # (nur channel_messages ist in Phase 9 konkret umgesetzt).
    "personality_response_mode": "status_only",  # status_only | mention_only | channel_messages
    "personality_spontaneous_channel_id": "",
    # Harter Mindestabstand (Stunden) zwischen zwei spontanen Nachrichten - der
    # Code erzwingt zusaetzlich eine serverseitige Untergrenze (siehe
    # mood.MINIMUM_SPONTANEOUS_HOURS), die auch ein sehr niedrig gesetzter
    # Wert hier nicht unterschreiten kann.
    "personality_spontaneous_min_hours": "12",
    "dynamic_status_enabled": "1",  # unabhaengig von personality_enabled steuerbar
    "status_rotation_minutes": "20",
    # Zufallsereignisse (Spec 1, Phase 10), bauen auf dem Mood-System (Phase 9)
    # auf. Haeufigkeit, Ort und die relativen Raritaeten der Event-Typen sind
    # bewusst konfigurierbar (Spec-Vorgabe) - der Mood-Einfluss auf die Basis-
    # wahrscheinlichkeit dagegen ist bewusst NICHT als Setting exponiert, siehe
    # events.MOOD_MULTIPLIERS (gleiche Begruendung wie bei den Mood-Erkennungs-
    # schwellen aus Phase 9: nur Haeufigkeit/Ort/Ton der AUSGABE ist
    # konfigurierbar, nicht jedes interne Detail).
    "random_events_enabled": "1",
    "event_check_interval_minutes": "15",
    "event_probability_percent": "15",
    "event_channel_id": "",
    # Relative Gewichte (muessen sich nicht zu 100 aufsummieren) - Humor am
    # haeufigsten/harmlosesten (rein kosmetisch, keine Mechanik-Wirkung),
    # XP-Drop/Community-Bonus seltener (haben echte Spielauswirkung).
    "event_weight_humor": "40",
    "event_weight_quiz": "25",
    "event_weight_xpdrop": "20",
    "event_weight_community": "15",
    "event_xpdrop_amount": "50",
    "event_community_bonus_multiplier": "2",
    "event_community_bonus_minutes": "10",
    # Erweiterte Community-Reaktionen (Spec 1, Phasen 4+9): Server-Rekorde,
    # Quiz-Serien, Server-Meilensteine. Eigener Schalter community_reactions_enabled
    # ZUSAETZLICH zu announcements_enabled (Phase 6) - so lassen sich Level-Up/
    # Achievement-Ankuendigungen unabhaengig von diesen neuen Ankuendigungsarten
    # an-/abschalten. Komma-getrennte Schwellenwert-Listen werden ueber
    # database.parse_csv_ints() geparst.
    "community_reactions_enabled": "1",
    "streak_announcement_thresholds": "5,10,20,50",
    "milestone_thresholds": "100,500,1000,5000,10000",
}

# Menschenlesbare Beschriftungen für die granularen Berechtigungs-Flags
# (database.ALL_PERMISSIONS ist die maßgebliche Liste der gültigen Keys).
PERMISSION_LABELS = {
    "is_admin": "Admin (voller Zugriff auf alles, inkl. Benutzerverwaltung)",
    "verify_questions": "Fragen verifizieren/bearbeiten/löschen",
    "manage_categories": "Quiz-Kategorien verwalten",
    "create_embed": "Discord-Embeds erstellen/senden",
    "create_giveaway": "Giveaways starten",
    "send_dm": "Nutzer per Direktnachricht anschreiben",
    "manage_badword_filter": "Schimpfwortfilter verwalten (Wortliste, Einstellungen, Reset)",
    "manage_game_settings": "Quiz-Einstellungen ändern (Punkte, Zeitlimit, Cooldown)",
    "moderate_members": "Mitglieder kicken/bannen/timeouten, Überwachungs-Übersicht einsehen",
}
