"""Zentrale Ankündigungslogik für Levelaufstieg/Achievement-Freischaltung
(Spec 1, Phase 6+8) und erweiterte Community-Reaktionen (Phase 11: Server-
Rekorde, Quiz-Serien, Server-Meilensteine).

Von genau EINER Stelle genutzt, egal welcher Cog die Ankündigung auslöst,
damit alle Wege exakt denselben, bereits getesteten Ankündigungs-Mechanismus
und Wortlaut verwenden, statt eigene Parallel-Implementierungen zu pflegen.

announce_progress() (Level-Up/Achievement) prüft NUR announcements_enabled.
Die Phase-11-Funktionen unten prüfen ZUSÄTZLICH community_reactions_enabled -
so lassen sich beide Ankündigungskategorien unabhängig voneinander
an-/abschalten (siehe _community_reactions_allowed())."""
import discord

import config
import database
import modul_xp_level


def _get_announcement_channel(bot: discord.Client):
    """announcement_channel_id - OHNE Rückfall auf welcome_channel_id (User-
    Vorgabe: ist kein eigener Kanal zugewiesen, postet der Bot NIRGENDWO, statt
    unerwartet in einem anderen Kanal aufzutauchen). bot.get_channel() ist eine
    reine Cache-Abfrage - beide Aufrufer (Quiz-Button-Callback, on_message)
    laufen bereits im Discord-Gateway-Kontext, keine run_coroutine_threadsafe-
    Brücke wie beim Backend nötig."""
    channel_id = database.get_setting(
        "announcement_channel_id", config.DEFAULT_SETTINGS["announcement_channel_id"]
    )
    if not channel_id:
        return None
    return bot.get_channel(int(channel_id))


async def announce_progress(bot: discord.Client, user: discord.abc.User, xp_result):
    """Kurze, sparsame Community-Reaktion auf Levelaufstieg/Achievement-
    Freischaltung (Spec 1: "keine Chat-Flut") - passiert von Natur aus selten,
    daher ohne zusätzlichen Cooldown. xp_result ist der Rückgabewert von
    modul_xp_level.award_xp() - None, wenn dort z.B. ein Cooldown oder das Tageslimit
    (Phase 7) gegriffen hat; dann gibt es nichts anzukündigen."""
    if not xp_result:
        return
    if database.get_setting(
        "announcements_enabled", config.DEFAULT_SETTINGS["announcements_enabled"]
    ) != "1":
        return

    channel = _get_announcement_channel(bot)
    if not channel:
        return

    if xp_result.get("leveled_up"):
        embed = discord.Embed(
            description=f"🎉 {user.mention} hat **Level {xp_result['new_level']}** erreicht!",
            color=discord.Color.green(),
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    for achievement in xp_result.get("unlocked_achievements", []):
        # Geheimhaltung (secret=1) gilt nur für die Vorschau VOR Freischaltung
        # auf /achievements - die Ankündigung danach zeigt immer Klartext.
        embed = discord.Embed(
            description=(
                f"🏆 {user.mention} hat das Achievement "
                f"**{achievement['name']}** freigeschaltet!\n{achievement['description']}"
            ),
            color=discord.Color.gold(),
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass


# --- Erweiterte Community-Reaktionen (Phase 11) ---

_RECORD_LABELS = {
    "messages_per_day": "Nachrichten an einem Tag geschrieben",
    "quiz_answered_per_day": "Quizfragen an einem Tag richtig beantwortet",
    "games_per_day": "Gewinnspiele an einem Tag beendet",
    "active_users_per_day": "aktive User an einem Tag gezählt",
}


def _community_reactions_allowed() -> bool:
    if database.get_setting(
        "announcements_enabled", config.DEFAULT_SETTINGS["announcements_enabled"]
    ) != "1":
        return False
    if database.get_setting(
        "community_reactions_enabled", config.DEFAULT_SETTINGS["community_reactions_enabled"]
    ) != "1":
        return False
    return True


async def _send_community_embed(bot: discord.Client, description: str, color: discord.Color):
    if not _community_reactions_allowed():
        return
    channel = _get_announcement_channel(bot)
    if not channel:
        return
    try:
        await channel.send(embed=discord.Embed(description=description, color=color))
    except discord.HTTPException:
        pass


async def announce_server_record(bot: discord.Client, stat_key: str, new_value: int, old_value: int):
    label = _RECORD_LABELS.get(stat_key, stat_key)
    await _send_community_embed(
        bot,
        f"🏆 Neuer Serverrekord! Heute wurden **{new_value}** {label} — bisheriger Rekord: **{old_value}**.",
        discord.Color.gold(),
    )


async def announce_streak_milestone(bot: discord.Client, user: discord.abc.User, streak_length: int):
    await _send_community_embed(
        bot,
        f"🔥 {user.mention} hat eine Quiz-Serie von **{streak_length}** richtigen Antworten in Folge erreicht!",
        discord.Color.orange(),
    )


async def announce_streak_ended(bot: discord.Client, user: discord.abc.User, streak_length: int):
    await _send_community_embed(
        bot,
        f"💔 Die Quiz-Serie von {user.mention} ist nach **{streak_length}** richtigen Antworten in Folge zu Ende gegangen.",
        discord.Color.dark_grey(),
    )


async def announce_milestone(bot: discord.Client, total_value: int):
    await _send_community_embed(
        bot,
        f"🎉 Server-Meilenstein! Insgesamt wurden bereits **{total_value}** Quizfragen richtig beantwortet!",
        discord.Color.purple(),
    )
