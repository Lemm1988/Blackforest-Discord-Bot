"""Pflanzenquiz: Kategorie wählen, Fragen per Button beantworten, Punkte, Leaderboard.

Punkte pro richtiger Antwort, Antwort-Zeitlimit und Cooldown zwischen zwei
`/frage`-Aufrufen sind über die Backend-Seite "/games/quiz" einstellbar (settings-
Tabelle) und werden bei JEDEM Aufruf frisch aus der DB gelesen (nicht einmalig
beim Bot-Start gecacht) - Änderungen im Backend wirken sich sofort aus, ohne
Bot-Neustart. Fragen-Kategorien sind über die Backend-Seite "/categories"
verwaltbar.
"""
import time
from typing import Literal

import discord
from discord.ext import commands

import announcements
from cogs import verdiente_rollen
import config
import database
import modul_xp_level
import modul_quiz
import mood


def _truncate(text: str, limit: int = 1024) -> str:
    """Schützt vor Discords Embed-Feldlimit (1024 Zeichen) bei sehr langen Antworttexten."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class AnswerView(discord.ui.View):
    """Vier Buttons (A-D). Nur der Zielnutzer darf klicken.

    Nach der Antwort (oder bei Zeitablauf) wird der korrekte Button grün und ein
    falsch angeklickter Button rot eingefärbt, alle werden deaktiviert - so ist auf
    einen Blick klar, was richtig gewesen wäre, statt nur einen Text lesen zu müssen
    (besonders auf dem kleineren Mobile-Bildschirm hilfreich).
    """

    def __init__(self, question_row, author: discord.abc.User, timeout_seconds: float, points: int):
        super().__init__(timeout=timeout_seconds)
        self.question_id = question_row["id"]
        self.correct_option = question_row["correct_option"]
        self.author = author
        self.points = points
        self.answered = False
        self.message: discord.Message | None = None
        self._buttons = {}

        for opt in ("A", "B", "C", "D"):
            button = self._make_button(opt)
            self._buttons[opt] = button
            self.add_item(button)

    def _make_button(self, option: str) -> discord.ui.Button:
        button = discord.ui.Button(label=option, style=discord.ButtonStyle.secondary, custom_id=option)

        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id:
                await interaction.response.send_message(
                    "Diese Frage ist nicht an dich gerichtet!", ephemeral=True
                )
                return
            if self.answered:
                await interaction.response.send_message(
                    "Diese Frage wurde bereits beantwortet.", ephemeral=True
                )
                return

            self.answered = True
            modul_quiz.mark_question_answered(self.author.id, self.question_id)
            correct = option == self.correct_option
            if correct:
                modul_quiz.add_points(self.author.id, self.points)
                # XP separat vom Quiz-Highscore (players.score) vergeben - eigener
                # Cooldown (xp_cooldown_seconds) gilt hier bewusst NICHT, das regelt
                # bereits der Quiz-eigene Cooldown zwischen zwei >frage-Aufrufen.
                xp_amount = int(database.get_setting(
                    "xp_per_quiz", config.DEFAULT_SETTINGS["xp_per_quiz"]
                ))
                xp_result = modul_xp_level.award_xp(self.author.id, xp_amount, reason="quiz", bypass_cooldown=True)
                await announcements.announce_progress(interaction.client, self.author, xp_result)
                # Zaehler fuer richtige Antworten ist gestiegen - kann eine
                # verdiente Rolle ausloesen (z.B. 100 richtige Fragen).
                await verdiente_rollen.verdiente_rollen_pruefen(
                    interaction.client, interaction.guild.get_member(self.author.id)
                    if interaction.guild else None)
                # Server-Statistik-Tracking (Phase 11) - nur bei richtiger Antwort,
                # wie in der Spec vorgegeben (aktive_users wird bewusst NUR ueber
                # Nachrichten gezaehlt, siehe cogs/moderation.py).
                database.increment_server_stat("quiz_answered_count")

            # Quiz-Serien (Streaks, Phase 11) - fuer beide Ausgaenge (richtig
            # erhoeht, falsch setzt zurueck), daher ausserhalb des if-Zweigs.
            streak_result = modul_quiz.update_quiz_streak(self.author.id, correct)
            await self._announce_streak(interaction.client, correct, streak_result)

            self._reveal(clicked=option)
            await interaction.response.edit_message(embed=self._result_embed(correct), view=self)
            self.stop()

        button.callback = callback
        return button

    def _reveal(self, clicked=None):
        for opt, button in self._buttons.items():
            button.disabled = True
            if opt == self.correct_option:
                button.style = discord.ButtonStyle.success
            elif opt == clicked:
                button.style = discord.ButtonStyle.danger

    def _result_embed(self, correct: bool) -> discord.Embed:
        if correct:
            description = f"Du hast **{self.points} Punkte** gewonnen! 🎉"
            # personality_enabled=0 -> IMMER die bisherige neutrale Beschreibung
            # (keine Regression). Bei personality_enabled=1 nur mit der
            # konfigurierten Wahrscheinlichkeit mood-gefaerbt statt neutral.
            if database.get_setting("personality_enabled", config.DEFAULT_SETTINGS["personality_enabled"]) == "1":
                frequency = database.get_setting(
                    "personality_response_frequency", config.DEFAULT_SETTINGS["personality_response_frequency"]
                )
                if mood.roll_personality_response(frequency):
                    humor_level = database.get_setting(
                        "personality_humor_level", config.DEFAULT_SETTINGS["personality_humor_level"]
                    )
                    current_mood = database.get_bot_state("mood", "neutral")
                    pool = database.get_personality_text_pool("quiz_correct")
                    colored = mood.pick_variant(
                        pool, current_mood, humor_level, points=self.points
                    )
                    if colored:
                        description = colored
            return discord.Embed(title="✅ Richtig!", description=description, color=discord.Color.green())
        return discord.Embed(
            title="❌ Leider falsch",
            description=f"Die richtige Antwort war **{self.correct_option}**.",
            color=discord.Color.red(),
        )

    async def _announce_streak(self, bot: discord.Client, correct: bool, streak_result: dict):
        """Ankuendigung bei Erreichen eines konfigurierten Meilenstein-Werts
        (current_streak) ODER wenn eine mindestens so lange Serie gerade zu
        Ende gegangen ist (Phase 11) - beides ueber die zentrale
        announcements.py, gated durch announcements_enabled UND
        community_reactions_enabled (siehe dort)."""
        thresholds = database.parse_csv_ints(
            database.get_setting(
                "streak_announcement_thresholds", config.DEFAULT_SETTINGS["streak_announcement_thresholds"]
            )
        )
        if not thresholds:
            return

        if correct:
            if streak_result["current_streak"] in thresholds:
                await announcements.announce_streak_milestone(bot, self.author, streak_result["current_streak"])
        else:
            if streak_result["streak_ended"] and streak_result["ended_streak_length"] >= min(thresholds):
                await announcements.announce_streak_ended(bot, self.author, streak_result["ended_streak_length"])

    async def on_timeout(self):
        if self.answered:
            return
        self._reveal()
        if self.message:
            try:
                embed = discord.Embed(
                    title="⚠️ Zeit abgelaufen",
                    description=f"Die richtige Antwort wäre **{self.correct_option}** gewesen.",
                    color=discord.Color.orange(),
                )
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

class CategorySelectView(discord.ui.View):
    """Auswahlmenü mit allen Kategorien plus "Zufällige Kategorie", vor der eigentlichen Frage."""

    def __init__(self, categories, author: discord.abc.User, on_choice):
        super().__init__(timeout=60.0)
        self.author = author

        options = [discord.SelectOption(label="Zufällige Kategorie", value="random", emoji="🎲")]
        for cat in categories:
            options.append(
                discord.SelectOption(label=cat["name"], value=str(cat["id"]), emoji=cat["emoji"] or None)
            )

        select = discord.ui.Select(placeholder="Wähle eine Kategorie …", options=options)

        async def select_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id:
                await interaction.response.send_message(
                    "Diese Auswahl ist nicht an dich gerichtet!", ephemeral=True
                )
                return
            self.stop()
            value = select.values[0]
            category_id = None if value == "random" else int(value)
            await on_choice(interaction, category_id)

        select.callback = select_callback
        self.add_item(select)

    async def on_timeout(self):
        # Kein Folgeschritt möglich (kein Interaction mehr verfügbar) - die Nachricht
        # bleibt einfach mit deaktiviertem Menü stehen.
        for child in self.children:
            child.disabled = True


async def _darf_fragen_einreichen(ctx: commands.Context) -> bool:
    """Fragen einreichen ist seit dem 03.09.2026 an ein Recht gebunden.

    Es kann aus dem Backend kommen (Flag "submit_questions") ODER aus einer
    Discord-Rolle, der dieses Recht zugeordnet ist - so ist "Ideenschmied"
    gedacht: Wer sich durch 100 Fragen gearbeitet hat, darf eigene beisteuern.

    Vorher durfte das jedes Servermitglied.
    """
    rollen = {str(r.id) for r in getattr(ctx.author, "roles", [])}
    if database.has_permission(ctx.author.id, "submit_questions", rollen=rollen):
        return True
    raise commands.CheckFailure(
        "Fragen einreichen dürfen Mitglieder mit der passenden Rolle. "
        "Beantworte fleißig Quizfragen — ab einer gewissen Zahl schalte ich dich frei!"
    )


class Quiz(commands.Cog):
    # Fuer die Gruppierung in /help. Das Label steht am Cog und nicht in
    # einer zentralen Liste, damit ein entferntes Modul auch aus der Hilfe
    # verschwindet, ohne dass man daran denken muss.
    MODUL_LABEL = "🌿 Pflanzenquiz"
    MODUL_REIHENFOLGE = 1
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Bewusst nur im Arbeitsspeicher (kein DB-Feld): der Cooldown muss keinen
        # Bot-Neustart überstehen, ein Reset auf 0 bei jedem Neustart ist unkritisch.
        self._last_attempt: dict[int, float] = {}

    def _cooldown_remaining(self, user_id: int, cooldown_seconds: int) -> int:
        last = self._last_attempt.get(user_id)
        if last is None or cooldown_seconds <= 0:
            return 0
        remaining = cooldown_seconds - (time.monotonic() - last)
        return max(0, round(remaining))

    async def _send_question(self, target, author: discord.abc.User, category_id):
        """Zieht eine Frage (ggf. auf eine Kategorie eingeschränkt) und schickt sie
        als Embed mit Antwort-Buttons. `target` ist entweder ein discord.Interaction
        (nach Kategorie-Auswahl) oder ein commands.Context (Fallback ohne Kategorien)."""
        question = modul_quiz.get_unanswered_question(author.id, category_id=category_id)

        if not question:
            score = modul_quiz.get_score(author.id)
            if category_id is not None:
                category = modul_quiz.get_category(category_id)
                cat_name = category["name"] if category else "dieser Kategorie"
                text = (
                    f"🎉 {author.mention}, du hast bereits **alle Fragen in der Kategorie "
                    f"\"{cat_name}\"** beantwortet! Wähle gerne eine andere Kategorie mit `/frage`.\n"
                    f"Dein aktueller Punktestand: **{score}** Punkte."
                )
            else:
                text = (
                    f"🎉 {author.mention}, du hast bereits **alle verfügbaren Fragen** beantwortet!\n"
                    f"Dein aktueller Punktestand: **{score}** Punkte."
                )
            if isinstance(target, discord.Interaction):
                await target.response.edit_message(content=text, embed=None, view=None)
            else:
                await target.send(text)
            return

        self._last_attempt[author.id] = time.monotonic()

        points = int(database.get_setting(
            "quiz_points_correct", config.DEFAULT_SETTINGS["quiz_points_correct"]
        ))
        timeout_seconds = int(database.get_setting(
            "quiz_answer_timeout_seconds", config.DEFAULT_SETTINGS["quiz_answer_timeout_seconds"]
        ))

        total = modul_quiz.count_questions(True, category_id=category_id)
        # Zählt vor dem Beantworten, schließt also die aktuelle Frage noch ein.
        remaining_incl_current = modul_quiz.count_unanswered_for_user(author.id, category_id=category_id)
        question_number = total - remaining_incl_current + 1

        category = modul_quiz.get_category(category_id) if category_id is not None else None
        category_label = f"{category['emoji'] or ''} {category['name']}".strip() if category else "Zufällige Kategorie"

        embed = discord.Embed(
            title="🌿 Pflanzenquiz",
            description=f"**{_truncate(question['question'], 4096)}**",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="🇦", value=_truncate(question["option_a"]), inline=False)
        embed.add_field(name="🇧", value=_truncate(question["option_b"]), inline=False)
        embed.add_field(name="🇨", value=_truncate(question["option_c"]), inline=False)
        embed.add_field(name="🇩", value=_truncate(question["option_d"]), inline=False)
        embed.set_footer(text=f"{category_label} • Frage {question_number} von {total} • {timeout_seconds}s Zeit")

        view = AnswerView(question, author, timeout_seconds=timeout_seconds, points=points)

        if isinstance(target, discord.Interaction):
            await target.response.edit_message(content=None, embed=embed, view=view)
            view.message = await target.original_response()
        else:
            message = await target.send(embed=embed, view=view)
            view.message = message

    @commands.hybrid_command(name="frage", description="Zieht dir eine Quizfrage — richtig beantwortet gibt es Punkte und XP",
        extras={"antwort": "vertraulich", "hilfe": "Ich stelle dir eine zufällige Pflanzenfrage mit vier Antworten zur Auswahl. Tippe auf die Antwort, die du für richtig hältst.\nLiegst du richtig, bekommst du Quiz-Punkte **und** Server-XP. Zwischen zwei Fragen liegt eine kurze Wartezeit, damit niemand einfach durchklickt."},)
    async def frage(self, ctx: commands.Context):
        # An/Aus-Schalter des Spiels (Spec 1, Phase B1, /games/quiz) - blockiert
        # bewusst nur das SPIELEN (neue Frage abrufen), nicht das ANSEHEN
        # bestehender Staende (gamescore/xp/leaderboard/achievements bleiben nutzbar).
        if database.get_setting("game_quiz_enabled", config.DEFAULT_SETTINGS["game_quiz_enabled"]) != "1":
            await ctx.send("🌿 Das Pflanzenquiz ist aktuell deaktiviert.", ephemeral=True)
            return
        cooldown_seconds = int(database.get_setting(
            "quiz_cooldown_seconds", config.DEFAULT_SETTINGS["quiz_cooldown_seconds"]
        ))
        remaining = self._cooldown_remaining(ctx.author.id, cooldown_seconds)
        if remaining > 0:
            await ctx.send(
                f"⏳ {ctx.author.mention}, bitte warte noch **{remaining}s**, bevor du die nächste Frage abrufst.",
                ephemeral=True,
            )
            return

        categories = modul_quiz.get_categories()
        if not categories:
            # Keine Kategorien angelegt: wie gehabt direkt eine zufällige Frage ohne Auswahlmenü.
            await self._send_question(ctx, ctx.author, category_id=None)
            return

        async def on_choice(interaction: discord.Interaction, category_id):
            await self._send_question(interaction, ctx.author, category_id=category_id)

        view = CategorySelectView(categories, ctx.author, on_choice)
        await ctx.send("🌿 Wähle eine Kategorie für deine nächste Frage:", view=view, ephemeral=True)

    @commands.hybrid_command(name="gamescore", description="Zeigt deine gesammelten Quiz-Punkte",
        extras={"hilfe": "Zeigt, wie viele Punkte du im Pflanzenquiz gesammelt hast. Das ist etwas anderes als deine XP: Quiz-Punkte bekommst du **nur** durchs Quiz, XP auch fürs Schreiben im Chat. Deine XP siehst du mit `/xp`."},)
    async def gamescore(self, ctx: commands.Context):
        score = modul_quiz.get_score(ctx.author.id)
        await ctx.send(f"{ctx.author.mention}, dein aktueller Punktestand beträgt **{score}** Punkte! 🌟")

    # Am 03.09.2026 von "leaderboard" umbenannt. Der alte Name liess offen, um
    # welche Wertung es geht - gezeigt wurden immer schon die QUIZ-Punkte aus der
    # Tabelle players, nie die serverweiten XP. Seit es mit
    # /serverxp_rangliste eine zweite Rangliste gibt, waere das nicht mehr nur
    # ungenau, sondern irrefuehrend.
    @commands.hybrid_command(
        name="quiz_rangliste",
        description="Die 15 Besten im Pflanzenquiz",
        extras={"hilfe": "Zeigt die 15 Personen mit den meisten **Quiz-Punkten**.\nNicht zu verwechseln mit `/serverxp_rangliste` — das ist die Wertung über die Server-XP. Wer viel schreibt, aber selten Quiz spielt, steht dort weit oben und hier vielleicht gar nicht."},
    )
    async def quiz_rangliste(self, ctx: commands.Context):
        top_players = modul_quiz.get_leaderboard(15)
        if not top_players:
            await ctx.send("Es hat noch niemand Punkte im Pflanzenquiz gesammelt.")
            return

        embed = discord.Embed(
            title="🏆 Pflanzenquiz: Top 15",
            description=(
                "Punkte gibt es ausschliesslich fuer richtige Quizantworten. "
                "Die serverweiten XP stehen getrennt davon in `/serverxp_rangliste`."
            ),
            color=discord.Color.gold(),
        )
        for idx, player in enumerate(top_players, start=1):
            user_id = int(player["user_id"])
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            embed.add_field(name=f"{idx}. {user.name}", value=f"Punkte: {player['score']}", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="rules", description="Die Spielregeln des Pflanzenquiz",
        extras={"hilfe": "Erklärt, wie viele Punkte es pro richtiger Antwort gibt, wie lange du zum Antworten hast und welche Wartezeit zwischen zwei Fragen liegt."},)
    async def rules(self, ctx: commands.Context):
        points = database.get_setting("quiz_points_correct", config.DEFAULT_SETTINGS["quiz_points_correct"])
        timeout_seconds = database.get_setting(
            "quiz_answer_timeout_seconds", config.DEFAULT_SETTINGS["quiz_answer_timeout_seconds"]
        )
        rules_text = (
            "1️⃣ **Frage beantworten:** Schreibe `/frage`.\n"
            "2️⃣ **Kategorie wählen:** Wähle eine Themenkategorie oder \"Zufällige Kategorie\".\n"
            f"3️⃣ **Antwort auswählen:** Klicke innerhalb von {timeout_seconds} Sekunden auf einen Antwort-Button.\n"
            f"4️⃣ **Punkte sammeln:** Jede richtige Antwort bringt dir **{points} Punkte**.\n"
            "5️⃣ **Keine Wiederholung:** Jede Frage wird dir nur einmal gestellt.\n"
            "6️⃣ **Rangliste:** Die Top 15 im Quiz mit `/quiz_rangliste`, oder die komplette\n"
            "Rangliste im Web-Backend unter \"Spiele\" → \"Pflanzenquiz\".\n"
        )
        embed = discord.Embed(title="📜 Spielregeln 📜", description=rules_text, color=discord.Color.blue())
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="botgame", description="Schickt dir die ausführlichen Quiz-Infos per DM",
        extras={"hilfe": "Ich schicke dir eine private Nachricht mit allem Wissenswerten zum Pflanzenquiz.\nWenn du keine Direktnachrichten von Servermitgliedern annimmst, kann ich dir nichts schicken — ich sage dir dann im Kanal Bescheid."},)
    async def botgame(self, ctx: commands.Context):
        points = database.get_setting("quiz_points_correct", config.DEFAULT_SETTINGS["quiz_points_correct"])
        embed = discord.Embed(
            title="Pflanzenquiz",
            description="Willkommen zum Blackforest Craft&Grow Pflanzenquiz!\n\n"
                        "Hier kannst du ab sofort dein Pflanzenwissen unter Beweis stellen, viel Spaß beim Spielen.",
            color=discord.Color.purple(),
        )
        embed.add_field(
            name="Spielregeln",
            value=f"🌿 Wähle eine Kategorie, beantworte spannende Fragen und sammle Punkte!\n"
                  f"Jede richtige Antwort bringt {points} Punkte\nFragen werden nicht doppelt gestellt\n"
                  "Zeige dein Wissen und erreiche die Spitze des Leaderboards!",
            inline=True,
        )
        embed.add_field(
            name="Spielbefehle",
            value="`/frage` – Starte eine neue Frage\n`/gamescore` – Zeige deinen Punktestand an\n"
                  "`/quiz_rangliste` – Die Top 15 im Quiz\n`/rules` – Zeige die Spielregeln\n"
                  "`/frage_einreichen` – Reiche eine eigene Frage direkt per Befehl ein\n\n"
                  "Viel Erfolg und Spaß beim Spielen!",
            inline=True,
        )
        try:
            await ctx.author.send(embed=embed)
            await ctx.send(f"{ctx.author.mention}, die Botgame-Infos wurden dir per Direktnachricht geschickt!")
        except discord.Forbidden:
            await ctx.send(f"{ctx.author.mention}, ich konnte dir keine Direktnachricht schicken. Bitte überprüfe deine DM-Einstellungen!")

    @commands.hybrid_command(
        name="frage_einreichen",
        description="Reiche eine eigene Quizfrage ein",
        extras={"antwort": "manuell", "hilfe": "Du kannst eigene Fragen beisteuern. Gib den Fragetext, vier Antworten und die richtige Antwort an.\nDeine Frage wird **nicht sofort** gespielt: Ein Admin sieht sie sich vorher an. Für deine erste eingereichte Frage gibt es das Achievement „Community-Mitglied\".", "recht": "submit_questions"},
    )
    @discord.app_commands.describe(
        frage="Der Fragetext",
        antwort_a="Antwortmöglichkeit A",
        antwort_b="Antwortmöglichkeit B",
        antwort_c="Antwortmöglichkeit C",
        antwort_d="Antwortmöglichkeit D",
        richtige_antwort="Welche der vier Antworten korrekt ist",
    )
    @commands.check(_darf_fragen_einreichen)
    async def frage_einreichen(
        self,
        ctx: commands.Context,
        frage: str,
        antwort_a: str,
        antwort_b: str,
        antwort_c: str,
        antwort_d: str,
        richtige_antwort: Literal["A", "B", "C", "D"],
    ):
        """Erfuellt dieselbe Funktion wie das Web-Formular /dbot-/qinput, direkt per
        Discord-Befehl - ohne Umweg uebers Backend. Nutzt bewusst dieselbe zentrale
        Validierung (database.validate_question_submission()) und dieselbe
        Speicherfunktion (modul_quiz.submit_question()) wie die /qinput-Route in
        backend/app.py, damit die Regeln nicht an zwei Stellen auseinanderlaufen
        koennen. Discords Literal["A","B","C","D"]-Typ gibt bei der Slash-Variante
        bereits eine Auswahlliste vor; die Prefix-Variante (>frage_einreichen) kann
        trotzdem einen beliebigen String uebergeben, daher zusaetzlich serverseitig
        validiert."""
        error = database.validate_question_submission(frage, antwort_a, antwort_b, antwort_c, antwort_d, richtige_antwort)
        if error:
            await ctx.send(f"⚠️ {error}", ephemeral=True)
            return

        # Erst ab hier steht fest, dass die Antwort oeffentlich wird - die
        # Meldungen der Pruefung oben gehen nur an den Aufrufer. Deshalb traegt
        # dieser Befehl "manuell" und bestaetigt selbst, an der einzigen Stelle,
        # an der die Antwortart feststeht (siehe interaktion.py).
        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            await ctx.defer()

        modul_quiz.submit_question(frage, antwort_a, antwort_b, antwort_c, antwort_d, richtige_antwort, ctx.author.id)
        await verdiente_rollen.verdiente_rollen_pruefen(
            self.bot, ctx.guild.get_member(ctx.author.id) if ctx.guild else None)
        await ctx.send(
            f"✅ Danke {ctx.author.mention}! Deine Frage wurde eingereicht und wartet jetzt auf Verifizierung "
            "durch ein Team-Mitglied."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Quiz(bot))
