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


def _kanal_fuer(bot: discord.Client, schluessel: str):
    """Der Zielkanal fuer EINE Art von Ankuendigung.

    Patrick am 07.09.2026: "man soll immer überall einstellen können wohin der
    bot postet wenn er etwas Postet." Level-Aufstiege, Achievements, Rekorde,
    Meilensteine und Quiz-Serien haben deshalb je einen eigenen Kanal.

    Ist keiner eingetragen, greift der allgemeine `announcement_channel_id` -
    sonst muesste man fuenf Felder ausfuellen, nur um den Zustand von vorher zu
    bekommen. Ist auch der leer, postet der Bot NIRGENDWO, statt unerwartet in
    einem anderen Kanal aufzutauchen.
    """
    kanal_id = database.get_setting(schluessel, config.DEFAULT_SETTINGS[schluessel])
    if not kanal_id:
        kanal_id = database.get_setting(
            "announcement_channel_id", config.DEFAULT_SETTINGS["announcement_channel_id"])
    if not kanal_id:
        return None
    return bot.get_channel(int(kanal_id))


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

    # Zwei getrennte Kanaele: Ein Levelaufstieg ist etwas anderes als ein
    # freigeschaltetes Achievement, und Patrick will beides einzeln steuern
    # koennen. Faellt einer der beiden weg, laeuft der andere trotzdem - ein
    # frueher Ausstieg hier haette die Achievements mit verschluckt.
    levelkanal = _kanal_fuer(bot, "levelup_channel_id")
    if xp_result.get("leveled_up") and levelkanal:
        embed = discord.Embed(
            description=f"🎉 {user.mention} hat **Level {xp_result['new_level']}** erreicht!",
            color=discord.Color.green(),
        )
        try:
            await levelkanal.send(embed=embed)
        except discord.HTTPException:
            pass

    erfolge = xp_result.get("unlocked_achievements", [])
    erfolgskanal = _kanal_fuer(bot, "achievement_channel_id") if erfolge else None
    for achievement in erfolge:
        if not erfolgskanal:
            break
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
            await erfolgskanal.send(embed=embed)
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


async def _send_community_embed(bot: discord.Client, description: str, color: discord.Color,
                                kanal_schluessel: str = "announcement_channel_id"):
    if not _community_reactions_allowed():
        return
    channel = _kanal_fuer(bot, kanal_schluessel)
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
        f"🏆 Neuer Serverrekord! Gestern wurden **{new_value}** {label} — bisheriger Rekord: **{old_value}**.",
        discord.Color.gold(),
        "record_channel_id",
    )


async def announce_streak_milestone(bot: discord.Client, user: discord.abc.User, streak_length: int):
    await _send_community_embed(
        bot,
        f"🔥 {user.mention} hat eine Quiz-Serie von **{streak_length}** richtigen Antworten in Folge erreicht!",
        discord.Color.orange(),
        "streak_channel_id",
    )


async def announce_streak_ended(bot: discord.Client, user: discord.abc.User, streak_length: int):
    await _send_community_embed(
        bot,
        f"💔 Die Quiz-Serie von {user.mention} ist nach **{streak_length}** richtigen Antworten in Folge zu Ende gegangen.",
        discord.Color.dark_grey(),
        "streak_channel_id",
    )


async def announce_milestone(bot: discord.Client, total_value: int):
    await _send_community_embed(
        bot,
        f"🎉 Server-Meilenstein! Insgesamt wurden bereits **{total_value}** Quizfragen richtig beantwortet!",
        discord.Color.purple(),
        "milestone_channel_id",
    )
