import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, time, timedelta
import pytz
import asyncio
import os

class Birthday(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Load data from environment variables instead of files
        self.birthdays = self.load_birthdays()
        self.channels = self.load_channels()
        self.check_birthdays.start()

    def load_birthdays(self):
        try:
            # Load from environment variable
            birthdays_str = os.getenv('BIRTHDAYS', '{}')
            return json.loads(birthdays_str)
        except Exception as e:
            print(f"Error loading birthdays: {e}")
            return {}

    def save_birthdays(self):
        # Convert dict to JSON string
        birthdays_str = json.dumps(self.birthdays)
        # In local development, you might want to save to a file
        if os.getenv('DEVELOPMENT'):
            with open('birthdays.json', 'w') as f:
                json.dump(self.birthdays, f, indent=4)

    def load_channels(self):
        try:
            # Load from environment variable
            channels_str = os.getenv('BIRTHDAY_CHANNELS', '{}')
            return json.loads(channels_str)
        except Exception as e:
            print(f"Error loading channels: {e}")
            return {}

    def save_channels(self):
        with open('birthday_channels.json', 'w') as f:
            json.dump(self.channels, f, indent=4)

    @tasks.loop(hours=24)  # Check once per day
    async def check_birthdays(self):
        try:
            current_time = datetime.now(pytz.timezone('America/Sao_Paulo'))
            today = current_time.strftime('%d-%m')
            
            for user_id, birthday in self.birthdays.items():
                if birthday == today:
                    for guild in self.bot.guilds:
                        member = guild.get_member(int(user_id))
                        if member:
                            embed = discord.Embed(
                                title="🎉 Feliz Aniversário! 🎂",
                                description=f"Hoje é aniversário de {member.mention}!\nParabéns! 🎈🎊",
                                color=discord.Color.gold()
                            )
                            channel_id = self.channels.get(str(guild.id))
                            channel = guild.get_channel(int(channel_id)) if channel_id else guild.system_channel
                            if channel:
                                await channel.send(embed=embed)
                                
        except Exception as e:
            print(f"[ERROR] Error in check_birthdays: {str(e)}")

    @check_birthdays.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()
        # Wait until midnight
        current_time = datetime.now(pytz.timezone('America/Sao_Paulo'))
        next_run = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        if current_time >= next_run:
            next_run = next_run + timedelta(days=1)
        await asyncio.sleep((next_run - current_time).total_seconds())

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{__name__} is online!")

    @commands.group(invoke_without_command=True)
    async def birthday(self, ctx):
        """Birthday commands"""
        await ctx.send("Use `%birthday add <dd-mm>` to add your birthday\n"
                      "Use `%birthday remove` to remove your birthday\n"
                      "Use `%birthday list` to see all birthdays")

    # > Commands <  
    @commands.has_permissions(administrator=True)
    @birthday.command(name="add")
    async def add_birthday(self, ctx, member: discord.Member, date: str):
        """Add a member's birthday (format: dd-mm)"""
        try:
            # Validate date format
            datetime.strptime(date, '%d-%m')
            
            self.birthdays[str(member.id)] = date
            self.save_birthdays()
            
            await ctx.send(f"Birthday for {member.mention} set to {date}! 🎂")
        except ValueError:
            await ctx.send("Invalid date format! Use dd-mm (example: 25-12)")

    @commands.has_permissions(administrator=True)
    @birthday.command(name="remove")
    async def remove_birthday(self, ctx, member: discord.Member):
        """Remove a member's birthday"""
        if str(member.id) in self.birthdays:
            del self.birthdays[str(member.id)]
            self.save_birthdays()
            await ctx.send(f"{member.mention}'s birthday has been removed!")
        else:
            await ctx.send(f"{member.mention} doesn't have a birthday set!")

    @birthday.command(name="list")
    async def list_birthdays(self, ctx):
        """List all birthdays"""
        if not self.birthdays:
            await ctx.send("No birthdays set!")
            return

        embed = discord.Embed(title="🎂 Birthday List", color=discord.Color.blue())
        
        for user_id, date in self.birthdays.items():
            member = ctx.guild.get_member(int(user_id))
            if member:
                embed.add_field(name=member.display_name, value=date, inline=True)

        await ctx.send(embed=embed)

    @commands.has_permissions(administrator=True)
    @birthday.command(name="setchannel")
    async def set_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the birthday announcement channel"""
        channel = channel or ctx.channel
        self.channels[str(ctx.guild.id)] = str(channel.id)
        self.save_channels()
        await ctx.send(f"Birthday announcements will now be sent in {channel.mention}! 🎉")

    @commands.has_permissions(administrator=True)
    @birthday.command(name="removechannel")
    async def remove_channel(self, ctx):
        """Remove the birthday announcement channel (will use system channel instead)"""
        if str(ctx.guild.id) in self.channels:
            del self.channels[str(ctx.guild.id)]
            self.save_channels()
            await ctx.send("Birthday channel removed! Will use system channel instead.")
        else:
            await ctx.send("No birthday channel was set!")

async def setup(bot):
    await bot.add_cog(Birthday(bot))
