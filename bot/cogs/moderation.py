"""Automod: Schimpfwortfilter, Spam-/Flutschutz, Link-Filter, Willkommensnachricht.

Alle drei Filter teilen sich dieselbe Verstoß-Historie (Tabelle "badword_violations",
über die Spalte "violation_type" unterschieden) und denselben Verjährungs-Modus
("badword_violation_expiry_mode"), statt drei fast identische Tabellen/Einstellungen
zu bauen. Die Schimpfwortliste liegt in der DB (Tabelle "badwords") statt in einer
statischen Textdatei, damit sie über das Backend verwaltbar ist, ohne den Bot neu
starten zu müssen. Beim ersten Start wird die alte badwords.txt einmalig (idempotent)
importiert.
"""
import asyncio
import re
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands

import announcements
from cogs import verdiente_rollen
import config
import database
import linkfilter
import modul_xp_level
import modul_quiz
import mood
import wortfilter
from wortfilter import find_matched_word, normalize

# Bewusst einfacher, robuster Link-Erkenner statt vollständiger URL-Validierung -
# reicht für den Zweck (Spam-/Phishing-Links erkennen), muss keine RFC-genaue
# URL-Grammatik abbilden.
_LINK_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)


def _load_lines(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip().lower() for line in f if line.strip()]
    except FileNotFoundError:
        return []


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        imported = database.import_badwords_from_list(_load_lines(config.BADWORDS_FILE))
        if imported:
            print(f"[moderation] {imported} Wörter einmalig aus badwords.txt in die DB importiert.")
        # Spam-/Flut-Erkennung: pro User die Zeitpunkte (+ Nachrichten) der letzten
        # Nachrichten. Bewusst nur im Arbeitsspeicher (kein DB-Feld) - muss keinen
        # Bot-Neustart überstehen, genau wie der Quiz-Cooldown.
        self._message_times: dict = defaultdict(deque)
        # Ein Lock pro discord_user_id um den kritischen Abschnitt in _maybe_timeout()
        # (Verstoß zählen -> Schwelle pruefen -> timeouten). Ohne das kann discord.py
        # zwei Nachrichten desselben Users als nebenlaeufige Tasks verarbeiten, die
        # beide die Schwelle ueberschritten sehen und dadurch beide gleichzeitig
        # timeouten + eine Warnung schicken. defaultdict(asyncio.Lock) legt das Lock
        # bei erstem Zugriff pro User an; der dict-Zugriff selbst hat keinen await-
        # Punkt und ist daher innerhalb der Event-Loop race-frei.
        self._timeout_locks: dict = defaultdict(asyncio.Lock)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Reihenfolge: Flut zuerst (zeitkritischster Fall), dann Links, dann
        # Schimpfwörter. Sobald ein Filter die Nachricht behandelt (und meist
        # gelöscht) hat, wird abgebrochen - weitere Prüfungen auf einer bereits
        # gelöschten Nachricht ergeben keinen Sinn mehr. Aus demselben Grund gibt
        # es fuer eine derart behandelte Nachricht auch KEINE Nachrichten-XP
        # (Phase 7) - die wird erst vergeben, wenn keiner der drei Filter greift.
        if await self._check_spam(message):
            return
        if await self._check_links(message):
            return
        if await self._check_badwords(message):
            return
        # Server-Statistik-Tracking (Spec 1, Phase 11) - unabhaengig davon, ob
        # Nachrichten-XP ueberhaupt aktiv ist (message_xp_enabled), zaehlt jede
        # unbehandelte, normale Nachricht fuer Server-Rekorde/Mood-Aktivitaet mit.
        database.increment_server_stat("messages_count")
        database.track_daily_active_user(message.author.id)
        # Pro-User Nachrichtenzaehler + letzte Aktivitaet (Spec 1, Phase B4,
        # Ueberwachungs-Uebersicht) - getrennt von den beiden Aufrufen oben,
        # die nur serverweite Aggregate/Tages-Zaehler pflegen.
        modul_quiz.track_user_message(message.author.id)
        await self._award_message_xp(message)

    # --- Nachrichten-XP (Spec 1, Phase 7) ---

    async def _award_message_xp(self, message: discord.Message):
        if database.get_setting("message_xp_enabled", config.DEFAULT_SETTINGS["message_xp_enabled"]) != "1":
            return
        amount = int(database.get_setting("xp_per_message", config.DEFAULT_SETTINGS["xp_per_message"]))
        # KEIN bypass_cooldown - anders als beim Quiz nutzt das hier zum ersten
        # Mal tatsaechlich den generischen xp_cooldown_seconds-Mechanismus aus
        # Phase 4, der genau fuer diesen Zweck vorgesehen war. Das zusaetzliche
        # daily_xp_limit wird zentral innerhalb von award_xp() durchgesetzt.
        xp_result = modul_xp_level.award_xp(message.author.id, amount, reason="message")
        # Dieselbe zentrale Ankuendigungsfunktion wie im Quiz-Cog (Phase 8) -
        # kein eigener Parallel-Mechanismus fuer Level-Aufstiege/Achievements,
        # die durch Nachrichten-XP statt Quiz-Erfolg ausgeloest werden.
        await announcements.announce_progress(self.bot, message.author, xp_result)
        # Neue XP koennen eine Schwelle fuer eine verdiente Rolle gerissen haben.
        await verdiente_rollen.verdiente_rollen_pruefen(self.bot, message.author)

    # --- Schimpfwortfilter ---

    async def _check_badwords(self, message: discord.Message) -> bool:
        # Bewusst kein In-Memory-Cache: bei diesem Nachrichtenaufkommen ist ein
        # SQLite-SELECT pro Nachricht kein spürbarer Kostenfaktor, dafür sind
        # Änderungen an Wortliste/Einstellungen übers Backend sofort wirksam.
        if database.get_setting("badword_filter_enabled", config.DEFAULT_SETTINGS["badword_filter_enabled"]) != "1":
            return False

        aggressive = database.get_setting(
            "badword_aggressive_normalization_enabled",
            config.DEFAULT_SETTINGS["badword_aggressive_normalization_enabled"],
        ) == "1"
        treffer = wortfilter.pruefe(
            message.content,
            database.get_badwords_mit_stufe(),
            aggressive=aggressive,
            ausnahmen=database.get_badword_ausnahmen(),
        )
        if not treffer.getroffen:
            return False

        matched = treffer.wort
        database.log_badword_violation(message.author.id, message.content, matched,
                                       violation_type="badword", stufe=treffer.stufe)

        # Die Reaktion haengt an der Stufe des Wortes: Ein derber Fluch loest
        # nicht denselben Timeout aus wie eine Beleidigung. Vorgabe Patrick,
        # 05.09.2026 - vorher war jedes Wort gleich schlimm.
        schluessel = f"badword_action_{treffer.stufe}"
        action = database.get_setting(
            schluessel,
            config.DEFAULT_SETTINGS.get(schluessel, config.DEFAULT_SETTINGS["badword_filter_action"]),
        )

        try:
            await message.delete()
        except discord.Forbidden:
            if action != "delete_only":
                await message.channel.send(
                    "Ich kann diese Nachricht nicht löschen, da mir die Berechtigungen fehlen."
                )

        if action != "delete_only":
            await message.channel.send(
                f":warning: {message.author.mention}, bitte bleib respektvoll! "
                "Verwende keine Schimpfwörter."
            )

        # Der Timeout haengt am Punktekonto, nicht an der Reaktion dieser einen
        # Stufe. Auch ein leichter Verstoss, der nur geloescht wird, bringt
        # Punkte und kann das Fass zum Ueberlaufen bringen - deshalb steht die
        # Pruefung HINTER der Warnung, aber VOR jedem vorzeitigen Ausstieg.
        # "Nur loeschen" heisst, dass keine Warnung im Kanal steht, nicht dass
        # der Verstoss folgenlos bleibt.
        await self._maybe_timeout(
            message,
            violation_type="badword",
            threshold_key="badword_punkte_schwelle",
            duration_key="badword_timeout_duration_minutes",
            reason_label="Schimpfwort-Punkte",
            stand=database.verstoss_punkte(message.author.id),
            einheit="Punkte",
        )

        return True

    # --- Spam-/Flutschutz ---

    async def _check_spam(self, message: discord.Message) -> bool:
        if database.get_setting("spam_filter_enabled", config.DEFAULT_SETTINGS["spam_filter_enabled"]) != "1":
            return False

        threshold = int(database.get_setting(
            "spam_message_threshold", config.DEFAULT_SETTINGS["spam_message_threshold"]
        ))
        window = int(database.get_setting(
            "spam_time_window_seconds", config.DEFAULT_SETTINGS["spam_time_window_seconds"]
        ))

        now = time.monotonic()
        times = self._message_times[message.author.id]
        times.append((now, message))
        while times and now - times[0][0] > window:
            times.popleft()

        if len(times) <= threshold:
            return False

        # Flut erkannt: alle gemerkten Nachrichten dieses Ausbruchs löschen (soweit
        # noch vorhanden) und den Zähler zurücksetzen, damit nicht sofort erneut
        # ausgelöst wird, sobald die Warnung/Timeout-Nachricht selbst gezählt würde.
        burst = list(times)
        times.clear()

        for _, msg in burst:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass

        database.log_badword_violation(
            message.author.id, f"{len(burst)} Nachrichten in {window}s",
            f"{len(burst)} Nachrichten", violation_type="spam",
        )

        action = database.get_setting("spam_filter_action", config.DEFAULT_SETTINGS["spam_filter_action"])
        if action == "delete_only":
            return True

        try:
            await message.channel.send(
                f":rotating_light: {message.author.mention}, bitte poste nicht so viele Nachrichten in so kurzer Zeit!"
            )
        except discord.HTTPException:
            pass

        if action == "delete_warn_timeout":
            duration_minutes = int(database.get_setting(
                "spam_timeout_duration_minutes", config.DEFAULT_SETTINGS["spam_timeout_duration_minutes"]
            ))
            try:
                await message.author.timeout(
                    timedelta(minutes=duration_minutes),
                    reason="Automatischer Timeout: Nachrichtenflut erkannt",
                )
                await message.channel.send(
                    f"🔇 {message.author.mention} wurde wegen Nachrichtenflut für {duration_minutes} Minuten stummgeschaltet."
                )
            except discord.Forbidden:
                await message.channel.send(
                    "⚠️ Wegen Nachrichtenflut sollte ein Timeout erfolgen, mir fehlt dafür aber die Berechtigung."
                )

        return True

    # --- Link-Filter ---

    async def _check_links(self, message: discord.Message) -> bool:
        if database.get_setting("link_filter_enabled", config.DEFAULT_SETTINGS["link_filter_enabled"]) != "1":
            return False

        match = _LINK_RE.search(message.content)
        if not match:
            return False

        # Rollen-Whitelist: Mitglieder mit einer dieser Rollen sind komplett vom
        # Link-Filter ausgenommen, unabhaengig von allen Regeln unten (Link-Filter-
        # Ueberarbeitung). getattr(..., "roles", []), da message.author in einem
        # DM-Kontext ein reines User- statt Member-Objekt sein kann (kein .roles).
        exempt_role_ids = database.get_link_filter_whitelisted_role_ids()
        if exempt_role_ids:
            author_role_ids = {str(r.id) for r in getattr(message.author, "roles", [])}
            if author_role_ids & set(exempt_role_ids):
                return False

        # Kanal-Ausnahmeliste: dieser Kanal ist komplett vom Link-Filter
        # ausgenommen (z.B. ein "Ressourcen"-Kanal), unabhaengig von allen
        # Regeln unten.
        if str(message.channel.id) in database.get_link_filter_exempt_channel_ids():
            return False

        # Die Regeln greifen ineinander; das Urteil steht deshalb in
        # linkfilter.py und ist ohne laufenden Bot pruefbar. Reihenfolge dort:
        # Sperrliste, dann "alles blockieren", dann Erlaubtliste, dann neue
        # Mitglieder. Die Sperrliste kommt zuerst - wer eine Domain
        # ausdruecklich verbietet, meint es.
        holen = lambda schluessel: database.get_setting(
            schluessel, config.DEFAULT_SETTINGS[schluessel]) == "1"

        ist_neu = False
        if holen("link_filter_new_members_enabled"):
            tage = int(database.get_setting(
                "link_filter_new_member_days",
                config.DEFAULT_SETTINGS["link_filter_new_member_days"]))
            beigetreten = getattr(message.author, "joined_at", None)
            if beigetreten is not None:
                ist_neu = (discord.utils.utcnow() - beigetreten).days < tage

        urteil = linkfilter.pruefe(
            url=match.group(0),
            gesperrt=database.get_link_blacklist(),
            erlaubt=database.get_link_whitelist(),
            alles_blockieren=holen("link_filter_block_all_enabled"),
            erlaubtliste_aktiv=holen("link_filter_whitelist_enabled"),
            neue_mitglieder_aktiv=holen("link_filter_new_members_enabled"),
            ist_neues_mitglied=ist_neu,
        )
        violation = urteil.verstoss
        if not violation:
            return False

        database.log_badword_violation(
            message.author.id, message.content, match.group(0)[:60], violation_type="link"
        )

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        action = database.get_setting("link_filter_action", config.DEFAULT_SETTINGS["link_filter_action"])
        if action == "delete_only":
            return True

        try:
            await message.channel.send(
                f":link: {message.author.mention}, das Posten von Links ist hier nicht erlaubt."
            )
        except discord.HTTPException:
            pass

        if action == "delete_warn_timeout":
            await self._maybe_timeout(
                message,
                violation_type="link",
                threshold_key="link_filter_timeout_threshold",
                duration_key="link_filter_timeout_duration_minutes",
                reason_label="Link-Verstöße",
            )

        return True

    # --- Gemeinsame Timeout-Eskalation (Schimpfwort + Link; Spam timeoutet direkt) ---

    async def _maybe_timeout(self, message: discord.Message, violation_type: str,
                             threshold_key: str, duration_key: str, reason_label: str,
                             stand=None, einheit: str = "Verstöße"):
        """Timeoutet den Autor, wenn er die konfigurierte Schwelle erreicht hat.

        `stand` ist der aktuelle Wert; ohne Angabe wird die Anzahl der Verstöße
        dieses Typs gezählt. Der Schimpfwortfilter reicht stattdessen sein
        Punktekonto herein — drei „scheisse" wiegen nicht dasselbe wie drei
        rassistische Beschimpfungen.

        Läuft komplett unter einem Lock pro discord_user_id, damit zwei nahezu
        gleichzeitige Verstöße desselben Users (zwei parallele on_message-Tasks)
        nicht beide unabhängig voneinander die Schwelle als überschritten sehen
        und dadurch doppelt timeouten + doppelt warnen.
        """
        try:
            threshold = int(database.get_setting(threshold_key, config.DEFAULT_SETTINGS[threshold_key]))
            duration_minutes = int(database.get_setting(duration_key, config.DEFAULT_SETTINGS[duration_key]))
        except (TypeError, ValueError):
            return

        async with self._timeout_locks[message.author.id]:
            # Bereits aktiv getimeoutet (z.B. durch eine parallel verarbeitete
            # zweite Nachricht, die denselben Schwellen-Uebertritt sah und den
            # Timeout unter diesem Lock bereits ausgefuehrt hat) - kein erneuter
            # Timeout-Aufruf, keine zweite Warnnachricht fuer denselben Uebertritt.
            if message.author.timed_out_until and message.author.timed_out_until > discord.utils.utcnow():
                return

            count = (database.count_user_violations(message.author.id, violation_type=violation_type)
                     if stand is None else stand)
            if count < threshold:
                return

            try:
                await message.author.timeout(
                    timedelta(minutes=duration_minutes),
                    reason=f"Automatischer Timeout: {count} {reason_label} erreicht",
                )
                await message.channel.send(
                    f"🔇 {message.author.mention} wurde wegen wiederholter Verstöße "
                    f"({count} {einheit}) für {duration_minutes} Minuten stummgeschaltet."
                )
            except discord.Forbidden:
                await message.channel.send(
                    "⚠️ Der Nutzer sollte laut Einstellung getimeoutet werden, mir fehlt dafür aber die Berechtigung."
                )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if database.get_setting("welcome_message_enabled", config.DEFAULT_SETTINGS["welcome_message_enabled"]) != "1":
            return
        channel_id = database.get_setting("welcome_channel_id", config.DEFAULT_SETTINGS["welcome_channel_id"])
        # Kein Fallback auf einen anderen Kanal mehr (User-Vorgabe: ohne
        # zugewiesenen Kanal soll NIRGENDWO gepostet werden) - und ohne
        # Truthy-Check wuerde int("") hier crashen, seit der Kanal ueber das
        # Dropdown auch explizit auf "kein Kanal" gesetzt werden kann.
        if not channel_id:
            return
        channel = member.guild.get_channel(int(channel_id))
        if not channel:
            return

        greeting = f"Willkommen {member.mention} auf unserem Server!"
        # personality_enabled=0 -> IMMER die bisherige neutrale Begruessungszeile
        # (keine Regression). Der Regel-Hinweis danach bleibt in JEDEM Fall
        # unveraendert, da er operativ wichtig ist (kein Personality-Bestandteil).
        if database.get_setting("personality_enabled", config.DEFAULT_SETTINGS["personality_enabled"]) == "1":
            frequency = database.get_setting(
                "personality_response_frequency", config.DEFAULT_SETTINGS["personality_response_frequency"]
            )
            if mood.roll_personality_response(frequency):
                humor_level = database.get_setting(
                    "personality_humor_level", config.DEFAULT_SETTINGS["personality_humor_level"]
                )
                current_mood = database.get_bot_state("mood", "neutral")
                pool = database.get_personality_text_pool("welcome")
                colored = mood.pick_variant(pool, current_mood, humor_level, mention=member.mention)
                if colored:
                    greeting = colored

        await channel.send(
            f"{greeting}\n\n"
            "Bitte lies dir die Regeln im Kanal #regeln durch und markiere den Beitrag mit einem :thumbsup:, "
            "um Zugang zum Server zu erhalten."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
