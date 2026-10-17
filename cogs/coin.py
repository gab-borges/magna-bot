import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import asyncio
import logging
import math
import re
import time

from magna.countries import normalize
from magna.slash import defer

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)
CURRENCY_CODE = re.compile(r'[A-Z]{3}')
# Suggested while typing /coin; any other 3-letter code still works.
COMMON_CURRENCIES = {
    'BRL': 'Real brasileiro',
    'USD': 'Dólar americano',
    'EUR': 'Euro',
    'GBP': 'Libra esterlina',
    'JPY': 'Iene japonês',
    'CNY': 'Yuan chinês',
    'ARS': 'Peso argentino',
    'CLP': 'Peso chileno',
    'COP': 'Peso colombiano',
    'MXN': 'Peso mexicano',
    'UYU': 'Peso uruguaio',
    'PYG': 'Guarani paraguaio',
    'PEN': 'Sol peruano',
    'CAD': 'Dólar canadense',
    'AUD': 'Dólar australiano',
    'NZD': 'Dólar neozelandês',
    'CHF': 'Franco suíço',
    'SEK': 'Coroa sueca',
    'NOK': 'Coroa norueguesa',
    'DKK': 'Coroa dinamarquesa',
    'PLN': 'Zloty polonês',
    'RUB': 'Rublo russo',
    'TRY': 'Lira turca',
    'INR': 'Rupia indiana',
    'KRW': 'Won sul-coreano',
    'HKD': 'Dólar de Hong Kong',
    'SGD': 'Dólar de Singapura',
    'ZAR': 'Rand sul-africano',
    'ILS': 'Novo shekel israelense',
    'AED': 'Dirham dos Emirados',
}
# Discord shows at most this many autocomplete suggestions.
MAX_CHOICES = 25
# Rates are reused this many seconds before asking the API again.
RATES_TTL = 5 * 60


def format_number(value, decimals=2):
    """Write a number the Brazilian way: 1234.5 becomes 1.234,50."""
    return f"{value:,.{decimals}f}".translate(str.maketrans(',.', '.,'))


def currency_choices(current):
    """Common currencies whose code or name contains what was typed (accents ignored)."""
    text = normalize(current)
    return [
        app_commands.Choice(name=f"{code} — {name}", value=code)
        for code, name in COMMON_CURRENCIES.items()
        if text in code.lower() or text in normalize(name)
    ][:MAX_CHOICES]


class CoinConverter(commands.Cog, name="Moedas"):
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
        valor="Quanto dinheiro converter",
        de="Moeda de origem, como BRL",
        para="Moeda de destino, como USD",
    )
    async def coin_convert(self, ctx, valor: float, de: str, para: str):
        """
        Converte um valor de uma moeda para outra.
        Uso: %coin <valor> <de> <para>
        Exemplo: %coin 5 BRL USD
        """
        # Convert currencies to uppercase
        from_currency = de.upper()
        to_currency = para.upper()

        # The code goes into the API URL, so only accept ISO-style codes.
        for code in (from_currency, to_currency):
            if not CURRENCY_CODE.fullmatch(code):
                await ctx.send(f"Erro: `{code}` não é um código de moeda (use 3 letras, como BRL).")
                return

        # float() also reads "nan" and "inf".
        if not math.isfinite(valor) or valor <= 0:
            await ctx.send("Erro: o valor precisa ser um número positivo.")
            return

        # Fetch exchange rates from API
        await defer(ctx)
        try:
            rates = await self.fetch_rates(from_currency)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            logger.exception("Could not fetch exchange rates for %s", from_currency)
            await ctx.send("Erro: o serviço de cotações não está respondendo. Tente novamente mais tarde.")
            return

        if rates is None:
            await ctx.send(f"Erro: não consegui buscar as cotações de {from_currency}.")
            return
        if to_currency not in rates:
            await ctx.send(f"Erro: a moeda {to_currency} não foi encontrada nas cotações.")
            return

        # Get exchange rate and calculate converted amount
        exchange_rate = rates[to_currency]
        converted_amount = valor * exchange_rate

        # Create and send an embedded message with the result
        embed = discord.Embed(
            title="Conversão de moeda",
            description=(
                f"{format_number(valor)} {from_currency} = "
                f"**{format_number(converted_amount)} {to_currency}**"
            ),
            color=discord.Color.green()
        )
        embed.add_field(
            name="Cotação",
            value=f"1 {from_currency} = {format_number(exchange_rate, 6)} {to_currency}",
            inline=False,
        )

        await ctx.send(embed=embed)

    @coin_convert.autocomplete('de')
    @coin_convert.autocomplete('para')
    async def currency_autocomplete(self, interaction, current: str):
        return currency_choices(current)

async def setup(bot):
    await bot.add_cog(CoinConverter(bot))
