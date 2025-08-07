import discord
from discord.ext import commands
import aiohttp
import json

class CoinConverter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_url = "https://api.exchangerate-api.com/v4/latest/"
    
    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{__name__} is online!")
    
    @commands.command(name="coin")
    async def coin_convert(self, ctx, amount: float, from_currency: str, to_currency: str):
        """
        Convert an amount from one currency to another.
        Usage: %coin <amount> <from_currency> <to_currency>
        Example: %coin 5 BRL USD
        """
        try:
            # Convert currencies to uppercase
            from_currency = from_currency.upper()
            to_currency = to_currency.upper()
            
            # Fetch exchange rates from API
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}{from_currency}") as response:
                    if response.status != 200:
                        await ctx.send(f"Error: Couldn't fetch exchange rates for {from_currency}.")
                        return
                    
                    data = await response.json()
                    
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
                    
        except ValueError:
            await ctx.send("Error: Please provide a valid amount to convert.")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

async def setup(bot):
    await bot.add_cog(CoinConverter(bot))
