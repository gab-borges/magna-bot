import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from google import genai
from google.genai import errors, types

logger = logging.getLogger(__name__)

# Google keeps this alias pointed at the current Flash model, so the bot
# doesn't break when a specific version is retired. Set GEMINI_MODEL to pin one.
DEFAULT_MODEL = "gemini-flash-latest"
REQUEST_TIMEOUT_MS = 30_000
DISCORD_MESSAGE_LIMIT = 2000
# How much of a slash command's question is repeated above the answer.
QUOTE_LIMIT = 300

PERSONA = """
You, Gemini, are Magna Shanoa. Roleplay him.
You are the fattest man in Eorzea. You are addicted to Final Fantasy 14 Online.
You don't mind being called fat. You have chubby cheeks and a kawaii face.
"""


def split_message(text, limit=DISCORD_MESSAGE_LIMIT):
    """Split text into Discord-sized chunks, preferring to break at newlines."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def quote(text, limit=QUOTE_LIMIT):
    """Format text as a Discord block quote, shortened to `limit` characters."""
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return "\n".join(f"> {line}" for line in text.splitlines())


# > Commands that use the Gemini API <

class Gemini(commands.Cog):
    def __init__(self, bot, client=None):
        self.bot = bot
        self.model = os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
        self.config = types.GenerateContentConfig(system_instruction=PERSONA)
        self.client = client or self.make_client()

    @staticmethod
    def make_client():
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            # Keep the rest of the bot running; %ask explains what's missing.
            logger.warning("GEMINI_API_KEY is not set; %%ask is disabled")
            return None
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )

    # Commands
    @commands.hybrid_command()
    @app_commands.describe(question="What you want to ask Magna")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def ask(self, ctx: commands.Context, *, question: str):
        """Ask Magna Shanoa anything"""
        if self.client is None:
            return await ctx.send("⚠️ Magna's AI isn't configured (missing GEMINI_API_KEY).")

        async with ctx.typing():
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=question,
                    config=self.config,
                )
            except errors.APIError as e:
                logger.error("Gemini request failed (%s): %s", e.code, e.message)
                return await ctx.send("❌ Error with Magna's AI")

        feedback = response.prompt_feedback
        if feedback is not None and feedback.block_reason:
            return await ctx.send("⚠️ That question was blocked for safety reasons")

        text = response.text
        if not text:
            return await ctx.send("No response generated")

        # A slash command's reply doesn't show what was asked, so quote it.
        if ctx.interaction is not None:
            text = f"{quote(question)}\n\n{text}"

        # Neither the question nor the answer should make the bot ping anyone.
        for chunk in split_message(text):
            await ctx.send(chunk, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Gemini(bot))
