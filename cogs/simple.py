from discord.ext import commands

# > Simple Commands <

class Simple(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Commands
    @commands.hybrid_command(aliases=["hi", "howdy", "hey"])
    async def hello(self, ctx):
        """Say hello to Magna"""
        await ctx.send(f"Hello there, {ctx.author.mention}!")

    @commands.hybrid_command(name="morning")
    async def goodmorning(self, ctx):
        """Wish everyone a good morning"""
        await ctx.send(f"Good morning, {ctx.author.mention}!")
    
    @commands.hybrid_command(name="ping")
    async def ping(self, ctx):
        """Show the bot's latency"""
        await ctx.send(f":ping_pong:  {self.bot.user.name}'s Latency: {round(self.bot.latency * 1000)}ms")

async def setup(bot):
    await bot.add_cog(Simple(bot))
