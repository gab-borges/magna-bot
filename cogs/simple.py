from discord.ext import commands

# > Simple Commands <

class Simple(commands.Cog, name="Básicos"):
    def __init__(self, bot):
        self.bot = bot

    # Commands
    @commands.hybrid_command(aliases=["hi", "howdy", "hey"])
    async def hello(self, ctx):
        """Diz oi para o Magna"""
        await ctx.send(f"Olá, {ctx.author.mention}!")

    @commands.hybrid_command(name="morning")
    async def goodmorning(self, ctx):
        """Deseja bom dia a todos"""
        await ctx.send(f"Bom dia, {ctx.author.mention}!")
    
    @commands.hybrid_command(name="ping")
    async def ping(self, ctx):
        """Mostra a latência do bot"""
        await ctx.send(f":ping_pong:  Latência do {self.bot.user.name}: {round(self.bot.latency * 1000)}ms")

async def setup(bot):
    await bot.add_cog(Simple(bot))
