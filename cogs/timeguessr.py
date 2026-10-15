import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import asyncio
import logging
import math
import random
import re
import time
import typing
import unicodedata
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass

from magna import countries
from magna.slash import defer
from magna.storage import data_file, load_json, save_json

logger = logging.getLogger(__name__)

# > TimeGuessr Game <

# --- Scoring helpers ---

def haversine(lat1, lon1, lat2, lon2):
    """Return distance in km between two lat/lng points."""
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def year_score(guess_year, actual_year):
    """Max 2500 pts. Lose 100 per year off."""
    diff = abs(guess_year - actual_year)
    return max(0, 2500 - diff * 100)


def location_score(dist_km):
    """Max 2500 pts. Exponential decay based on distance."""
    return round(2500 * math.exp(-dist_km / 2000))


def parse_guess(text):
    """Parse 'country, year' from a message. Returns (country, year) or None.

    Guesses go through the round's button now; this only spots one typed in
    the chat, to delete it before someone copies it.
    """
    if "," not in text:
        return None
    parts = text.rsplit(",", 1)
    country = parts[0].strip()
    year_str = parts[1].strip()
    try:
        year = int(year_str)
    except ValueError:
        return None
    if not country:
        return None
    return (country, year)


HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)

SITE = "https://timeguessr.com"
DAILY_URL = f"{SITE}/getDaily"
# Free play: every request deals 5 random photos. Like the archive, it's the
# data the site's own pages load (SvelteKit), not a documented API.
RANDOM_URL = f"{SITE}/game-settings/__data.json?x-sveltekit-invalidated=01"
ARCHIVE_URL = f"{SITE}/archive/__data.json"
DAY_URL = f"{SITE}/play/__data.json"

SEEN_FILE = "timeguessr_seen.json"
ROUNDS_PER_GAME = 5
# Fetches of each source per game before settling for fewer new photos.
MAX_FETCHES = 3


def unflatten(values):
    """Rebuild the value SvelteKit's devalue format flattened into `values`.

    Lists and dicts hold indices into `values`; index 0 is the root.
    Negative indices stand for undefined/NaN/... and become None. The typed
    forms (["Date", ...], ["Map", ...], ...) don't appear in this data and
    aren't supported.
    """
    built = {}

    def build(index):
        if index < 0:
            return None
        if index in built:
            return built[index]
        value = values[index]
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                raise ValueError(f"unsupported devalue type {value[0]!r}")
            built[index] = result = []
            result.extend(build(i) for i in value)
        elif isinstance(value, dict):
            built[index] = result = {}
            for key, i in value.items():
                result[key] = build(i)
        else:
            built[index] = result = value
        return result

    return build(0)


def page_data(doc, key):
    """The data of the page node that has `key`, from a __data.json response."""
    for node in doc.get("nodes") or []:
        if isinstance(node, dict) and node.get("type") == "data":
            data = unflatten(node["data"])
            if isinstance(data, dict) and key in data:
                return data
    return None


def playable(item):
    """Whether a round object has what a round needs (photo, year, place)."""
    if not isinstance(item, dict) or not item.get("URL"):
        return False
    try:
        int(item["Year"])
    except (KeyError, TypeError, ValueError):
        return False
    return isinstance(item.get("Location"), dict)


def image_key(round_data):
    return round_data.get("ImageId") or round_data["URL"]


def month_range(first, last):
    """The "YYYY-MM" months from `first` to `last`, both included."""
    year, month = map(int, first.split("-"))
    end = tuple(map(int, last.split("-")))
    months = []
    while (year, month) <= end:
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months or [last]


def street_view_link(embed_url):
    """A Google Maps link to the Street View that TimeGuessr embeds.

    The embed URL opens too, but only in a browser; this one also opens the
    Maps app. Falls back to the embed URL when it isn't the expected shape.
    """
    match = re.search(r"!1s([^!]+)!2m2!1d[-\d.]+!2d[-\d.]+!3f([-\d.]+)!4f([-\d.]+)", embed_url)
    if not match:
        return embed_url
    pano, heading, pitch = match.groups()
    query = urllib.parse.urlencode({
        "api": 1, "map_action": "pano", "pano": pano, "heading": heading, "pitch": pitch,
    })
    return f"https://www.google.com/maps/@?{query}"


def fit_lines(entries, limit):
    """Join entries one per line, cutting the ones that don't fit in `limit`.

    Each entry is kept only with room left for the "…e mais N" that would
    follow it, so that line always fits.
    """
    lines = []
    for shown, entry in enumerate(entries):
        left_after = len(entries) - shown - 1
        tail = f"\n…e mais {left_after}" if left_after else ""
        if len("\n".join(lines + [entry]) + tail) > limit:
            return "\n".join(lines) + f"\n…e mais {len(entries) - shown}"
        lines.append(entry)
    return "\n".join(lines)


def thousands(number):
    """12345 -> "12.345"."""
    return f"{round(number):,}".replace(",", ".")


def years_text(years_off):
    if years_off == 0:
        return "📅 ano exato!"
    return f"📅 {years_off} {'ano' if years_off == 1 else 'anos'} de diferença"


@dataclass
class Guess:
    author: discord.abc.User
    text: str  # the country as typed
    year: int
    country: typing.Optional[countries.Country]  # None when not recognized


@dataclass
class Result:
    score: int
    years_off: int
    km: typing.Optional[float]  # from the guessed country's center; None if not recognized
    right_country: bool


async def event_within(event, timeout):
    """Whether `event` is set within `timeout` seconds."""
    if event.is_set():
        return True
    try:
        await asyncio.wait_for(event.wait(), timeout=max(timeout, 0))
        return True
    except asyncio.TimeoutError:
        return False


class GuessRound:
    """The guesses of one round, made through its button so nobody sees them.

    The round ends early once every player in `players` (those who played
    the earlier rounds) has guessed.
    """

    def __init__(self, number, players=()):
        self.number = number
        self.players = set(players)
        self.guesses = {}  # user_id -> Guess; a new guess replaces the old one
        self.everyone_in = asyncio.Event()
        self.open = True
        self.message = None  # the round's message, to show who has guessed
        self.embed = None

    async def submit(self, interaction, country_text, year_text):
        if not self.open:
            await interaction.response.send_message("⏰ Essa rodada já acabou.", ephemeral=True)
            return
        try:
            year = int(year_text.strip())
        except ValueError:
            await interaction.response.send_message(
                f"⚠️ “{year_text}” não é um ano. Clique em **Palpitar** de novo e use só números, tipo `1985`.",
                ephemeral=True,
            )
            return

        text = country_text.strip()
        country = countries.find(text)
        self.guesses[interaction.user.id] = Guess(interaction.user, text, year, country)
        if country:
            reply = (
                f"✅ Palpite registrado: **{country.name}**, {year}.\n"
                "Dá pra mudar clicando em **Palpitar** de novo até a rodada acabar."
            )
        else:
            reply = (
                f"⚠️ Palpite registrado, mas não reconheci o país “{text}”: assim ele vale "
                "0 pts de local. Clique em **Palpitar** de novo para corrigir."
            )
        await interaction.response.send_message(reply, ephemeral=True)

        if self.players and self.players <= self.guesses.keys():
            self.everyone_in.set()
        await self.show_progress()

    async def show_progress(self):
        """List who has guessed (not what) under the photo."""
        if self.message is None or self.embed is None:
            return
        names = ", ".join(guess.author.display_name for guess in self.guesses.values())
        self.embed.set_footer(text=f"✏️ Já palpitaram: {names}"[:2048])
        try:
            await self.message.edit(embed=self.embed)
        except discord.HTTPException:
            logger.warning("Could not update the round message", exc_info=True)


class GuessModal(discord.ui.Modal):
    country = discord.ui.TextInput(label="País", placeholder="ex: Brasil, Japão, EUA", max_length=60)
    year = discord.ui.TextInput(label="Ano", placeholder="ex: 1985", min_length=1, max_length=4)

    def __init__(self, round_, user_id):
        super().__init__(title=f"Palpite — Rodada {round_.number}")
        self.round = round_
        previous = round_.guesses.get(user_id)
        if previous:
            self.country.default = previous.text
            self.year.default = str(previous.year)

    async def on_submit(self, interaction):
        await self.round.submit(interaction, self.country.value, self.year.value)


class GuessView(discord.ui.View):
    def __init__(self, round_, timeout):
        super().__init__(timeout=timeout)
        self.round = round_

    @discord.ui.button(label="Palpitar", emoji="✏️", style=discord.ButtonStyle.primary)
    async def guess(self, interaction, button):
        if not self.round.open:
            await interaction.response.send_message("⏰ Essa rodada já acabou.", ephemeral=True)
            return
        await interaction.response.send_modal(GuessModal(self.round, interaction.user.id))

    def close(self):
        self.stop()
        for item in self.children:
            item.disabled = True


class GameMode(commands.Converter):
    """'aleatorio' or 'diario', accents and case ignored."""

    choices = ("aleatorio", "diario")

    async def convert(self, ctx, argument):
        plain = unicodedata.normalize("NFKD", argument.lower()).encode("ascii", "ignore").decode()
        if plain in self.choices:
            return plain
        raise commands.BadArgument(f"Modo desconhecido: {argument}")


class TimeGuessr(commands.Cog):
    min_round_time = 10  # the 10-second warning needs at least this much
    max_round_time = 120
    warning_lead = 10  # seconds left when the warning is sent
    intro_delay = 3
    hint_lifetime = 15  # seconds the "use the button" hint stays
    # Ends a game nobody continues, so the channel isn't locked forever.
    reaction_timeout = 300

    def __init__(self, bot):
        self.bot = bot
        # channel_id -> {'starter_id': int, 'task': Task, 'round': GuessRound,
        #                'hinted': user ids told to use the button}
        self.active_games = {}
        self.archive_months = {}  # past "YYYY-MM" -> its archive listing
        # scope (server, or DM channel) -> keys of the photos already shown
        try:
            saved = load_json(data_file(SEEN_FILE), {})
        except ValueError:
            logger.exception("Could not read %s; starting over", SEEN_FILE)
            saved = {}
        self.seen = {scope: set(keys) for scope, keys in saved.items()}

    @staticmethod
    def scope_of(ctx):
        """Photos aren't repeated within a server (or a DM channel)."""
        guild = getattr(ctx, "guild", None)
        return str(guild.id) if guild else f"channel-{ctx.channel.id}"

    def mark_seen(self, scope, round_data):
        self.seen.setdefault(scope, set()).add(image_key(round_data))
        try:
            save_json(
                data_file(SEEN_FILE),
                {key: sorted(keys) for key, keys in self.seen.items()},
            )
        except OSError:
            logger.exception("Could not save %s", SEEN_FILE)

    async def fetch_rounds(self, mode="aleatorio", scope=None):
        """The rounds of a game, or None when TimeGuessr couldn't be reached."""
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                if mode == "diario":
                    return await self.fetch_daily(session)
                return await self.fetch_unseen(session, self.seen.get(scope, set()))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
            logger.warning("Could not fetch TimeGuessr rounds", exc_info=True)
            return None

    @staticmethod
    async def get_json(session, url, params=None):
        async with session.get(url, params=params) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def fetch_daily(self, session):
        """Today's 5 rounds (the API adds a trailing 0 to the list)."""
        data = await self.get_json(session, DAILY_URL)
        return [item for item in data if playable(item)] or None

    async def fetch_unseen(self, session, seen):
        """5 photos not shown before, from free play and past dailies.

        A source that fails is skipped as long as the other one works.
        """
        found = {}  # key -> round, keeps the order they came in
        failures = 0
        for _ in range(MAX_FETCHES):
            for source in (self.fetch_free_play, self.fetch_archive_day):
                try:
                    rounds = await source(session)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
                    logger.warning("TimeGuessr source %s failed", source.__name__, exc_info=True)
                    failures += 1
                    continue
                for item in rounds:
                    key = image_key(item)
                    if key not in seen:
                        found.setdefault(key, item)
            if len(found) >= ROUNDS_PER_GAME:
                break
        if not found and failures == 2 * MAX_FETCHES:
            return None
        rounds = list(found.values())
        random.shuffle(rounds)
        return rounds[:ROUNDS_PER_GAME]

    async def fetch_free_play(self, session):
        data = page_data(await self.get_json(session, RANDOM_URL), "playArray")
        return [item for item in data["playArray"] if playable(item)]

    async def fetch_archive_day(self, session):
        """The rounds of a random past daily."""
        current = page_data(await self.get_json(session, ARCHIVE_URL), "archive")["archive"]
        month = random.choice(month_range(current["minMonth"], current["currentMonth"]))
        listing = current if month == current["month"] else await self.archive_month(session, month)
        days = [day for day in listing["days"] if day.get("link") and not day.get("isToday")]
        if not days:
            return []
        link = urllib.parse.urlsplit(random.choice(days)["link"])
        archive = urllib.parse.parse_qs(link.query)["archive"][0]
        doc = await self.get_json(session, DAY_URL, {"mode": "daily", "archive": archive})
        data = page_data(doc, "playArray")
        return [item for item in data["playArray"] if playable(item)]

    async def archive_month(self, session, month):
        """A past month's listing; those don't change, so they're kept."""
        if month not in self.archive_months:
            doc = await self.get_json(session, ARCHIVE_URL, {"month": month})
            self.archive_months[month] = page_data(doc, "archive")["archive"]
        return self.archive_months[month]

    @commands.hybrid_command(name="timeguessr", aliases=["tg"])
    @app_commands.describe(
        modo="Aleatório: fotos que ainda não apareceram aqui (padrão). Diário: o desafio de hoje.",
        tempo=f"Segundos por rodada, de {min_round_time} a {max_round_time} (padrão: 30)",
    )
    @app_commands.choices(modo=[
        app_commands.Choice(name="Aleatório", value="aleatorio"),
        app_commands.Choice(name="Diário", value="diario"),
    ])
    async def timeguessr(self, ctx, modo: typing.Optional[GameMode] = None, tempo: int = 30):
        """
        Start a TimeGuessr game!
        An image is shown each round and players guess where/when it was taken.
        Usage: %timeguessr [diario] [segundos]  (padrão: aleatório, 30)
        Sem modo, as fotos são sorteadas e não se repetem no servidor;
        "diario" joga o desafio do dia do site.
        O palpite (país e ano) é pelo botão Palpitar de cada foto e só aparece
        na resposta. A rodada acaba antes quando todo mundo já palpitou.
        Use %tgstop para encerrar o jogo.
        """
        modo = modo or "aleatorio"
        if not self.min_round_time <= tempo <= self.max_round_time:
            await ctx.send(
                f"⚠️ O tempo por rodada deve ficar entre {self.min_round_time} "
                f"e {self.max_round_time} segundos."
            )
            return

        # Prevent multiple games in the same channel
        if ctx.channel.id in self.active_games:
            await ctx.send("⚠️ Já tem um jogo de TimeGuessr rolando nesse canal! Espere terminar.")
            return

        game = {'starter_id': ctx.author.id, 'task': asyncio.current_task()}
        self.active_games[ctx.channel.id] = game

        try:
            await defer(ctx)
            scope = self.scope_of(ctx)
            rounds = await self.fetch_rounds(modo, scope)
            if rounds is None:
                await ctx.send("❌ Não consegui buscar os dados do TimeGuessr. Tente novamente mais tarde.")
                return
            if not rounds:
                await ctx.send(
                    "😮 Não achei fotos novas para esse servidor agora. "
                    "Tente de novo daqui a pouco, ou jogue o desafio do dia com `%tg diario`."
                )
                return

            # Per-player total scores across rounds
            scoreboard = defaultdict(int)  # user_id -> total score

            await ctx.send(
                embed=discord.Embed(
                    title="🌍 TimeGuessr",
                    description=(
                        f"{'📅 Desafio diário' if modo == 'diario' else '🎲 Fotos aleatórias'} — "
                        "o jogo vai começar!\n"
                        f"**{len(rounds)} rodadas** — {tempo} segundos cada.\n\n"
                        "Em cada foto, clique em **✏️ Palpitar** e diga o país e o ano "
                        "(em português ou inglês, tanto faz). Ninguém vê o seu palpite "
                        "até a resposta.\n"
                        "Depois da primeira rodada, a rodada acaba assim que todo mundo "
                        "que está jogando palpitou."
                    ),
                    color=discord.Color.gold(),
                )
            )
            await asyncio.sleep(self.intro_delay)

            for i, round_data in enumerate(rounds, start=1):
                self.mark_seen(scope, round_data)
                guesses = await self.play_round(ctx, i, len(rounds), round_data, tempo, set(scoreboard))
                round_scores = self.score_round(round_data, guesses)

                # Accumulate scores; whoever guessed is a player from now on
                for user_id, result in round_scores.items():
                    scoreboard[user_id] += result.score

                answer_msg = await self.show_answer(ctx, i, round_data, guesses, round_scores)

                # Wait for someone to react ✅ before next round
                if i < len(rounds):
                    await answer_msg.add_reaction("✅")
                    try:
                        await self.bot.wait_for(
                            "reaction_add",
                            check=lambda reaction, user: (
                                reaction.message.id == answer_msg.id
                                and str(reaction.emoji) == "✅"
                                and not user.bot
                            ),
                            timeout=self.reaction_timeout,
                        )
                    except asyncio.TimeoutError:
                        await ctx.send("⏹️ Jogo encerrado por inatividade.")
                        break

            # Game over — show final scoreboard
            await self.show_final_scoreboard(ctx, scoreboard)

        finally:
            if self.active_games.get(ctx.channel.id) is game:
                del self.active_games[ctx.channel.id]

    @commands.hybrid_command(name="tgstop", aliases=["tgparar"])
    async def timeguessr_stop(self, ctx):
        """Encerra o jogo de TimeGuessr deste canal (quem começou ou moderadores)."""
        game = self.active_games.get(ctx.channel.id)
        if game is None:
            await ctx.send("Nenhum jogo de TimeGuessr rolando nesse canal.")
            return

        is_starter = ctx.author.id == game['starter_id']
        is_moderator = ctx.channel.permissions_for(ctx.author).manage_messages
        if not (is_starter or is_moderator):
            await ctx.send("⚠️ Só quem começou o jogo (ou um moderador) pode encerrá-lo.")
            return

        # The game's `finally` frees the channel once the task unwinds.
        game['task'].cancel()
        await ctx.send("⏹️ Jogo de TimeGuessr encerrado.")

    async def play_round(self, ctx, round_num, total_rounds, round_data, tempo=30, players=()):
        """Show the photo and take guesses for `tempo` seconds, or until every
        player in `players` has guessed. Returns {user_id: Guess}."""
        deadline_unix = int(time.time()) + tempo
        round_ = GuessRound(round_num, players)

        embed = discord.Embed(
            title=f"Rodada {round_num}/{total_rounds}",
            description=(
                "Onde e quando essa foto foi tirada?\n"
                "Clique em **✏️ Palpitar** e responda com o país e o ano.\n\n"
                f"⏱️ Tempo acaba <t:{deadline_unix}:R>"
            ),
            color=discord.Color.blue(),
        )
        embed.set_image(url=round_data["URL"])
        view = GuessView(round_, timeout=tempo + 60)
        round_.embed = embed
        round_.message = await ctx.send(embed=embed, view=view)

        game = self.active_games.get(ctx.channel.id)
        if game is not None:
            game['round'] = round_
        try:
            everyone_in = await self.wait_for_guesses(ctx, round_, tempo)
        finally:
            round_.open = False
            view.close()
        try:
            await round_.message.edit(view=view)
        except discord.HTTPException:
            logger.warning("Could not disable the guess button", exc_info=True)
        if everyone_in:
            await ctx.send("✅ Todo mundo já palpitou!")
        return round_.guesses

    async def wait_for_guesses(self, ctx, round_, tempo):
        """Wait out the round, warning when time is almost up.

        Returns True when everyone guessed before the time ran out.
        """
        lead = min(self.warning_lead, tempo)
        if await event_within(round_.everyone_in, tempo - lead):
            return True
        await ctx.send(f"⏰ **{self.warning_lead} segundos restantes!**")
        return await event_within(round_.everyone_in, lead)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Delete a guess typed in the chat during a round, before it's copied."""
        game = self.active_games.get(message.channel.id)
        round_ = game.get('round') if game else None
        if round_ is None or not round_.open or message.author.bot:
            return
        if message.content.startswith("%") or not parse_guess(message.content):
            return
        try:
            await message.delete()
        except discord.HTTPException:
            pass  # no permission to delete it, or already gone
        hinted = game.setdefault('hinted', set())
        if message.author.id in hinted:
            return
        hinted.add(message.author.id)
        await message.channel.send(
            f"🤫 {message.author.mention}, o palpite agora é pelo botão **✏️ Palpitar** "
            "na foto, para ninguém ver o seu.",
            delete_after=self.hint_lifetime,
        )

    def score_round(self, round_data, guesses):
        """{user_id: Result} for each player's guess in this round."""
        actual_year = int(round_data["Year"])
        answer = round_data.get("Country", "")
        answer_codes = {country.code for country in countries.find_all(answer)}
        location = round_data.get("Location", {})
        lat, lng = location.get("lat", 0), location.get("lng", 0)

        results = {}
        for user_id, guess in guesses.items():
            km = haversine(guess.country.lat, guess.country.lng, lat, lng) if guess.country else None
            right_country = (
                guess.country is not None and guess.country.code in answer_codes
            ) or countries.normalize(guess.text) == countries.normalize(answer)
            if right_country:
                loc_score = 2500
            else:
                loc_score = location_score(km) if km is not None else 0
            results[user_id] = Result(
                score=year_score(guess.year, actual_year) + loc_score,
                years_off=abs(guess.year - actual_year),
                km=km,
                right_country=right_country,
            )
        return results

    async def show_answer(self, ctx, round_num, round_data, guesses, round_scores):
        """Reveal the correct answer and each player's guess and score."""
        location = round_data.get("Location", {})
        lat = location.get("lat", "?")
        lng = location.get("lng", "?")
        links = [f"[Google Maps](https://www.google.com/maps?q={lat},{lng})"]
        if round_data.get("StreetView"):
            links.append(f"[Street View]({street_view_link(round_data['StreetView'])})")

        answer = round_data.get("Country", "?")
        named = countries.find_all(answer)
        country_text = " ou ".join(country.name for country in named) if named else answer

        embed = discord.Embed(
            title=f"📍 Resposta — Rodada {round_num}",
            color=discord.Color.green(),
        )
        embed.add_field(name="📅 Ano", value=round_data.get("Year", "?"), inline=True)
        embed.add_field(name="🌎 País", value=country_text, inline=True)
        embed.add_field(name="📝 Descrição", value=round_data.get("Description") or "—", inline=False)
        embed.add_field(name="🗺️ Mapa", value=" · ".join(links), inline=False)

        if guesses:
            ranked = sorted(guesses, key=lambda uid: round_scores[uid].score, reverse=True)
            entries = []
            for rank, uid in enumerate(ranked, start=1):
                guess, result = guesses[uid], round_scores[uid]
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "▫️")
                if result.right_country:
                    place = "✅ país certo"
                elif result.km is not None:
                    place = f"📍 a {thousands(result.km)} km"
                else:
                    place = "❓ país não reconhecido"
                shown = guess.country.name if guess.country else guess.text
                entries.append(
                    f"{medal} **{guess.author.display_name}** — {shown}, {guess.year} "
                    f"→ **{thousands(result.score)}** pts\n"
                    f"↳ {place} · {years_text(result.years_off)}"
                )
            embed.add_field(name="🏆 Pontuação", value=fit_lines(entries, 1024), inline=False)
        else:
            embed.add_field(name="💬 Respostas", value="Ninguém respondeu nessa rodada!", inline=False)

        embed.set_thumbnail(url=round_data["URL"])
        return await ctx.send(embed=embed)

    async def show_final_scoreboard(self, ctx, scoreboard):
        """Show the final leaderboard after all rounds."""
        if not scoreboard:
            embed = discord.Embed(
                title="🏁 Fim de jogo!",
                description="Ninguém jogou 😢",
                color=discord.Color.gold(),
            )
            await ctx.send(embed=embed)
            return

        sorted_players = sorted(scoreboard.items(), key=lambda x: x[1], reverse=True)

        lines = []
        for rank, (user_id, total) in enumerate(sorted_players, start=1):
            user = self.bot.get_user(user_id)
            name = user.display_name if user else f"User {user_id}"
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "▫️")
            lines.append(f"{medal} **{name}** — **{thousands(total)}** pts")

        embed = discord.Embed(
            title="🏁 Fim de jogo — Placar Final",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Pontuação máxima: 5.000 pts por rodada")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(TimeGuessr(bot))
