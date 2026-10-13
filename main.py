import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load .env
load_dotenv('.env')

# Every module logs through `logging`; this sends it all to stdout, which is
# what `podman logs` shows.
discord.utils.setup_logging(level=logging.INFO)
logger = logging.getLogger('magna')

# Bot creation: Prefix Definition and Intents (to specify the events the bot will listen to)
bot = commands.Bot(command_prefix="%", intents=discord.Intents.all())

# Rich Presence
@bot.event
async def on_ready():
    logger.info("Logged in as %s", bot.user)

    game = discord.Game("Final Fantasy XIV Online")
    await bot.change_presence(status=discord.Status.online, activity=game)


# Bot Running Section
async def load():
    for filename in sorted(os.listdir("./cogs")):
        if filename.endswith(".py"):
            await bot.load_extension(f"cogs.{filename[:-3]}")
            logger.info("Loaded cogs.%s", filename[:-3])

async def main():
    async with bot:
        await load()
        await bot.start(os.getenv("TOKEN"))

asyncio.run(main())
