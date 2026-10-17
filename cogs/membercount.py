import discord
from discord import app_commands
from discord.ext import commands, tasks
import logging

from magna.slash import defer
from magna.storage import data_file, load_json, save_json

logger = logging.getLogger(__name__)

class MemberCount(commands.Cog, name="Contador de membros"):
    def __init__(self, bot):
        self.bot = bot
        self.config = self.load_config()
        self.update_channel.start()

    def cog_unload(self):
        self.update_channel.cancel()

    async def cog_check(self, ctx):
        # Every command here works on a server's state or voice channel.
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return True

    # > Config <
    def load_config(self):
        return load_json(data_file('membercount.json'), {})

    def save_config(self):
        save_json(data_file('membercount.json'), self.config)

    # > Tasks <
    @tasks.loop(minutes=30)
    async def update_channel(self):
        # Copied: the listener below may remove a guild while this awaits.
        for guild_id, channel_id in list(self.config.items()):
            try:
                guild = self.bot.get_guild(int(guild_id))
                channel = guild and guild.get_channel(int(channel_id))

                if not channel:
                    continue
                
                # Format the channel name
                count = guild.member_count
                new_name = f"👥 Membros: {count}"
                
                # Check if the name needs to be updated
                if channel.name != new_name:
                    await channel.edit(name=new_name)
            
            except Exception:
                logger.exception("Error updating the member count channel of guild %s", guild_id)

    @update_channel.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        # The bot left or was removed; there's no channel to update anymore.
        if self.config.pop(str(guild.id), None) is not None:
            self.save_config()

    # > Commands <
    @commands.hybrid_command()
    @app_commands.describe(canal="Canal de voz cujo nome vai mostrar o número de membros")
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setmembercount(self, ctx, canal: discord.VoiceChannel):
        """Define o canal que vai mostrar o número de membros"""
        self.config[str(ctx.guild.id)] = str(canal.id)
        self.save_config()
        
        # Renaming a channel can wait on Discord's rate limit.
        await defer(ctx)

        # Immediate update
        count = ctx.guild.member_count
        await canal.edit(name=f"👥 Membros: {count}")
        
        await ctx.send(f"O canal {canal.mention} agora mostra o número de membros!")

    @commands.hybrid_command()
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def stopmembercount(self, ctx):
        """Para de atualizar o número de membros"""
        if str(ctx.guild.id) in self.config:
            del self.config[str(ctx.guild.id)]
            self.save_config()
            await ctx.send("Contador de membros desativado!")
        else:
            await ctx.send("Nenhum canal configurado neste servidor!")

async def setup(bot):
    await bot.add_cog(MemberCount(bot))
