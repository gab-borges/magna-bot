import discord
from discord.ext import commands, tasks
import json

class MemberCount(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = self.load_config()
        self.update_channel.start()

    # > Config <
    def load_config(self):
        try:
            with open('membercount.json', 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def save_config(self):
        with open('membercount.json', 'w') as f:
            json.dump(self.config, f, indent=4)

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
            
            except Exception as e:
                print(f"Error on channel update: {e}")

    @update_channel.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

    # > Commands <
    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{__name__} is online!")

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