import discord
from discord.ext import commands, tasks
import os
import asyncio
from dotenv import load_dotenv

# Load .env
load_dotenv('.env')

# Bot creation: Prefix Definition and Intents (to specify the events the bot will listen to)
bot = commands.Bot(command_prefix="%", intents=discord.Intents.all())

# Rich Presence
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    
    game = discord.Game("Final Fantasy XIV Online")
    await bot.change_presence(status=discord.Status.online, activity=game)


# Bot Running Section
async def load():
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py"):
            await bot.load_extension(f"cogs.{filename[:-3]}")

async def main():
    async with bot:
        await load()
        await bot.start(os.getenv("TOKEN"))

asyncio.run(main())
