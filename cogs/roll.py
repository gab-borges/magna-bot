import discord
from discord import app_commands
from discord.ext import commands
import re
import random

MAX_DICE = 100
MAX_SIDES = 100
# Each dice term gets an embed field, and Discord allows 25 per embed.
MAX_TERMS = 20

# One term: an optional sign, then dice (XdY, or dY for one die) or a plain number.
TERM_PATTERN = re.compile(r'\s*([+-])?\s*(?:(\d*)d(\d+)|(\d+))\s*', re.IGNORECASE)


class DiceError(ValueError):
    """An expression that can't be rolled; the message is shown to the user."""


def parse_dice(expression):
    """Parse "2d20+5-1d4" into [(sign, dice, sides), ...] and a modifier."""
    terms, modifier, pos = [], 0, 0
    while pos < len(expression):
        match = TERM_PATTERN.match(expression, pos)
        # Every term after the first needs a sign to join it to the previous one.
        if not match or match.end() == pos or (pos > 0 and not match.group(1)):
            raise DiceError("❌ Formato de dados inválido! Use algo como: 2d20+5 ou 3d6+2d4-2")
        pos = match.end()

        sign = -1 if match.group(1) == '-' else 1
        if match.group(4) is not None:
            modifier += sign * int(match.group(4))
            continue

        dice, sides = int(match.group(2) or 1), int(match.group(3))
        if dice > MAX_DICE:
            raise DiceError(f"❌ Não dá para rolar mais de {MAX_DICE} dados de uma vez!")
        if sides > MAX_SIDES:
            raise DiceError(f"❌ Os dados não podem ter mais de {MAX_SIDES} lados!")
        if dice < 1 or sides < 1:
            raise DiceError("❌ É preciso pelo menos 1 dado com pelo menos 1 lado!")
        terms.append((sign, dice, sides))

    if not terms and not modifier:
        raise DiceError("❌ Formato de dados inválido! Use algo como: 2d20+5 ou 3d6+2d4-2")
    if len(terms) > MAX_TERMS:
        raise DiceError(f"❌ Não dá para combinar mais de {MAX_TERMS} tipos de dados!")
    return terms, modifier


class Roll(commands.Cog, name="Dados"):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command()
    @app_commands.describe(dados="Dados para rolar, como 2d20+5 ou 3d6+2d4-2")
    async def roll(self, ctx, *, dados: str):
        """Rola dados no formato XdY+Z
        Exemplo: %roll 2d20+5 (rola 2 dados d20 e soma 5)
        Dá para combinar vários dados: %roll 5d6+2d4-2"""
        try:
            terms, modifier = parse_dice(dados)
        except DiceError as e:
            await ctx.send(str(e))
            return

        # Create a nice embed to show the results
        embed = discord.Embed(
            title="🎲 Resultado dos dados",
            color=discord.Color.blue()
        )

        total = modifier
        for sign, dice, sides in terms:
            dice_rolls = [random.randint(1, sides) for _ in range(dice)]
            total += sign * sum(dice_rolls)
            embed.add_field(
                name=f"{'-' if sign < 0 else ''}{dice}d{sides}",
                value=f"[{', '.join(map(str, dice_rolls))}]",
                inline=False
            )

        if modifier:
            embed.add_field(name="Modificador", value=f"{modifier:+d}", inline=False)

        # Add the total
        embed.add_field(
            name="Total",
            value=str(total),
            inline=False
        )

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Roll(bot))
