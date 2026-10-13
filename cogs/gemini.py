from discord.ext import commands
import os
import asyncio
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

# > Commands that use the Gemini API <

class Gemini(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Initialize the Gemini client
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = genai.GenerativeModel('models/gemini-2.0-flash')
        self.persona = """
        You, Gemini, are Magna Shanoa. Roleplay him.
        You are the fattest man in Eorzea. You are addicted to Final Fantasy 14 Online.
        You don't mind being called fat. You have chubby cheeks and a kawaii face.
        """
    
    # Commands
    @commands.command()
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def ask(self, ctx: commands.Context, *, question: str):
        try:
            async with ctx.typing():
                full_prompt = f"{self.persona}\nThe user asks: {question}\nYou, Magna Shanoa, respond:"

                # Run synchronous Gemini API call in executor
                response = await asyncio.to_thread(
                    self.model.generate_content,
                    full_prompt,
                )

                if not response.text:
                    return await ctx.send("No response generated")
                
                # Split long responses
                chunks = [response.text[i:i+2000] for i in range(0, len(response.text), 2000)]
                
                for chunk in chunks:
                    await ctx.send(chunk)

        except genai.types.BlockedPromptException:
            await ctx.send("⚠️ That question was blocked for safety reasons")
        
        except Exception:
            logger.exception("Gemini request failed")
            await ctx.send("❌ Error with Magna's AI")

async def setup(bot):
    await bot.add_cog(Gemini(bot))