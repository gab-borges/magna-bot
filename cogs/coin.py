import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import asyncio
import logging
import re

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

        # Fetch exchange rates from API
        await defer(ctx)
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                async with session.get(f"{self.api_url}{from_currency}") as response:
                    if response.status != 200:
                        await ctx.send(f"Error: Couldn't fetch exchange rates for {from_currency}.")
                        return

                    data = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            logger.exception("Could not fetch exchange rates for %s", from_currency)
            await ctx.send("Error: The exchange rate service isn't responding. Try again later.")
            return

        if "rates" not in data or to_currency not in data["rates"]:
            await ctx.send(f"Error: Currency {to_currency} not found in exchange rates.")
            return

        # Get exchange rate and calculate converted amount
        exchange_rate = data["rates"][to_currency]
        converted_amount = amount * exchange_rate

        # Create and send an embedded message with the result
        embed = discord.Embed(
            title="Currency Conversion",
            description=f"{amount:.2f} {from_currency} = **{converted_amount:.2f} {to_currency}**",
            color=discord.Color.green()
        )
        embed.add_field(name="Exchange Rate", value=f"1 {from_currency} = {exchange_rate:.6f} {to_currency}", inline=False)

        await ctx.send(embed=embed)

    @coin_convert.autocomplete('from_currency')
    @coin_convert.autocomplete('to_currency')
    async def currency_autocomplete(self, interaction, current: str):
        return currency_choices(current)

async def setup(bot):
    await bot.add_cog(CoinConverter(bot))
