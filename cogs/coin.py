import discord
from discord.ext import commands
import aiohttp
import asyncio
import logging
import re

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)
CURRENCY_CODE = re.compile(r'[A-Z]{3}')


class CoinConverter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_url = "https://api.exchangerate-api.com/v4/latest/"
    
    @commands.command(name="coin")
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

async def setup(bot):
    await bot.add_cog(CoinConverter(bot))
