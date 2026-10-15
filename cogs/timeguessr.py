import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import asyncio
import logging
import math
import random
import time
import typing
import unicodedata
import urllib.parse
from collections import defaultdict

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
    """Parse 'country, year' from a message. Returns (country, year) or None."""
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
    intro_delay = 3
    # Ends a game nobody continues, so the channel isn't locked forever.
    reaction_timeout = 300

    def __init__(self, bot):
        self.bot = bot
        self.active_games = {}  # channel_id -> {'starter_id': int, 'task': Task}
        self.country_cache = {}  # name -> (lat, lng) or None
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

    async def geocode_country(self, country_name):
        """Get (lat, lng) for a country name via restcountries API. Cached.

        An exact name match comes first, so "Niger" isn't taken for "Nigeria";
        a partial match is the fallback. Only definite answers are cached: a
        timeout or server error just scores this guess 0.
        """
        key = country_name.lower()
        if key in self.country_cache:
            return self.country_cache[key]

        # The name comes from chat, so it must not alter the URL path.
        quoted = urllib.parse.quote(country_name, safe="")
        url = f"https://restcountries.com/v3.1/name/{quoted}"
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                result = await self.fetch_latlng(session, f"{url}?fullText=true")
                if result is None:
                    result = await self.fetch_latlng(session, url)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            logger.warning("Could not geocode %r", country_name, exc_info=True)
            return None

        self.country_cache[key] = result
        return result

    @staticmethod
    async def fetch_latlng(session, url):
        """(lat, lng) of the first country found, or None when there's none.

        Raises aiohttp.ClientError for answers other than found / not found.
        """
        async with session.get(url) as response:
            if response.status == 404:
                return None
            response.raise_for_status()
            data = await response.json()
        if data and isinstance(data, list):
            latlng = data[0].get("latlng")
            if latlng and len(latlng) >= 2:
                return (latlng[0], latlng[1])
        return None

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
        Format: país, ano  (ex: Brazil, 2016)
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
                        "Envie seu palpite no formato: **país, ano**\n"
                        "Exemplo: `Brazil, 2016`"
                    ),
                    color=discord.Color.gold(),
                )
            )
            await asyncio.sleep(self.intro_delay)

            for i, round_data in enumerate(rounds, start=1):
                self.mark_seen(scope, round_data)
                guesses = await self.play_round(ctx, i, len(rounds), round_data, tempo)
                round_scores = await self.score_round(round_data, guesses)

                # Accumulate scores
                for user_id, score in round_scores.items():
                    scoreboard[user_id] += score

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

    async def play_round(self, ctx, round_num, total_rounds, round_data, tempo=30):
        """Display the image and collect guesses for `tempo` seconds."""
        deadline_unix = int(time.time()) + tempo

        embed = discord.Embed(
            title=f"Rodada {round_num}/{total_rounds}",
            description=(
                "Onde e quando essa foto foi tirada?\n"
                "Responda com: **país, ano**\n\n"
                f"⏱️ Tempo acaba <t:{deadline_unix}:R>"
            ),
            color=discord.Color.blue(),
        )
        embed.set_image(url=round_data["URL"])
        await ctx.send(embed=embed)

        # Collect guesses — last valid guess per player wins
        guesses = {}  # user_id -> (author, country, year)
        deadline = asyncio.get_event_loop().time() + tempo
        warned_10s = False

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break

            # Send a warning when ~10 seconds remain
            if remaining <= 10 and not warned_10s:
                warned_10s = True
                await ctx.send("⏰ **10 segundos restantes!**")

            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: (
                        m.channel.id == ctx.channel.id
                        and not m.author.bot
                        and not m.content.startswith("%")
                    ),
                    timeout=min(remaining, 1.0),
                )
                parsed = parse_guess(msg.content)
                if parsed:
                    country, year = parsed
                    guesses[msg.author.id] = (msg.author, country, year)
                    await msg.add_reaction("✅")
            except asyncio.TimeoutError:
                continue

        return guesses

    async def score_round(self, round_data, guesses):
        """Compute scores for each player's guess in this round."""
        actual_year = int(round_data["Year"])
        actual_country = round_data.get("Country", "").lower()
        actual_loc = round_data.get("Location", {})
        actual_lat = actual_loc.get("lat", 0)
        actual_lng = actual_loc.get("lng", 0)

        scores = {}  # user_id -> score

        for user_id, (author, country, guess_yr) in guesses.items():
            # Year score
            yr_score = year_score(guess_yr, actual_year)

            # Location score — exact country match = max points
            if country.lower() == actual_country:
                loc_score = 2500
            else:
                coords = await self.geocode_country(country)
                if coords:
                    dist = haversine(coords[0], coords[1], actual_lat, actual_lng)
                    loc_score = location_score(dist)
                else:
                    loc_score = 0  # Country not found

            scores[user_id] = yr_score + loc_score

        return scores

    async def show_answer(self, ctx, round_num, round_data, guesses, round_scores):
        """Reveal the correct answer and each player's score."""
        location = round_data.get("Location", {})
        lat = location.get("lat", "?")
        lng = location.get("lng", "?")
        maps_link = f"https://www.google.com/maps?q={lat},{lng}"

        embed = discord.Embed(
            title=f"📍 Resposta — Rodada {round_num}",
            color=discord.Color.green(),
        )
        embed.add_field(name="📅 Ano", value=round_data.get("Year", "?"), inline=True)
        embed.add_field(name="🌎 País", value=round_data.get("Country", "?"), inline=True)
        embed.add_field(name="📝 Descrição", value=round_data.get("Description", "—"), inline=False)
        embed.add_field(name="🗺️ Mapa", value=f"[Ver no Google Maps]({maps_link})", inline=False)

        if guesses:
            # Sort by score descending
            sorted_players = sorted(
                guesses.keys(),
                key=lambda uid: round_scores.get(uid, 0),
                reverse=True,
            )
            lines = []
            for rank, uid in enumerate(sorted_players, start=1):
                author, country, year = guesses[uid]
                score = round_scores.get(uid, 0)
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "▫️")
                lines.append(
                    f"{medal} **{author.display_name}** — "
                    f"{country}, {year} → **{score}** pts"
                )
            text = "\n".join(lines)
            if len(text) > 1024:
                text = text[:1020] + "\n..."
            embed.add_field(name="🏆 Pontuação", value=text, inline=False)
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
            lines.append(f"{medal} **{name}** — **{total}** pts")

        embed = discord.Embed(
            title="🏁 Fim de jogo — Placar Final",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Pontuação máxima: 5.000 pts por rodada")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(TimeGuessr(bot))
