import discord
from discord.ext import commands, tasks
import calendar
import datetime
import json
import logging
import os
from zoneinfo import ZoneInfo

from magna.storage import data_file, load_json, save_json

logger = logging.getLogger(__name__)

TIMEZONE = ZoneInfo('America/Sao_Paulo')
MIDNIGHT = datetime.time(hour=0, minute=0, tzinfo=TIMEZONE)


def parse_birthday(text):
    """Return the date as zero-padded dd-mm, or raise ValueError."""
    # A leap year, so 29-02 is accepted.
    return datetime.datetime.strptime(f"{text}-2000", '%d-%m-%Y').strftime('%d-%m')


def is_birthday(birthday, day):
    """29-02 birthdays are celebrated on 28-02 in non-leap years."""
    today = day.strftime('%d-%m')
    if birthday == today:
        return True
    return birthday == '29-02' and today == '28-02' and not calendar.isleap(day.year)

class Birthday(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.birthdays = self.load_birthdays()
        self.channels = self.load_channels()
        self.check_birthdays.start()

    def cog_unload(self):
        self.check_birthdays.cancel()

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return True

    @staticmethod
    def load_saved(filename, env_var):
        """Load saved data. The env var only seeds it before anything is saved,
        so changes made with commands survive a restart."""
        path = data_file(filename)
        try:
            data = load_json(path, None)
            if data is None and os.getenv(env_var):
                data = json.loads(os.environ[env_var])
                save_json(path, data)
            return data or {}
        except Exception:
            logger.exception("Error loading %s", filename)
            return {}

    def load_birthdays(self):
        return self.load_saved('birthdays.json', 'BIRTHDAYS')

    def save_birthdays(self):
        save_json(data_file('birthdays.json'), self.birthdays)

    def load_channels(self):
        return self.load_saved('birthday_channels.json', 'BIRTHDAY_CHANNELS')

    def save_channels(self):
        save_json(data_file('birthday_channels.json'), self.channels)

    @tasks.loop(time=MIDNIGHT)  # Every day at midnight, São Paulo time
    async def check_birthdays(self):
        try:
            today = datetime.datetime.now(TIMEZONE).date()

            for user_id, birthday in self.birthdays.items():
                if is_birthday(birthday, today):
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
                                
        except Exception:
            logger.exception("Error in check_birthdays")

    @check_birthdays.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

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
            date = parse_birthday(date)

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
