import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import asyncio
import logging
import math
import re
import time

from magna.slash import defer

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)
CURRENCY_CODE = re.compile(r'[A-Z]{3}')
# Suggested while typing /coin; any other 3-letter code still works.
COMMON_CURRENCIES = {
    'BRL': 'Brazilian Real',
    'USD': 'US Dollar',
    'EUR': 'Euro',
    'GBP': 'British Pound',
    'JPY': 'Japanese Yen',
    'CNY': 'Chinese Yuan',
    'ARS': 'Argentine Peso',
    'CLP': 'Chilean Peso',
    'COP': 'Colombian Peso',
    'MXN': 'Mexican Peso',
    'UYU': 'Uruguayan Peso',
    'PYG': 'Paraguayan Guarani',
    'PEN': 'Peruvian Sol',
    'CAD': 'Canadian Dollar',
    'AUD': 'Australian Dollar',
    'NZD': 'New Zealand Dollar',
    'CHF': 'Swiss Franc',
    'SEK': 'Swedish Krona',
    'NOK': 'Norwegian Krone',
    'DKK': 'Danish Krone',
    'PLN': 'Polish Zloty',
    'RUB': 'Russian Ruble',
    'TRY': 'Turkish Lira',
    'INR': 'Indian Rupee',
    'KRW': 'South Korean Won',
    'HKD': 'Hong Kong Dollar',
    'SGD': 'Singapore Dollar',
    'ZAR': 'South African Rand',
    'ILS': 'Israeli New Shekel',
    'AED': 'UAE Dirham',
}
# Discord shows at most this many autocomplete suggestions.
MAX_CHOICES = 25
# Rates are reused this many seconds before asking the API again.
RATES_TTL = 5 * 60


def format_number(value, decimals=2):
    """Write a number the Brazilian way: 1234.5 becomes 1.234,50."""
    return f"{value:,.{decimals}f}".translate(str.maketrans(',.', '.,'))


def currency_choices(current):
    """Common currencies whose code or name contains what was typed."""
    text = current.strip().lower()
    return [
        app_commands.Choice(name=f"{code} — {name}", value=code)
        for code, name in COMMON_CURRENCIES.items()
        if text in code.lower() or text in name.lower()
    ][:MAX_CHOICES]


class CoinConverter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_url = "https://api.exchangerate-api.com/v4/latest/"
        # Base currency -> (time.monotonic() when fetched, rates)
        self.rates_cache = {}

    async def fetch_rates(self, base):
        """Rates from `base` to every currency, or None if the API refused.

        Each base is fetched at most once every RATES_TTL seconds.
        """
        cached = self.rates_cache.get(base)
        if cached and time.monotonic() - cached[0] < RATES_TTL:
            return cached[1]

        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            async with session.get(f"{self.api_url}{base}") as response:
                if response.status != 200:
                    return None
                data = await response.json()

        rates = data.get("rates") if isinstance(data, dict) else None
        if not rates:
            return {}
        self.rates_cache[base] = (time.monotonic(), rates)
        return rates

    @commands.hybrid_command(name="coin")
    @app_commands.describe(
        amount="How much money to convert",
        from_currency="Currency to convert from, like BRL",
        to_currency="Currency to convert to, like USD",
    )
    async def coin_convert(self, ctx, amount: float, from_currency: str, to_currency: str):
        """
        Convert an amount from one currency to another.
        Usage: %coin <amount> <from_currency> <to_currency>
        Example: %coin 5 BRL USD
        """
        # Convert currencies to uppercase
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        # The code goes into the API URL, so only accept ISO-style codes.
        for code in (from_currency, to_currency):
            if not CURRENCY_CODE.fullmatch(code):
                await ctx.send(f"Error: `{code}` isn't a currency code (use 3 letters, like BRL).")
                return

        # float() also reads "nan" and "inf".
        if not math.isfinite(amount) or amount <= 0:
            await ctx.send("Error: The amount must be a positive number.")
            return

        # Fetch exchange rates from API
        await defer(ctx)
        try:
            rates = await self.fetch_rates(from_currency)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            logger.exception("Could not fetch exchange rates for %s", from_currency)
            await ctx.send("Error: The exchange rate service isn't responding. Try again later.")
            return

        if rates is None:
            await ctx.send(f"Error: Couldn't fetch exchange rates for {from_currency}.")
            return
        if to_currency not in rates:
            await ctx.send(f"Error: Currency {to_currency} not found in exchange rates.")
            return

        # Get exchange rate and calculate converted amount
        exchange_rate = rates[to_currency]
        converted_amount = amount * exchange_rate

        # Create and send an embedded message with the result
        embed = discord.Embed(
            title="Currency Conversion",
            description=(
                f"{format_number(amount)} {from_currency} = "
                f"**{format_number(converted_amount)} {to_currency}**"
            ),
            color=discord.Color.green()
        )
        embed.add_field(name="Exchange Rate", value=f"1 {from_currency} = {format_number(exchange_rate, 6)} {to_currency}", inline=False)

        await ctx.send(embed=embed)

    @coin_convert.autocomplete('from_currency')
    @coin_convert.autocomplete('to_currency')
    async def currency_autocomplete(self, interaction, current: str):
        return currency_choices(current)

async def setup(bot):
    await bot.add_cog(CoinConverter(bot))
