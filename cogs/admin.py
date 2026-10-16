import discord
from discord.ext import commands

from magna.slash import sync_commands


class Admin(commands.Cog):
    """Commands for the bot's owner."""

    def __init__(self, bot):
        self.bot = bot

    # Prefix only: if the slash commands are out of date, this still works.
    @commands.command(hidden=True)
    @commands.is_owner()
    async def sync(self, ctx):
        """Registra os comandos de barra no Discord de novo"""
        try:
            await sync_commands(self.bot, force=True)
        except discord.HTTPException as e:
            return await ctx.send(f"❌ Não consegui sincronizar os comandos: {e}")
        await ctx.send(f"✅ {len(self.bot.tree.get_commands())} comandos de barra sincronizados.")


async def setup(bot):
    await bot.add_cog(Admin(bot))
