import asyncio
import logging
import os
import signal

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
# Text from outside (Gemini answers, video titles) goes into messages, so it
# must never be able to ping @everyone, @here or a role.
bot = commands.Bot(
    command_prefix="%",
    intents=discord.Intents.all(),
    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False),
)

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
        # Python running as PID 1 in the container ignores SIGTERM unless it is
        # handled, so `podman stop` would wait and then kill the bot. Closing
        # the bot leaves voice channels and makes bot.start() return.
        asyncio.get_running_loop().add_signal_handler(
            signal.SIGTERM, lambda: asyncio.create_task(bot.close())
        )
        await load()
        await bot.start(os.getenv("TOKEN"))

asyncio.run(main())
