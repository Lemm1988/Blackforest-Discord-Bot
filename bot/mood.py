"""Deterministische Mood-Bewertung und Text-/Status-Auswahl (Spec 1, Phase 9).

WICHTIG (Grundprinzip aus der Spec, unbedingt einhalten): Mood ist ein interner
Spiel-/Verhaltenszustand, KEINE echte Emotion und KEINE KI - alles hier sind
reine, deterministische Funktionen ohne I/O und ohne LLM-Aufrufe, absichtlich
von der Discord-/DB-Anbindung (siehe cogs/personality.py) getrennt, damit sie
isoliert und ohne laufenden Bot testbar sind.

Alle Formulierungen sind bewusst freundlich gehalten - auch "annoyed" bedeutet
hier nur "gerade viel zu tun", niemals unhöflich oder aggressiv.
"""
import random

MOODS = ("happy", "neutral", "excited", "bored", "sleepy", "annoyed")

# Default-Schwellenwerte fuer die Mood-Erkennung. Ueberschreibt die urspruengliche
# Phase-9-Entscheidung ("bewusst NICHT konfigurierbar") - der User wollte das in
# Phase B3 doch als Settings exponiert haben. Bleiben hier als benannte Konstanten
# stehen (dienen jetzt als Parameter-Defaults von evaluate_mood() unten UND als
# Vorlage fuer config.DEFAULT_SETTINGS), damit evaluate_mood() weiterhin eine
# reine Funktion ohne I/O bleibt - die Werte werden vom AUFRUFER (cogs/personality.py)
# aus den Settings gelesen und hier nur als Parameter hineingereicht.
ACTIVITY_WINDOW_MINUTES = 15
EXCITED_ACTIVE_USER_THRESHOLD = 5
BORED_INACTIVITY_MINUTES = 30
VIOLATION_WINDOW_MINUTES = 60
ANNOYED_VIOLATION_THRESHOLD = 3

# Harte, im Code festgeschriebene Untergrenze für den Mindestabstand zwischen
# zwei spontanen Nachrichten (Stunden) - unabhängig davon, was der Admin über
# personality_spontaneous_min_hours einstellt. Verhindert eine Chat-Flut auch
# bei einer versehentlichen (oder absichtlich sehr niedrigen) Fehlkonfiguration.
MINIMUM_SPONTANEOUS_HOURS = 4.0

HUMOR_TIERS = {"low": 0, "medium": 1, "high": 2}


def is_night_hour(hour: int, night_start_hour: int, night_end_hour: int) -> bool:
    """True, wenn `hour` im konfigurierten Nachtfenster liegt. Unterstützt auch
    ein über Mitternacht laufendes Fenster (z.B. 22-6 Uhr)."""
    if night_start_hour == night_end_hour:
        return False  # entartetes Fenster (0 Stunden Breite) - nie "Nacht"
    if night_start_hour < night_end_hour:
        return night_start_hour <= hour < night_end_hour
    return hour >= night_start_hour or hour < night_end_hour


def evaluate_mood(
    *,
    current_hour: int,
    night_start_hour: int,
    night_end_hour: int,
    recent_violation_count: int,
    active_user_count: int,
    minutes_since_last_message,
    excited_active_user_threshold: int = EXCITED_ACTIVE_USER_THRESHOLD,
    bored_inactivity_minutes: float = BORED_INACTIVITY_MINUTES,
    annoyed_violation_threshold: int = ANNOYED_VIOLATION_THRESHOLD,
) -> str:
    """Wertet die aktuelle Mood deterministisch aus einem festen Satz Signale
    aus. Prioritätsreihenfolge bei konkurrierenden Signalen (bewusst gewählt
    und hier dokumentiert):

    1. annoyed - viele Automod-Verstöße kürzlich: ein aktives Problem auf dem
       Server ist wichtiger als jedes andere Signal und soll nicht von
       Nachtzeit oder Chataktivität verdeckt werden.
    2. sleepy  - Nachtzeit: ein starkes, rein objektives Signal für den eigenen
       Rhythmus des Bots, unabhängig davon wie viele Menschen gerade wach sind
       (auch bei viel nächtlicher Aktivität bleibt der Bot "müde").
    3. excited - viele aktive User in den letzten ACTIVITY_WINDOW_MINUTES.
    4. bored   - seit BORED_INACTIVITY_MINUTES keine einzige Nachricht mehr.
    5. neutral - Fallback, wenn keine der obigen Bedingungen zutrifft.

    "happy" wird von diesen Regeln aktuell nicht ausgelöst (die Spec nennt es
    als gültigen Zustand, aber keine der hier vorgegebenen Regeln führt dazu) -
    bewusst als Wert unterstützt (Schema, Text-/Status-Pools), aber ungenutzt,
    bis eine spätere Phase eine konkrete Regel dafür festlegt.
    """
    if recent_violation_count >= annoyed_violation_threshold:
        return "annoyed"
    if is_night_hour(current_hour, night_start_hour, night_end_hour):
        return "sleepy"
    if active_user_count >= excited_active_user_threshold:
        return "excited"
    if minutes_since_last_message is None or minutes_since_last_message >= bored_inactivity_minutes:
        return "bored"
    return "neutral"


def roll_personality_response(frequency_percent) -> bool:
    """True mit Wahrscheinlichkeit frequency_percent% (0-100). Bei 0 immer
    False, bei 100 immer True (Grenzfälle bewusst exakt, kein Rundungsfehler)."""
    frequency_percent = max(0.0, min(100.0, float(frequency_percent)))
    if frequency_percent <= 0:
        return False
    if frequency_percent >= 100:
        return True
    return random.uniform(0, 100) < frequency_percent


def pick_variant(pool: dict, mood_key: str, humor_level: str, **format_kwargs) -> str:
    """Wählt zufällig eine Formulierung aus dem Pool für `mood_key`, eingeschränkt
    auf Varianten, deren Humor-Stufe <= der eingestellten humor_level liegt
    (low sieht nur "low"-Varianten, medium zusätzlich "medium", high alle) -
    genau das Verhalten, das personality_humor_level laut Vorgabe haben soll:
    es beeinflusst, welcher Text-Pool (wie verspielt) genutzt wird.
    Fällt auf den "neutral"-Pool zurück, falls mood_key unbekannt ist oder für
    die gewählte Humor-Stufe (theoretisch) keine Variante existiert.

    Seit Phase B3 kommen die Texte aus der admin-editierbaren personality_texts-
    Tabelle statt aus hartcodierten, von Entwicklern geschriebenen Strings - ein
    Admin könnte also versehentlich einen falschen/fehlenden {platzhalter}
    eintragen. `.format()` würde dabei mit einem ungefangenen KeyError crashen
    (z.B. bei der Willkommensnachricht in on_member_join, die pick_variant()
    NICHT in try/except aufruft) - daher hier abgefangen: lieber kein Text als
    ein abgestürzter Event-Handler."""
    max_tier = HUMOR_TIERS.get(humor_level, 1)
    variants = pool.get(mood_key) or pool.get("neutral", [])
    candidates = [text for tier, text in variants if HUMOR_TIERS.get(tier, 1) <= max_tier]
    if not candidates:
        candidates = [text for _, text in pool.get("neutral", [])]
    if not candidates:
        return ""
    chosen = random.choice(candidates)
    try:
        return chosen.format(**format_kwargs)
    except (KeyError, IndexError, ValueError):
        return ""


def pick_status_text(pool: dict, mood_key: str) -> str:
    """`pool` hat dieselbe Form wie bei pick_variant() ({mood: [(tier, text), ...]}),
    der Tier-Anteil wird hier ignoriert (Status-Texte sind nicht nach Humor-Level
    gestaffelt). Fällt auf einen festen Text zurück, falls der Admin sowohl den
    Pool für `mood_key` als auch den "neutral"-Pool komplett geleert hat."""
    variants = pool.get(mood_key) or pool.get("neutral") or []
    texts = [text for _, text in variants]
    if not texts:
        return "🌿 Blackforest Bot"
    return random.choice(texts)


# --- Text-Varianten-Pools: SEED-VORLAGEN fuer die DB (Phase B3) ---
# Format: {mood: [(humor_tier, text_mit_platzhaltern), ...]}. Alle Formulierungen
# sind absichtlich freundlich - auch bei "annoyed"/"bored" niemals unhöflich.
#
# Seit Phase B3 sind diese Pools admin-editierbar (Backend-Seite
# "/personality/texts", Tabelle "personality_texts") - die Dicts/Listen hier
# dienen nur noch als einmalige Seed-Vorlage beim allerersten Start (siehe
# database._seed_personality_texts(), TEXT_POOL_SEEDS unten) und werden zur
# Laufzeit NICHT mehr direkt gelesen. cogs/fun.py, cogs/moderation.py,
# cogs/quiz.py und cogs/personality.py holen ihre Pools stattdessen ueber
# database.get_personality_text_pool(<name>).

# Guten-Morgen-Nachrichten, die der Bot einmal taeglich von selbst in einen
# einstellbaren Kanal schickt (siehe cogs/personality.py, _maybe_send_morning_greeting).
#
# BEWUSST OHNE Mood und ohne Humorstufe - Vorgabe von Patrick am 03.09.2026:
# "guten morgen unabhaengig von mood". Deshalb eine flache Liste statt der
# verschachtelten Struktur der anderen Pools, und has_mood/has_tier stehen in
# TEXT_POOL_INFO auf False. Die Bearbeitungsseite im Backend blendet die beiden
# Auswahlfelder dann von selbst aus.
#
# BEWUSST OHNE {name}: Diese Nachricht richtet sich an den ganzen Kanal, nicht an
# eine einzelne Person. Die 89 Texte des alten >gumo-Befehls waren durchgehend
# auf {name} gemuenzt und deshalb nicht uebernehmbar (sie waren ausserdem
# unbrauchbar, siehe Loeschung von bot/data/gumo.txt - die Datei enthielt
# Python-Quelltext statt reiner Textzeilen).
MORNING_GREETINGS = [
    "Guten Morgen zusammen! ☀️",
    "Einen wunderschönen guten Morgen euch allen!",
    "Moin! Ich hoffe, ihr seid gut aufgestanden. ☕",
    "Guten Morgen! Der Kaffee läuft, die Pflanzen wachsen. 🌱",
    "Hallo und guten Morgen! Auf einen guten Tag.",
    "Guten Morgen! Ein neuer Tag, ein neues Stück Wachstum. 🌿",
    "Moin zusammen — schön, dass ihr da seid.",
    "Guten Morgen! Was steht heute bei euch an?",
    "Einen guten Start in den Tag, ihr Lieben! ✨",
    "Guten Morgen! Denkt ans Gießen. 💧",
    "Aufstehen, die Sonne ist auch schon wach. Guten Morgen! ☀️",
    "Guten Morgen! Heute ist ein guter Tag, um etwas wachsen zu lassen.",
    "Moin! Erst der Kaffee, dann der Rest. ☕",
    "Guten Morgen zusammen! Ich hoffe, ihr habt gut geschlafen. 😊",
    "Ein frischer Morgen, ein frischer Anfang. Guten Morgen!",
    "Guten Morgen! Die Lampen sind an, es kann losgehen. 💡",
    "Hallo in die Runde und einen schönen guten Morgen!",
    "Guten Morgen! Nehmt euch heute etwas Schönes vor. 🌻",
    "Moin moin! Wie ist die Lage bei euch?",
    "Guten Morgen! Auch die kleinen Fortschritte zählen. 🌱",
    "Einen wunderbaren Morgen euch! Lasst es ruhig angehen.",
    "Guten Morgen! Ich bin wach — mehr oder weniger. 😴☕",
    "Hallo zusammen, guten Morgen! Auf ein gutes Miteinander heute.",
    "Guten Morgen! Vergesst nicht, mal nach euren Pflanzen zu schauen. 👀🌿",
    "Ein neuer Tag wartet. Guten Morgen! 🌅",
    "Guten Morgen! Heute schon jemanden zum Lächeln gebracht?",
    "Moin! Möge euer Kaffee stark und euer Tag entspannt sein. ☕",
    "Guten Morgen zusammen! Erzählt mal, was heute ansteht.",
    "Guten Morgen! Geduld ist beim Wachsen die halbe Miete. 🌱",
    "Schönen guten Morgen! Ich wünsche euch einen richtig guten Tag. ☀️",
]

QUIZ_CORRECT_VARIANTS = {
    "happy": [
        ("low", "Du hast **{points} Punkte** gewonnen."),
        ("medium", "Du hast **{points} Punkte** gewonnen! Läuft bei dir heute! 😊"),
        ("high", "Volltreffer! **{points} Punkte** für dich! Was für ein Tag! 🎉"),
    ],
    "neutral": [
        ("low", "Du hast **{points} Punkte** gewonnen."),
        ("medium", "Du hast **{points} Punkte** gewonnen! 🎉"),
    ],
    "excited": [
        ("low", "Richtig! **{points} Punkte** für dich."),
        ("medium", "Richtig! **{points} Punkte** für dich - hier ist gerade richtig was los! 🔥"),
        ("high", "Boom! **{points} Punkte**! Und weiter geht die Post hier! 🚀"),
    ],
    "bored": [
        ("low", "Richtig! **{points} Punkte** für dich."),
        ("medium", "Richtig! **{points} Punkte** für dich - schön, dass wenigstens du gerade aktiv bist. 😌"),
    ],
    "sleepy": [
        ("low", "Richtig. **{points} Punkte** für dich."),
        ("medium", "Richtig! **{points} Punkte** für dich 😴 auch spät noch fit im Kopf."),
    ],
    "annoyed": [
        ("low", "Richtig! **{points} Punkte** für dich."),
        ("medium", "Richtig! **{points} Punkte** für dich - schön, dass wenigstens das gerade rund läuft. 👍"),
    ],
}

WELCOME_VARIANTS = {
    "happy": [
        ("low", "Willkommen {mention} auf unserem Server."),
        ("medium", "Willkommen {mention} auf unserem Server! Schön, dass du da bist! 😊"),
        ("high", "Willkommen {mention}!! Heute ist ein super Tag für einen Neuzugang! 🎉"),
    ],
    "neutral": [
        ("low", "Willkommen {mention} auf unserem Server."),
        ("medium", "Willkommen {mention} auf unserem Server! 👋"),
    ],
    "excited": [
        ("low", "Willkommen {mention} auf unserem Server."),
        ("medium", "Willkommen {mention}! Perfektes Timing, hier ist gerade richtig was los! 🔥"),
    ],
    "bored": [
        ("low", "Willkommen {mention} auf unserem Server."),
        ("medium", "Willkommen {mention}! Schön, dass du da bist - war gerade recht ruhig hier. 😌"),
    ],
    "sleepy": [
        ("low", "Willkommen {mention} auf unserem Server."),
        ("medium", "Willkommen {mention} 😴 Schön dich zu sehen, auch wenn's gerade spät ist."),
    ],
    "annoyed": [
        ("low", "Willkommen {mention} auf unserem Server."),
        ("medium", "Willkommen {mention}! Hier war gerade viel los, aber schön dass du jetzt auch da bist. 👋"),
    ],
}

# Feste Ziel-Moods fuer spontane Nachrichten (Spec: nur bei einem Wechsel ZU einem
# dieser beiden Moods). Bewusst eine EIGENE, hartcodierte Konstante statt aus den
# Schluesseln von SPONTANEOUS_VARIANTS (unten) abgeleitet - SPONTANEOUS_VARIANTS
# ist seit Phase B3 nur noch die SEED-Vorlage fuer die DB-editierbaren Texte
# (siehe database.get_personality_text_pool()); wuerde man WELCHE Moods spontane
# Nachrichten ausloesen an den DB-Pool-Inhalten festmachen, koennte ein Admin durch
# bloszes Hinzufuegen einer neuen Text-Variante ungewollt das Verhalten aendern.
SPONTANEOUS_TARGET_MOODS = ("excited", "annoyed")

# Kurze, zurückhaltende Nachrichten für spontane Mood-Wechsel-Ankündigungen
# (siehe cogs/personality.py) - nur für die beiden Ziel-Moods aus der Spec
# (excited/annoyed) überhaupt nötig, da nur bei diesen beiden ein spontaner
# Hinweis vorgesehen ist. Dient seit Phase B3 nur noch als Seed-Vorlage fuer die
# DB-editierbaren Texte (personality_texts-Tabelle), wird zur Laufzeit nicht mehr
# direkt gelesen.
SPONTANEOUS_VARIANTS = {
    "excited": [
        ("low", "Hier ist gerade einiges los! 🔥"),
        ("medium", "Wow, hier ist gerade richtig was los! Weiter so! 🔥"),
        ("high", "Boah, hier brennt gerade die Bude! Das liebe ich! 🚀🔥"),
    ],
    "annoyed": [
        ("low", "Kurzer Hinweis: gerade gab es einige Regelverstöße."),
        ("medium", "Kleiner Hinweis: hier gab es zuletzt einige Regelverstöße - bitte bleibt fair zueinander. 🙏"),
    ],
}

# Antworten bei direkter @Erwaehnung des Bots (personality_response_mode =
# "mention_only", Spec 1, Phase B4/Nachtrag) - im Unterschied zu SPONTANEOUS_VARIANTS
# (nur excited/annoyed, unaufgefordert in einen Kanal gepostet) deckt dieser Pool
# ALLE Moods ab (analog zu WELCOME_VARIANTS), da eine direkte Erwaehnung immer eine
# Reaktion verdient, unabhaengig von der aktuellen Mood - aber NUR als Reaktion,
# nie von sich aus.
MENTION_REPLY_VARIANTS = {
    "happy": [
        ("low", "Ja, {mention}?"),
        ("medium", "Hey {mention}! Was gibt's? Heute läuft's bei mir richtig gut. 😊"),
    ],
    "neutral": [
        ("low", "Ja, {mention}?"),
        ("medium", "Hey {mention}, wie kann ich helfen? 👋"),
    ],
    "excited": [
        ("low", "Ja, {mention}! Was los?"),
        ("medium", "{mention}! Perfektes Timing, hier ist gerade richtig was los! 🔥"),
    ],
    "bored": [
        ("low", "Ja, {mention}?"),
        ("medium", "Oh, {mention}! Endlich passiert mal was hier. 😌"),
    ],
    "sleepy": [
        ("low", "Ja, {mention}? *gähn*"),
        ("medium", "Hey {mention} 😴 bin gerade etwas müde, aber sag an."),
    ],
    "annoyed": [
        ("low", "Ja, {mention}?"),
        ("medium", "Hey {mention}, hier war gerade ganz schön was zu tun - aber sag an! 👋"),
    ],
}

STATUS_VARIANTS = {
    "happy": ["🏆 Rekordjagd", "😄 Bestens gelaunt"],
    "neutral": ["🧠 Ich denke nach...", "👀 Ich beobachte euch"],
    "excited": ["🔥 Hier ist was los", "🎮 Zeit zum Spielen", "🧠 Quiz läuft"],
    "bored": ["👀 Ich beobachte euch", "🧠 Ich denke nach..."],
    "sleepy": ["😴 Ich bin müde"],
    "annoyed": ["👀 Ich beobachte euch"],
}

# Rein kosmetische "Bot-Systemmeldungen" fuer das Humor-Zufallsereignis (Phase
# 10) - bewusst OHNE Mood-Bezug (die Spec beschreibt sie als reinen Spass ohne
# Mechanik-Wirkung), nutzt aber denselben Humor-Level-gestaffelten Mechanismus
# wie pick_variant() oben, statt eine zweite, eigene Auswahl-Logik zu bauen.
HUMOR_EVENT_VARIANTS = [
    ("low", "🤖 Systemmeldung: Alles funktioniert wie erwartet."),
    ("medium", "🤖 Bot-Systemmeldung: Ich habe gerade nachgedacht. Ergebnis: keine Ahnung. 🤔"),
    ("medium", "🤖 Zwischenbericht: Alles im grünen Bereich. Vermutlich."),
    ("high", "🤖 Bot-Systemmeldung: Ich habe kurz über den Sinn des Lebens nachgedacht. Fazit: 42. ✨"),
    ("high", "🤖 Achtung, Achtung: Ich habe gerade geblinzelt. Das war's schon. 😄"),
]


def pick_humor_event_text(pool: list, humor_level: str) -> str:
    """`pool` ist eine Liste aus (tier, text)-Paaren, gleiche Form wie
    HUMOR_EVENT_VARIANTS. Gibt "" zurück, falls der Admin den gesamten Pool
    geleert hat - der Aufrufer (cogs/personality.py) prüft das vor dem Senden."""
    if not pool:
        return ""
    max_tier = HUMOR_TIERS.get(humor_level, 1)
    candidates = [text for tier, text in pool if HUMOR_TIERS.get(tier, 1) <= max_tier]
    if not candidates:
        candidates = [text for _, text in pool]
    if not candidates:
        return ""
    return random.choice(candidates)


# Registry aller Text-Pools fuer die generische Seed-Funktion in database.py
# (_seed_personality_texts()) - ein neuer Pool-Name braucht nur einen weiteren
# Eintrag hier, keine Aenderung an der Seed-Funktion selbst. "shape" beschreibt
# das Datenformat, damit die Seed-Funktion weiss, wie mood/tier zu befuellen sind:
#   "mood_tier"   -> {mood: [(tier, text), ...]}          (hello/quiz_correct/welcome/spontaneous)
#   "mood_only"   -> {mood: [text, ...]}                   (status, keine Tier-Staffelung)
#   "tier_only"   -> [(tier, text), ...]                   (humor_event, kein Mood-Bezug)
TEXT_POOL_SEEDS = {
    "morning": ("plain", MORNING_GREETINGS),
    "quiz_correct": ("mood_tier", QUIZ_CORRECT_VARIANTS),
    "welcome": ("mood_tier", WELCOME_VARIANTS),
    "spontaneous": ("mood_tier", SPONTANEOUS_VARIANTS),
    "mention_reply": ("mood_tier", MENTION_REPLY_VARIANTS),
    "status": ("mood_only", STATUS_VARIANTS),
    "humor_event": ("tier_only", HUMOR_EVENT_VARIANTS),
}

# Anzeige-Metadaten fuer die Backend-Bearbeitungsseite ("/personality/texts") -
# welcher Pool wo benutzt wird und welche {platzhalter} im Text erlaubt/nötig
# sind, damit der Admin dort keine kaputten .format()-Aufrufe erzeugen kann.
TEXT_POOL_INFO = {
    "morning": {"label": "Guten-Morgen-Nachricht (täglich, automatisch)", "placeholders": "(keine)", "has_mood": False, "has_tier": False},
    "quiz_correct": {"label": "Quiz: richtige Antwort", "placeholders": "{points}", "has_mood": True, "has_tier": True},
    "welcome": {"label": "Willkommensnachricht (neues Mitglied)", "placeholders": "{mention}", "has_mood": True, "has_tier": True},
    "spontaneous": {"label": "Spontane Mood-Wechsel-Nachricht", "placeholders": "(keine)", "has_mood": True, "has_tier": True},
    "mention_reply": {"label": "Antwort bei @Erwähnung (mention_only-Modus)", "placeholders": "{mention}", "has_mood": True, "has_tier": True},
    "status": {"label": "Bot-Status-Text", "placeholders": "(keine)", "has_mood": True, "has_tier": False},
    "humor_event": {"label": "Humor-Zufallsereignis", "placeholders": "(keine)", "has_mood": False, "has_tier": True},
}
