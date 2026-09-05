"""Modul XP & Level — Schema, Achievements und Bedingungen an einer Stelle.

XP entsteht aus drei Quellen (Chat, Quizantworten, Zufallsereignisse), gehoert
aber diesem Modul und nicht dem Quiz: Wer nur im Chat schreibt, sammelt genauso.
"""
import time
from datetime import datetime, timezone
import achievements
import module
import schema_helfer
import verdiente_rollen
from achievements import Achievement, Bedingungstyp
from verdiente_rollen import Bedingungsart



def _db():
    """Zugriff auf die Kern-Funktionen (get_setting, Achievement-Freischaltung).

    Erst beim Aufruf geholt, aus demselben Grund wie bei _conn(): Das Laden des
    Moduls darf database nicht voraussetzen.
    """
    import database

    return database


def _cfg():
    """Die Standardwerte aus config. Erst beim Aufruf geholt - config verlangt
    dotenv und einen gesetzten DISCORD_TOKEN, was beim blossen Laden des Moduls
    nicht vorausgesetzt werden darf."""
    import config

    return config

def _conn():
    """Datenbankverbindung, erst beim Aufruf geholt.

    database wird BEWUSST nicht auf oberster Ebene importiert: Beim blossen
    Laden dieses Moduls (Schema, Achievements, Bedingungen) darf es nicht
    gebraucht werden, sonst zieht es config und dotenv nach sich - daran ist die
    CI schon einmal gescheitert. Wenn diese Funktionen laufen, laeuft ohnehin
    der Bot.
    """
    from database import get_connection

    return get_connection()


NAME = "xp_level"

SCHEMA = (
    """-- XP/Level-System (Spec 1), bewusst getrennt von players.score (Quiz-Highscore).
-- Kein fester Levelaufstieg pro Zeile - level wird bei jedem award_xp()-Aufruf
-- aus xp neu berechnet und hier nur zur schnellen Anzeige mitgespeichert.
CREATE TABLE IF NOT EXISTS user_xp (
    discord_user_id TEXT PRIMARY KEY,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    updated_at TEXT
);""",
)


def _migrationen(conn):
    """Tageszaehler fuer das XP-Limit, nachtraeglich hinzugekommen."""
    schema_helfer.ensure_column(conn, "user_xp", "daily_message_xp", "INTEGER DEFAULT 0")
    schema_helfer.ensure_column(conn, "user_xp", "daily_xp_date", "TEXT")


def _xp(discord_user_id) -> int:
    import database
    with _conn() as conn:
        row = conn.execute(
            "SELECT xp FROM user_xp WHERE discord_user_id = ?", (str(discord_user_id),)
        ).fetchone()
    return row["xp"] if row else 0


def _level(discord_user_id) -> int:
    import database
    with _conn() as conn:
        row = conn.execute(
            "SELECT level FROM user_xp WHERE discord_user_id = ?", (str(discord_user_id),)
        ).fetchone()
    return row["level"] if row else 0


def _mindestens_level(stufe: int):
    return lambda kontext: kontext.get("neues_level", 0) >= stufe


# Bedingungsart dieses Moduls - die Schwelle steht in der Datenbank und ist im
# Backend aenderbar (siehe achievements.Bedingungstyp).
achievements.BEDINGUNGEN.eintragen(
    Bedingungstyp("level_erreicht", "Erreichtes Level", NAME, "level_up",
                  "neues_level", einheit="Level",
                  hinweis="Level ergibt sich aus allen gesammelten XP - Chat, Quiz und Zufallsereignisse.")
)


for _a in (
    Achievement("LEVEL_10", "Aufsteiger", "Level 10 erreicht.",
                50, False, NAME, "level_up", "level_erreicht", 10),
    Achievement("LEVEL_25", "Veteran", "Level 25 erreicht.",
                150, False, NAME, "level_up", "level_erreicht", 25),
):
    achievements.REGISTRATUR.eintragen(_a)


for _b in (
    Bedingungsart("level", "Erreichtes Level", "Level",
                  "Level ergibt sich aus den gesammelten XP (Chat, Quiz, Zufallsereignisse).",
                  NAME, _level),
    Bedingungsart("xp", "Gesammelte XP", "XP",
                  "Alle XP zusammen, unabh\u00e4ngig davon, woher sie stammen.",
                  NAME, _xp),
):
    verdiente_rollen.REGISTRATUR.eintragen(_b)


def _uebersicht(discord_user_id) -> dict:
    """Level 1 als Standard, nicht 0: Wer noch keine XP hat, ist trotzdem auf
    Level 1 - so rechnet auch _level_for_xp()."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT xp, level FROM user_xp WHERE discord_user_id = ?",
            (str(discord_user_id),),
        ).fetchone()
    return {"xp": row["xp"] if row else 0, "level": row["level"] if row else 1}


module.REGISTRATUR.eintragen(module.Modul(
    name=NAME,
    label="XP & Level",
    schema=SCHEMA,
    migrationen=(_migrationen,),
    # "level_up" liefert database.award_xp() selbst mit (new_level) - dieses
    # Modul muss dafuer nichts nachschlagen.
    ereignis_kontext={},
    nutzer_uebersicht=_uebersicht,
    uebersicht_standard={"xp": 0, "level": 1},
))


# Sicherheitsgrenze gegen eine Endlosschleife bei base=growth=0.
_MAX_XP_LEVEL_ITER = 100_000


def _xp_threshold(level: int) -> float:
    """Kumulierte XP-Schwelle fuer ein Level, aus zwei konfigurierbaren Settings
    berechnet statt einer festen Tabelle (Spec 1 fordert eine konfigurierbare
    Levelkurve). Formel: schwelle(n) = base*(n-1) + growth*(n-1)*(n-2)/2 - das
    ergibt eine arithmetisch wachsende Kurve mit Zuwaechsen base, base+growth,
    base+2*growth, ... Mit den Defaults base=100/growth=50 trifft das exakt die
    Beispielwerte aus Spec 1: Level 1->0, 2->100, 3->250, 4->450 XP."""
    base = float(_db().get_setting("xp_level_base", _cfg().DEFAULT_SETTINGS["xp_level_base"]))
    growth = float(_db().get_setting("xp_level_growth", _cfg().DEFAULT_SETTINGS["xp_level_growth"]))
    n = level
    return base * (n - 1) + growth * (n - 1) * (n - 2) / 2

def _level_for_xp(xp: float) -> int:
    """Hoechstes Level, dessen Schwelle <= xp ist (mindestens Level 1)."""
    level = 1
    for _ in range(_MAX_XP_LEVEL_ITER):
        if _xp_threshold(level + 1) > xp:
            break
        level += 1
    return level

def _apply_community_bonus(amount):
    """Multipliziert `amount`, falls gerade ein Community-Bonus-Event laeuft
    (bot_state.community_bonus_until in der Zukunft) - siehe cogs/personality.py,
    _trigger_community_event(). Laeuft das Fenster ab, wird hier bewusst NICHTS
    zurueckgesetzt (unschaedlich - der naechste Event-Check ueberschreibt den
    Wert ohnehin bei Bedarf neu)."""
    until_str = _db().get_bot_state("community_bonus_until")
    if not until_str:
        return amount
    until = datetime.fromisoformat(until_str)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= until:
        return amount
    multiplier = float(_db().get_setting(
        "event_community_bonus_multiplier", _cfg().DEFAULT_SETTINGS["event_community_bonus_multiplier"]
    ))
    return amount * multiplier

def _apply_quiz_double_xp(amount):
    """Verdoppelt `amount` GENAU EINMAL, wenn ein Quiz-Blitz-Event aktiv ist
    (bot_state.quiz_double_xp_pending == "1") - konsumiert das Flag sofort,
    damit es wirklich nur fuer die naechste richtige Antwort gilt, nicht fuer
    mehrere. Bewusst kein Zeitlimit (anders als der Community-Bonus): die Spec
    beschreibt es als "die naechste richtige Antwort", nicht als Zeitfenster."""
    if _db().get_bot_state("quiz_double_xp_pending") != "1":
        return amount
    _db().set_bot_state("quiz_double_xp_pending", "")
    return amount * 2

# --------------------------------------------------------------------------
# Abfragen und Aenderungen an den eigenen Tabellen
#
# Am 03.09.2026 aus database.py hierher gezogen. Vorher standen sie zentral -
# ein entferntes Modul haette dort 3 Funktionen zurueckgelassen, die auf
# Tabellen zugreifen, die es nicht mehr gibt.
#
# get_connection wird INNERHALB der Funktionen importiert: Beim blossen Laden
# des Moduls (Schema, Achievements, Bedingungen) darf database nicht gebraucht
# werden, sonst zieht es config und dotenv nach sich - daran ist die CI schon
# einmal gescheitert.
# --------------------------------------------------------------------------

def get_xp_leaderboard(limit: int = 15):
    """Rangliste ueber die SERVERWEITEN XP - Modul "XP & Level".

    Bewusst getrennt von get_leaderboard(), das die Quiz-Punkte aus der Tabelle
    players liefert. Es sind zwei verschiedene Wertungen mit zwei verschiedenen
    Datentoepfen: Quiz-Punkte bekommt man nur durchs Quiz, XP dagegen auch durch
    Chatnachrichten und Zufallsereignisse. Jemand kann in der einen Liste ganz
    oben und in der anderen gar nicht auftauchen.

    Sortiert nach XP, bei Gleichstand nach Level - damit die Reihenfolge stabil
    bleibt und nicht bei jedem Aufruf wechselt.
    """
    with _conn() as conn:
        return conn.execute(
            "SELECT discord_user_id, xp, level FROM user_xp "
            "ORDER BY xp DESC, level DESC, discord_user_id ASC LIMIT ?",
            (limit,),
        ).fetchall()



def get_user_xp(discord_user_id) -> dict:
    """Aktueller XP-/Level-Stand eines Users, inkl. Restdistanz zum naechsten
    Level - fuer Anzeige im Backend/Bot-Command. Nutzer ohne Eintrag gelten als
    Level 1 mit 0 XP, ohne dass dafuer eine Zeile angelegt werden muss."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT xp, level FROM user_xp WHERE discord_user_id = ?", (str(discord_user_id),)
        ).fetchone()
    xp = row["xp"] if row else 0
    level = row["level"] if row else 1
    next_threshold = _xp_threshold(level + 1)
    return {
        "xp": xp,
        "level": level,
        "next_level_xp": next_threshold,
        "xp_to_next": max(0, next_threshold - xp),
    }



def award_xp(discord_user_id, amount: int, reason: str = "generic", bypass_cooldown: bool = False,
             _check_achievements: bool = True):
    """Vergibt XP und aktualisiert ggf. das Level. Gibt ein Dict mit dem neuen
    Stand zurueck, oder None, wenn die Vergabe wegen des Cooldowns unterdrueckt
    wurde (nur relevant, wenn bypass_cooldown=False).

    bypass_cooldown=True ist fuer den Quiz-Erfolg gedacht (reason="quiz"): der
    Quiz-eigene Cooldown (quiz_cooldown_seconds) regelt dort das Spam-Problem
    bereits, ein zweiter, unabhaengiger XP-Cooldown wuerde nur verwirren. Fuer
    generische/zukuenftige Quellen (z.B. Nachrichtenaktivitaet) bleibt der
    Cooldown aktiv, damit XP nicht pro Nachricht vergeben wird (Spec 1).

    Die eigentliche Levelaufstiegs-Ankuendigung (Nachricht an den User) ist NICHT
    Teil dieser Funktion - sie bereitet nur die Information (leveled_up/new_level)
    fuer einen spaeteren Aufrufer vor.

    Zentrale Achievement-Anbindung (Spec 1, Phase 6, "keine unabhaengigen
    Parallelmechanismen"): statt dass jeder Aufrufer separat an Achievements
    denken muss, meldet award_xp() selbst zwei der drei Ereignisse - "level_up"
    nach jedem Levelaufstieg, und "quiz_correct", wenn reason=="quiz" ist. Das
    dritte Ereignis ("question_submitted") meldet submit_question().

    WELCHE Achievements auf diese Ereignisse reagieren, weiss diese Datei seit
    dem 03.09.2026 bewusst NICHT mehr - das steht in den Modulen selbst
    (achievements_quiz.py, achievements_xp_level.py), damit sich ein Modul
    entfernen laesst, ohne hier Reste zu hinterlassen. Siehe achievements.py.
    _check_achievements=False wird NUR intern von _db().check_and_unlock_achievements()
    selbst gesetzt, wenn es die xp_reward eines gerade freigeschalteten
    Achievements gutschreibt - verhindert, dass ein Achievement-Bonus rekursiv
    erneut denselben Levelaufstieg auswertet (bei den aktuell kleinen Belohnungen
    ohnehin kaum relevant, aber sauberer als sich darauf zu verlassen).

    reason="message" (Phase 7, Nachrichten-XP) unterliegt zusaetzlich einem
    taeglichen Limit (daily_xp_limit) - dafuer wird pro User ein persistenter
    Tageszaehler (daily_message_xp/daily_xp_date) in user_xp mitgefuehrt, der
    sich automatisch zuruecksetzt, sobald daily_xp_date nicht mehr das heutige
    Datum ist. Das Limit gilt AUSSCHLIESSLICH fuer diese Quelle - Quiz-/
    Achievement-XP sind davon unberuehrt. Wird das Limit erreicht, gibt die
    Funktion None zurueck (wie bei einer Cooldown-Sperre), OHNE ueberhaupt XP
    gutzuschreiben."""
    uid = str(discord_user_id)
    if not bypass_cooldown:
        cooldown = float(_db().get_setting("xp_cooldown_seconds", _cfg().DEFAULT_SETTINGS["xp_cooldown_seconds"]))
        last = _xp_cooldowns.get(uid)
        now = time.monotonic()
        if last is not None and cooldown > 0 and now - last < cooldown:
            return None
        _xp_cooldowns[uid] = now

    # Event-Modifikatoren (Phase 10, Zufallsereignisse): Community-Bonus
    # multipliziert JEDE Quiz-/Nachrichten-XP-Vergabe innerhalb eines
    # Zeitfensters; das Quiz-Blitz-Event verdoppelt zusaetzlich GENAU die
    # naechste richtige Quiz-Antwort (wird danach konsumiert, kein Zeitlimit -
    # gilt buchstaeblich fuer "die naechste Antwort", egal wann die kommt).
    # Bewusst NUR fuer reason in (quiz, message) - Achievement-Belohnungen
    # (reason="achievement:...") und generische Testaufrufe bleiben unberuehrt,
    # damit sich Boni nicht unkontrolliert ueberlagern.
    if reason in ("quiz", "message"):
        amount = _apply_community_bonus(amount)
    if reason == "quiz":
        amount = _apply_quiz_double_xp(amount)
    amount = int(round(amount))

    today = time.strftime("%Y-%m-%d")
    with _conn() as conn:
        row = conn.execute(
            "SELECT xp, level, daily_message_xp, daily_xp_date FROM user_xp WHERE discord_user_id = ?", (uid,)
        ).fetchone()
        old_xp = row["xp"] if row else 0
        old_level = row["level"] if row else 1
        # Tageszaehler gilt nur, wenn er vom heutigen Tag stammt - sonst ist er
        # (noch) nicht zurueckgesetzt und wird hier als 0 behandelt.
        daily_xp_so_far = row["daily_message_xp"] if row and row["daily_xp_date"] == today else 0

        if reason == "message":
            daily_limit = int(_db().get_setting("daily_xp_limit", _cfg().DEFAULT_SETTINGS["daily_xp_limit"]))
            # Echte Obergrenze: blockt bereits die Nachricht, die die Grenze
            # ueberschreiten WUERDE (nicht erst die naechste danach) - so kann
            # daily_message_xp den konfigurierten Wert nie uebersteigen. Genau
            # auf der Grenze landen (Summe == Limit) bleibt weiterhin erlaubt.
            if daily_xp_so_far + amount > daily_limit:
                return None

        new_xp = old_xp + amount
        new_level = _level_for_xp(new_xp)
        if reason == "message":
            new_daily_xp = daily_xp_so_far + amount
            new_daily_date = today
        else:
            # Nicht-Nachrichten-XP laesst den Tageszaehler unangetastet - dafuer
            # den ROHEN gespeicherten Stand zurueckschreiben, nicht die oben ggf.
            # auf 0 normalisierte Variante (die waere nur fuer die Limit-Pruefung
            # gedacht, nicht zum dauerhaften Ueberschreiben eines noch gueltigen
            # Zaehlers eines anderen Tages).
            new_daily_xp = row["daily_message_xp"] if row else 0
            new_daily_date = row["daily_xp_date"] if row else None
        conn.execute(
            "INSERT INTO user_xp (discord_user_id, xp, level, updated_at, daily_message_xp, daily_xp_date) "
            "VALUES (?, ?, ?, datetime('now'), ?, ?) "
            "ON CONFLICT(discord_user_id) DO UPDATE SET xp = excluded.xp, level = excluded.level, "
            "updated_at = excluded.updated_at, daily_message_xp = excluded.daily_message_xp, "
            "daily_xp_date = excluded.daily_xp_date",
            (uid, new_xp, new_level, new_daily_xp, new_daily_date),
        )
        conn.commit()

    leveled_up = new_level > old_level
    unlocked_achievements = []
    if _check_achievements:
        if leveled_up:
            unlocked_achievements += _db().check_and_unlock_achievements(uid, "level_up", new_level=new_level)
        if reason == "quiz":
            unlocked_achievements += _db().check_and_unlock_achievements(
                uid, "quiz_correct", hour=time.localtime().tm_hour
            )

    return {
        "xp": new_xp,
        "old_level": old_level,
        "new_level": new_level,
        "leveled_up": leveled_up,
        "reason": reason,
        "unlocked_achievements": unlocked_achievements,
    }
