import discord
from discord import app_commands
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

def days_until(birthday, today):
    """Days from `today` to the next time `birthday` is celebrated (0 = today)."""
    for offset in range(367):
        if is_birthday(birthday, today + datetime.timedelta(days=offset)):
            return offset
    raise ValueError(f"Not a birthday: {birthday}")


# Discord rejects embeds with more fields than this.
EMBED_FIELD_LIMIT = 25

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

    def load_last_announced(self):
        return load_json(data_file('birthday_state.json'), {}).get('last_announced')

    def save_last_announced(self, day):
        save_json(data_file('birthday_state.json'), {'last_announced': day.isoformat()})

    @tasks.loop(time=MIDNIGHT)  # Every day at midnight, São Paulo time
    async def check_birthdays(self):
        await self.announce_today()

    @check_birthdays.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()
        # Catch up when the bot was down (or restarting) at midnight.
        await self.announce_today()

    async def announce_today(self):
        """Announce today's birthdays, unless that was already done today."""
        # An exception here would stop the daily loop for good.
        try:
            today = datetime.datetime.now(TIMEZONE).date()
            if self.load_last_announced() == today.isoformat():
                return
            await self.announce_birthdays(today)
            self.save_last_announced(today)
        except Exception:
            logger.exception("Error announcing birthdays")

    async def announce_birthdays(self, today):
        for user_id, birthday in self.birthdays.items():
            if not is_birthday(birthday, today):
                continue
            for guild in self.bot.guilds:
                member = guild.get_member(int(user_id))
                if not member:
                    continue
                # One failing channel (missing permission, deleted channel)
                # must not stop the other announcements.
                try:
                    await self.announce(guild, member)
                except Exception:
                    logger.exception(
                        "Could not announce the birthday of %s in guild %s", user_id, guild.id
                    )

    async def announce(self, guild, member):
        embed = discord.Embed(
            title="🎉 Feliz Aniversário! 🎂",
            description=f"Hoje é aniversário de {member.mention}!\nParabéns! 🎈🎊",
            color=discord.Color.gold()
        )
        channel_id = self.channels.get(str(guild.id))
        channel = guild.get_channel(int(channel_id)) if channel_id else guild.system_channel
        if channel:
            await channel.send(embed=embed)

    # Slash command groups can't run on their own, so this is /birthday help.
    @commands.hybrid_group(invoke_without_command=True, fallback="help")
    @commands.guild_only()
    async def birthday(self, ctx):
        """Birthday commands"""
        prefix = ctx.clean_prefix
        await ctx.send(f"`{prefix}birthday list` shows everyone's birthday\n"
                       "Admins only:\n"
                       f"`{prefix}birthday add <member> <dd-mm>` sets a member's birthday\n"
                       f"`{prefix}birthday remove <member>` removes it\n"
                       f"`{prefix}birthday setchannel [channel]` picks where birthdays are announced\n"
                       f"`{prefix}birthday removechannel` goes back to the server's system channel")

    # > Commands <  
    @commands.has_permissions(administrator=True)
    @birthday.command(name="add")
    @app_commands.describe(member="Whose birthday it is", date="Day and month, like 25-12")
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
    @app_commands.describe(member="Whose birthday to remove")
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

        # Upcoming birthdays first, starting with today's.
        today = datetime.datetime.now(TIMEZONE).date()
        upcoming = sorted(self.birthdays.items(), key=lambda item: days_until(item[1], today))
        members = [
            (member, date) for user_id, date in upcoming
            if (member := ctx.guild.get_member(int(user_id)))
        ]
        for member, date in members[:EMBED_FIELD_LIMIT]:
            embed.add_field(name=member.display_name, value=date, inline=True)
        if len(members) > EMBED_FIELD_LIMIT:
            embed.set_footer(text=f"…and {len(members) - EMBED_FIELD_LIMIT} more")

        await ctx.send(embed=embed)

    @commands.has_permissions(administrator=True)
    @birthday.command(name="setchannel")
    @app_commands.describe(channel="Where to announce birthdays (default: this channel)")
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
