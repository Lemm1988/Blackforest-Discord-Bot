"""Personality/Mood (Spec 1, Phasen 2+8): deterministischer interner
Verhaltenszustand, KEINE echte Emotion/KI. Reine Regelauswertung ohne LLM-
Aufrufe - siehe bot/mood.py für die eigentliche (isoliert testbare) Logik.

Haeufigkeit, Ort und Ton sind laut ausdruecklicher Vorgabe NICHT fest
einprogrammiert, sondern ausschliesslich ueber Settings steuerbar (Backend-
Seite "/personality"). Komplett deaktivierbar ueber personality_enabled, ohne
dass sich bestehende Befehle dabei aendern (siehe cogs/fun.py, cogs/quiz.py,
cogs/moderation.py: bei personality_enabled=0 IMMER die alte neutrale Antwort).

Ein einzelner "Herzschlag"-Task (jede Minute) prueft, ob Mood-Neubewertung
bzw. Status-Rotation faellig sind - die eigentlichen Intervalle
(mood_update_interval_minutes, status_rotation_minutes) sind Admin-Settings,
die sich OHNE Bot-Neustart aendern lassen sollen; ein discord.ext.tasks.loop
mit fest im Code verankertem Intervall wuerde das nicht erlauben.
"""
import asyncio
import random
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

import announcements
import config
import database
import modul_xp_level
import events
import mood
import scheduling


def _minutes_since(iso_timestamp: str) -> float:
    then = datetime.fromisoformat(iso_timestamp)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 60


class Personality(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Server-weite (nicht pro-User) Aktivitaetsverfolgung fuer die Mood-
        # Erkennung - bewusst nur im Arbeitsspeicher (muss keinen Bot-Neustart
        # ueberstehen, ein Neustart darf die Mood einfach neu "einschwingen"
        # lassen, analog zum Quiz-/Spam-Cooldown-Muster).
        self._recent_messages = deque()  # (monotonic_ts, user_id)
        self._last_message_monotonic = None
        # Pro-User Mindestabstand zwischen zwei Erwaehnungs-Antworten
        # (mention_only-Modus) - rein interner Schutz gegen Antwort-Spam durch
        # wiederholtes @Erwaehnen, kein Admin-Setting (analog zu
        # mood.MINIMUM_SPONTANEOUS_HOURS: eine harte, im Code verankerte
        # Untergrenze statt einer weiteren Stellschraube).
        self._mention_reply_cooldowns = {}  # user_id -> monotonic_ts
        self._heartbeat.start()

    def cog_unload(self):
        self._heartbeat.cancel()

    def _mood_thresholds(self) -> dict:
        """Liest die 5 Mood-Erkennungsschwellen aus den Settings (Spec 1, Phase B3
        - urspruenglich in Phase 9 hartcodiert, siehe bot/mood.py). Ein Ort fuer
        alle 5 Werte statt an jeder Nutzungsstelle einzeln gelesen, da sowohl
        on_message() als auch _maybe_update_mood() sie brauchen."""
        return {
            "activity_window_minutes": float(database.get_setting(
                "mood_activity_window_minutes", config.DEFAULT_SETTINGS["mood_activity_window_minutes"]
            )),
            "excited_active_user_threshold": int(database.get_setting(
                "mood_excited_active_user_threshold", config.DEFAULT_SETTINGS["mood_excited_active_user_threshold"]
            )),
            "bored_inactivity_minutes": float(database.get_setting(
                "mood_bored_inactivity_minutes", config.DEFAULT_SETTINGS["mood_bored_inactivity_minutes"]
            )),
            "violation_window_minutes": int(database.get_setting(
                "mood_violation_window_minutes", config.DEFAULT_SETTINGS["mood_violation_window_minutes"]
            )),
            "annoyed_violation_threshold": int(database.get_setting(
                "mood_annoyed_violation_threshold", config.DEFAULT_SETTINGS["mood_annoyed_violation_threshold"]
            )),
        }

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        thresholds = self._mood_thresholds()
        now = time.monotonic()
        self._recent_messages.append((now, message.author.id))
        self._last_message_monotonic = now
        cutoff = now - max(thresholds["activity_window_minutes"], thresholds["bored_inactivity_minutes"]) * 60
        while self._recent_messages and self._recent_messages[0][0] < cutoff:
            self._recent_messages.popleft()

        await self._maybe_reply_to_mention(message)

    MENTION_REPLY_COOLDOWN_SECONDS = 30

    async def _maybe_reply_to_mention(self, message: discord.Message):
        """personality_response_mode == "mention_only": der Bot antwortet
        mood-gefaerbt, wenn er DIREKT erwaehnt wird (message.mentions enthaelt
        laut discord.py-Doku nur explizite @User-Erwaehnungen, kein @everyone/
        @here/Rollen-Mentions - genau das gewuenschte Verhalten ohne
        zusaetzlichen Filter). Bewusst NUR in diesem Modus, nicht zusaetzlich in
        channel_messages - die drei Modi sind als sich gegenseitig ausschliessende
        Eskalationsstufen gedacht (siehe /personality-Seite)."""
        if self.bot.user not in message.mentions:
            return
        if database.get_setting(
            "personality_enabled", config.DEFAULT_SETTINGS["personality_enabled"]
        ) != "1":
            return
        if database.get_setting(
            "personality_response_mode", config.DEFAULT_SETTINGS["personality_response_mode"]
        ) != "mention_only":
            return

        last_reply = self._mention_reply_cooldowns.get(message.author.id)
        now = time.monotonic()
        if last_reply is not None and now - last_reply < self.MENTION_REPLY_COOLDOWN_SECONDS:
            return

        frequency = database.get_setting(
            "personality_response_frequency", config.DEFAULT_SETTINGS["personality_response_frequency"]
        )
        if not mood.roll_personality_response(frequency):
            return

        humor_level = database.get_setting(
            "personality_humor_level", config.DEFAULT_SETTINGS["personality_humor_level"]
        )
        current_mood = database.get_bot_state("mood", "neutral")
        pool = database.get_personality_text_pool("mention_reply")
        text = mood.pick_variant(pool, current_mood, humor_level, mention=message.author.mention)
        if not text:
            return

        try:
            await message.reply(text, mention_author=False)
            self._mention_reply_cooldowns[message.author.id] = now
        except discord.HTTPException:
            pass

    @tasks.loop(minutes=1)
    async def _heartbeat(self):
        # EIN gemeinsamer Herzschlag-Task fuer alle periodischen Personality-
        # Aufgaben (Mood, Status, Zufallsereignisse) statt eines eigenen
        # tasks.loop pro Aufgabe - passt zum Projektprinzip, keine parallelen
        # Mechanismen fuer aehnliche periodische Ablaeufe zu bauen. Die
        # eigentlichen Intervalle bleiben pro Aufgabe unabhaengig ueber ihre
        # jeweiligen "last_*"-Zeitstempel in bot_state gesteuert.
        try:
            await self._maybe_update_mood()
        except Exception as exc:
            print(f"[personality] Fehler bei Mood-Update: {exc}")
        try:
            await self._maybe_rotate_status()
        except Exception as exc:
            print(f"[personality] Fehler bei Status-Rotation: {exc}")
        try:
            await self._maybe_check_random_event()
        except Exception as exc:
            print(f"[personality] Fehler bei Zufallsereignis-Check: {exc}")
        try:
            await self._maybe_send_morning_greeting()
        except Exception as exc:
            print(f"[personality] Fehler beim Morgengruss: {exc}")
        try:
            await self._maybe_check_server_records()
        except Exception as exc:
            print(f"[personality] Fehler bei Rekord-Check: {exc}")
        try:
            await self._maybe_check_milestones()
        except Exception as exc:
            print(f"[personality] Fehler bei Meilenstein-Check: {exc}")

    @_heartbeat.before_loop
    async def _before_heartbeat(self):
        await self.bot.wait_until_ready()

    # --- Mood-Neubewertung ---

    async def _maybe_update_mood(self):
        if database.get_setting("mood_enabled", config.DEFAULT_SETTINGS["mood_enabled"]) != "1":
            return

        interval = float(database.get_setting(
            "mood_update_interval_minutes", config.DEFAULT_SETTINGS["mood_update_interval_minutes"]
        ))
        last_change = database.get_bot_state("last_mood_change")
        if last_change and _minutes_since(last_change) < interval:
            return

        night_start = int(database.get_setting(
            "mood_night_start_hour", config.DEFAULT_SETTINGS["mood_night_start_hour"]
        ))
        night_end = int(database.get_setting(
            "mood_night_end_hour", config.DEFAULT_SETTINGS["mood_night_end_hour"]
        ))
        thresholds = self._mood_thresholds()
        current_hour = time.localtime().tm_hour
        recent_violations = database.count_recent_violations(minutes=thresholds["violation_window_minutes"])

        now_mono = time.monotonic()
        window_cutoff = now_mono - thresholds["activity_window_minutes"] * 60
        active_users = {uid for ts, uid in self._recent_messages if ts >= window_cutoff}
        minutes_since_last = None
        if self._last_message_monotonic is not None:
            minutes_since_last = (now_mono - self._last_message_monotonic) / 60

        new_mood = mood.evaluate_mood(
            current_hour=current_hour,
            night_start_hour=night_start,
            night_end_hour=night_end,
            recent_violation_count=recent_violations,
            active_user_count=len(active_users),
            minutes_since_last_message=minutes_since_last,
            excited_active_user_threshold=thresholds["excited_active_user_threshold"],
            bored_inactivity_minutes=thresholds["bored_inactivity_minutes"],
            annoyed_violation_threshold=thresholds["annoyed_violation_threshold"],
        )
        old_mood = database.get_bot_state("mood", "neutral")

        database.set_bot_state("mood", new_mood)
        database.set_bot_state("energy", str(len(active_users)))
        database.set_bot_state("last_mood_change", datetime.now(timezone.utc).isoformat())

        if new_mood != old_mood:
            await self._maybe_send_spontaneous_message(new_mood)

    # --- Dynamischer Status ---

    # --- Taegliche Guten-Morgen-Nachricht (03.09.2026) ---
    #
    # Ersetzt den frueheren >gumo-Befehl. BEWUSST unabhaengig von der Stimmung
    # (Vorgabe Patrick): eine flache Liste, jeder Text gleich wahrscheinlich -
    # deshalb random.choice() statt mood.pick_variant().
    #
    # Haengt am bestehenden Minuten-Herzschlag statt an einem eigenen
    # tasks.loop - dasselbe Prinzip wie bei Mood, Status und Zufallsereignissen.

    async def _maybe_send_morning_greeting(self):
        if database.get_setting(
            "morning_greeting_enabled", config.DEFAULT_SETTINGS["morning_greeting_enabled"]
        ) != "1":
            return

        # Lokale Zeit, nicht UTC: Die eingestellte Uhrzeit meint die Uhr an der
        # Wand. Der Pi laeuft auf Europe/Berlin, die Sommerzeitumstellung
        # erledigt damit das System und nicht dieser Code.
        senden, tagesmerker = scheduling.morgengruss_faellig(
            jetzt=datetime.now(),
            uhrzeit=database.get_setting(
                "morning_greeting_time", config.DEFAULT_SETTINGS["morning_greeting_time"]
            ),
            zuletzt_gegruesst=database.get_bot_state("last_morning_greeting"),
            nachlauf_minuten=int(database.get_setting(
                "morning_greeting_grace_minutes",
                config.DEFAULT_SETTINGS["morning_greeting_grace_minutes"],
            )),
        )
        if not senden:
            if tagesmerker:
                # Fenster verpasst (Pi war aus). Tag abhaken, damit der Gruss
                # nicht nachmittags doch noch herausrutscht.
                database.set_bot_state("last_morning_greeting", tagesmerker)
            return

        # Erst ab hier etwas kosten lassen: Kanal und Texte werden nur geholt,
        # wenn tatsaechlich gesendet wird - der Herzschlag laeuft jede Minute.
        channel_id = database.get_setting(
            "morning_greeting_channel_id", config.DEFAULT_SETTINGS["morning_greeting_channel_id"]
        )
        if not channel_id:
            print("[personality] Morgengruss ist an, aber kein Kanal eingestellt - uebersprungen.")
            return

        pool = database.get_personality_text_pool("morning")
        # Pool ohne Mood/Tier: alles liegt unter dem Schluessel "none",
        # die Tier-Angabe ist dort bedeutungslos (siehe database.get_personality_text_pool).
        texte = [text for _tier, text in pool.get("none", [])]
        if not texte:
            print("[personality] Morgengruss ist an, aber der Textpool 'morning' ist leer - uebersprungen.")
            return

        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            print(f"[personality] Morgengruss: Kanal {channel_id} nicht gefunden - uebersprungen.")
            return

        try:
            await channel.send(random.choice(texte))
        except discord.HTTPException as exc:
            # Kein Tagesmerker bei einem Fehlschlag: Beim naechsten Herzschlag
            # noch einmal versuchen, solange das Nachlauffenster laeuft.
            print(f"[personality] Morgengruss konnte nicht gesendet werden: {exc}")
            return

        database.set_bot_state("last_morning_greeting", tagesmerker)

    async def _maybe_rotate_status(self):
        if database.get_setting(
            "dynamic_status_enabled", config.DEFAULT_SETTINGS["dynamic_status_enabled"]
        ) != "1":
            return
        interval = float(database.get_setting(
            "status_rotation_minutes", config.DEFAULT_SETTINGS["status_rotation_minutes"]
        ))
        last_change = database.get_bot_state("last_status_change")
        if last_change and _minutes_since(last_change) < interval:
            return

        current_mood = database.get_bot_state("mood", "neutral")
        pool = database.get_personality_text_pool("status")
        text = mood.pick_status_text(pool, current_mood)
        try:
            await self.bot.change_presence(activity=discord.CustomActivity(name=text))
        except discord.HTTPException:
            pass
        database.set_bot_state("last_status_change", datetime.now(timezone.utc).isoformat())

    # --- Spontane Nachrichten (nur personality_response_mode=channel_messages) ---

    async def _maybe_send_spontaneous_message(self, new_mood: str):
        if database.get_setting(
            "personality_enabled", config.DEFAULT_SETTINGS["personality_enabled"]
        ) != "1":
            return
        mode = database.get_setting(
            "personality_response_mode", config.DEFAULT_SETTINGS["personality_response_mode"]
        )
        if mode != "channel_messages":
            return
        if new_mood not in mood.SPONTANEOUS_TARGET_MOODS:
            return  # nur bei den in der Spec genannten Ziel-Moods (excited/annoyed)

        admin_min_hours = float(database.get_setting(
            "personality_spontaneous_min_hours", config.DEFAULT_SETTINGS["personality_spontaneous_min_hours"]
        ))
        # Harte serverseitige Untergrenze - unabhaengig vom Admin-Settingwert
        # nochmal durchgesetzt (siehe mood.MINIMUM_SPONTANEOUS_HOURS).
        effective_min_hours = max(admin_min_hours, mood.MINIMUM_SPONTANEOUS_HOURS)

        last_sent = database.get_bot_state("last_spontaneous_message")
        if last_sent and _minutes_since(last_sent) < effective_min_hours * 60:
            return

        channel_id = database.get_setting(
            "personality_spontaneous_channel_id", config.DEFAULT_SETTINGS["personality_spontaneous_channel_id"]
        )
        if not channel_id:
            return
        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            return

        humor_level = database.get_setting(
            "personality_humor_level", config.DEFAULT_SETTINGS["personality_humor_level"]
        )
        pool = database.get_personality_text_pool("spontaneous")
        text = mood.pick_variant(pool, new_mood, humor_level)
        if not text:
            return

        try:
            await channel.send(text)
            database.set_bot_state("last_spontaneous_message", datetime.now(timezone.utc).isoformat())
        except discord.HTTPException:
            pass


    # --- Zufallsereignisse (Spec 1, Phase 10) ---

    def _get_event_channel(self):
        """event_channel_id - OHNE Rueckfall auf announcement_channel_id/
        welcome_channel_id mehr (User-Vorgabe: ist kein eigener Kanal
        zugewiesen, postet der Bot NIRGENDWO statt ueberraschend in einem
        anderen Kanal aufzutauchen - das war genau der Grund, warum
        Zufallsereignisse zuvor unerwartet in der "Laberecke" landeten)."""
        channel_id = database.get_setting("event_channel_id", config.DEFAULT_SETTINGS["event_channel_id"])
        if not channel_id:
            return None
        return self.bot.get_channel(int(channel_id))

    async def _maybe_check_random_event(self):
        if database.get_setting(
            "random_events_enabled", config.DEFAULT_SETTINGS["random_events_enabled"]
        ) != "1":
            return

        interval = float(database.get_setting(
            "event_check_interval_minutes", config.DEFAULT_SETTINGS["event_check_interval_minutes"]
        ))
        last_check = database.get_bot_state("last_event_check")
        if last_check and _minutes_since(last_check) < interval:
            return
        database.set_bot_state("last_event_check", datetime.now(timezone.utc).isoformat())

        base_probability = float(database.get_setting(
            "event_probability_percent", config.DEFAULT_SETTINGS["event_probability_percent"]
        ))
        current_mood = database.get_bot_state("mood", "neutral")
        effective_probability = events.apply_mood_multiplier(base_probability, current_mood)

        if not events.roll_event(effective_probability):
            return

        weights = {
            "xpdrop": float(database.get_setting("event_weight_xpdrop", config.DEFAULT_SETTINGS["event_weight_xpdrop"])),
            "quiz": float(database.get_setting("event_weight_quiz", config.DEFAULT_SETTINGS["event_weight_quiz"])),
            "community": float(database.get_setting("event_weight_community", config.DEFAULT_SETTINGS["event_weight_community"])),
            "humor": float(database.get_setting("event_weight_humor", config.DEFAULT_SETTINGS["event_weight_humor"])),
        }
        event_type = events.pick_event_type(weights)

        channel = self._get_event_channel()
        if not channel:
            return

        if event_type == "xpdrop":
            await self._trigger_xpdrop_event(channel)
        elif event_type == "quiz":
            await self._trigger_quiz_event(channel)
        elif event_type == "community":
            await self._trigger_community_event(channel)
        elif event_type == "humor":
            await self._trigger_humor_event(channel)

    async def _trigger_xpdrop_event(self, channel):
        amount = int(database.get_setting("event_xpdrop_amount", config.DEFAULT_SETTINGS["event_xpdrop_amount"]))
        try:
            message = await channel.send(
                f"🎁 Der Bot hat eine XP-Kiste gefunden! Der schnellste User erhält **{amount} XP**."
            )
            await message.add_reaction("🎁")
        except discord.HTTPException:
            return

        def check(reaction: discord.Reaction, user: discord.abc.User) -> bool:
            return reaction.message.id == message.id and str(reaction.emoji) == "🎁" and not user.bot

        try:
            reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
        except asyncio.TimeoutError:
            try:
                await message.reply("⌛ Niemand war schnell genug - die XP-Kiste ist wieder verschwunden.")
            except discord.HTTPException:
                pass
            return

        # reason="event_xpdrop" (NICHT "quiz"/"message") - der versprochene Betrag
        # soll exakt stimmen, ohne sich mit einem evtl. gleichzeitig laufenden
        # Community-Bonus oder Quiz-Blitz-Event zu vermischen (siehe modul_xp_level.award_xp()).
        modul_xp_level.award_xp(user.id, amount, reason="event_xpdrop", bypass_cooldown=True)
        try:
            await message.reply(f"🎉 {user.mention} war am schnellsten und erhält **{amount} XP**!")
        except discord.HTTPException:
            pass

    async def _trigger_quiz_event(self, channel):
        database.set_bot_state("quiz_double_xp_pending", "1")
        try:
            await channel.send("🧠 Blitzquiz! Die nächste richtige Antwort gibt doppelte XP.")
        except discord.HTTPException:
            pass

    async def _trigger_community_event(self, channel):
        minutes = float(database.get_setting(
            "event_community_bonus_minutes", config.DEFAULT_SETTINGS["event_community_bonus_minutes"]
        ))
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        database.set_bot_state("community_bonus_until", until.isoformat())
        try:
            await channel.send(f"🎉 Community-Bonus! Für die nächsten {int(minutes)} Minuten gibt es Bonus-XP.")
        except discord.HTTPException:
            pass

    async def _trigger_humor_event(self, channel):
        humor_level = database.get_setting(
            "personality_humor_level", config.DEFAULT_SETTINGS["personality_humor_level"]
        )
        pool = database.get_personality_text_pool("humor_event").get("none", [])
        text = mood.pick_humor_event_text(pool, humor_level)
        if not text:
            return
        try:
            await channel.send(text)
        except discord.HTTPException:
            pass


    # --- Server-Rekorde und -Meilensteine (Phase 11) ---

    _RECORD_STAT_MAP = {
        "messages_per_day": "messages_count",
        "quiz_answered_per_day": "quiz_answered_count",
        "games_per_day": "games_count",
        "active_users_per_day": "active_users",
    }

    async def _maybe_check_server_records(self):
        # Bewusst bei JEDEM Heartbeat-Tick (jede Minute) statt eines eigenen
        # Intervall-Settings - die Vergleichsabfrage ist eine einzelne, billige
        # SELECT pro Stat-Key, kein spuerbarer Kostenfaktor.
        for stat_key, column in self._RECORD_STAT_MAP.items():
            current_value = database.get_server_stat_today(column)
            if current_value <= 0:
                continue
            result = database.check_and_update_server_record(stat_key, current_value)
            if result:
                await announcements.announce_server_record(
                    self.bot, stat_key, result["new_value"], result["old_value"]
                )

    async def _maybe_check_milestones(self):
        thresholds = database.parse_csv_ints(
            database.get_setting("milestone_thresholds", config.DEFAULT_SETTINGS["milestone_thresholds"])
        )
        if not thresholds:
            return
        result = database.check_and_update_milestone(thresholds)
        if result:
            await announcements.announce_milestone(self.bot, result["total"])


async def setup(bot: commands.Bot):
    await bot.add_cog(Personality(bot))
