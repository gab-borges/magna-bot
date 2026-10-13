import discord
from discord.ext import commands, tasks
import logging

from magna.storage import data_file, load_json, save_json

logger = logging.getLogger(__name__)

class MemberCount(commands.Cog):
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
        for guild_id, channel_id in self.config.items():
            try:
                guild = self.bot.get_guild(int(guild_id))
                channel = guild.get_channel(int(channel_id))
                
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

    # > Commands <
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setmembercount(self, ctx, channel: discord.VoiceChannel):
        """Define the channel that will show the member count"""
        self.config[str(ctx.guild.id)] = str(channel.id)
        self.save_config()
        
        # Immediate update
        count = ctx.guild.member_count
        await channel.edit(name=f"👥 Membros: {count}")
        
        await ctx.send(f"Channel {channel.mention} configured to show the member count!")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def stopmembercount(self, ctx):
        """Stop updating the member count"""
        if str(ctx.guild.id) in self.config:
            del self.config[str(ctx.guild.id)]
            self.save_config()
            await ctx.send("Member count disabled!")
        else:
            await ctx.send("No channel configured in this server!")

async def setup(bot):
    await bot.add_cog(MemberCount(bot))
