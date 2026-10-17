import asyncio
import logging
import os
import signal

import discord
from discord.ext import commands
from dotenv import load_dotenv

from magna.help import HelpCommand
from magna.slash import sync_commands

# Load .env
load_dotenv('.env')

# Every module logs through `logging`; this sends it all to stdout, which is
# what `podman logs` shows.
discord.utils.setup_logging(level=logging.INFO)
logger = logging.getLogger('magna')

class MagnaBot(commands.Bot):
    async def setup_hook(self):
        # Runs after login, once the cogs (and so the slash commands) are loaded.
        try:
            await sync_commands(self)
        except discord.HTTPException:
            # %commands still work; slash commands keep their last synced version.
            logger.exception("Could not sync slash commands")


# Bot creation: Prefix Definition and Intents (to specify the events the bot will listen to)
# Only the events the cogs use: messages for %commands (and their text, a
# privileged intent), reactions for TimeGuessr's ✅, members for birthdays and
# member counts (privileged), voice states for music. guilds fills the cache
# everything else relies on.
intents = discord.Intents.none()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.reactions = True
intents.members = True
intents.voice_states = True

# Text from outside (Gemini answers, video titles) goes into messages, so it
# must never be able to ping @everyone, @here or a role.
bot = MagnaBot(
    command_prefix="%",
    help_command=HelpCommand(),
    intents=intents,
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
