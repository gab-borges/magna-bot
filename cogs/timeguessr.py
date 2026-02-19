import discord
from discord.ext import commands
import aiohttp
import asyncio
import math
from collections import defaultdict

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


class TimeGuessr(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_channels = set()
        self.api_url = "https://timeguessr.com/getDaily"
        self.country_cache = {}  # name -> (lat, lng) or None

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{__name__} is online!")

    async def fetch_rounds(self):
        """Fetch daily round data from TimeGuessr API."""
        async with aiohttp.ClientSession() as session:
            async with session.get(self.api_url) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                # The API returns a list of 5 round objects + a trailing 0
                return [item for item in data if isinstance(item, dict)]

    async def geocode_country(self, country_name):
        """Get (lat, lng) for a country name via restcountries API. Cached."""
        key = country_name.lower()
        if key in self.country_cache:
            return self.country_cache[key]

        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://restcountries.com/v3.1/name/{country_name}"
                async with session.get(url) as response:
                    if response.status != 200:
                        self.country_cache[key] = None
                        return None
                    data = await response.json()
                    if not data or not isinstance(data, list):
                        self.country_cache[key] = None
                        return None
                    latlng = data[0].get("latlng")
                    if latlng and len(latlng) >= 2:
                        result = (latlng[0], latlng[1])
                        self.country_cache[key] = result
                        return result
        except Exception:
            pass

        self.country_cache[key] = None
        return None

    @commands.command(name="timeguessr", aliases=["tg"])
    async def timeguessr(self, ctx):
        """
        Start a TimeGuessr game!
        An image is shown each round and players guess where/when it was taken.
        Usage: %timeguessr
        Format: país, ano  (ex: Brazil, 2016)
        """
        # Prevent multiple games in the same channel
        if ctx.channel.id in self.active_channels:
            await ctx.send("⚠️ Já tem um jogo de TimeGuessr rolando nesse canal! Espere terminar.")
            return

        self.active_channels.add(ctx.channel.id)

        try:
            rounds = await self.fetch_rounds()
            if not rounds:
                await ctx.send("❌ Não consegui buscar os dados do TimeGuessr. Tente novamente mais tarde.")
                return

            # Per-player total scores across rounds
            scoreboard = defaultdict(int)  # user_id -> total score

            await ctx.send(
                embed=discord.Embed(
                    title="🌍 TimeGuessr",
                    description=(
                        "O jogo vai começar!\n"
                        f"**{len(rounds)} rodadas** — 30 segundos cada.\n\n"
                        "Envie seu palpite no formato: **país, ano**\n"
                        "Exemplo: `Brazil, 2016`"
                    ),
                    color=discord.Color.gold(),
                )
            )
            await asyncio.sleep(3)

            for i, round_data in enumerate(rounds, start=1):
                guesses = await self.play_round(ctx, i, len(rounds), round_data)
                round_scores = await self.score_round(round_data, guesses)

                # Accumulate scores
                for user_id, score in round_scores.items():
                    scoreboard[user_id] += score

                answer_msg = await self.show_answer(ctx, i, round_data, guesses, round_scores)

                # Wait for someone to react ✅ before next round
                if i < len(rounds):
                    await answer_msg.add_reaction("✅")
                    await self.bot.wait_for(
                        "reaction_add",
                        check=lambda reaction, user: (
                            reaction.message.id == answer_msg.id
                            and str(reaction.emoji) == "✅"
                            and not user.bot
                        ),
                    )

            # Game over — show final scoreboard
            await self.show_final_scoreboard(ctx, scoreboard)

        finally:
            self.active_channels.discard(ctx.channel.id)

    async def play_round(self, ctx, round_num, total_rounds, round_data):
        """Display the image and collect guesses for 30 seconds."""
        embed = discord.Embed(
            title=f"Rodada {round_num}/{total_rounds}",
            description="Onde e quando essa foto foi tirada?\nResponda com: **país, ano**",
            color=discord.Color.blue(),
        )
        embed.set_image(url=round_data["URL"])
        embed.set_footer(text="⏱️ Você tem 30 segundos!")
        await ctx.send(embed=embed)

        # Collect guesses for 30 seconds — last valid guess per player wins
        guesses = {}  # user_id -> (author, country, year)
        deadline = asyncio.get_event_loop().time() + 30

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break

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
                    await msg.add_reaction(":magna:")
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
        embed.set_footer(text="Pontuação máxima: 25.000 pts (5.000 por rodada)")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(TimeGuessr(bot))
