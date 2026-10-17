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

class Birthday(commands.Cog, name="Aniversários"):
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
        """Comandos de aniversário"""
        prefix = ctx.clean_prefix
        await ctx.send(f"`{prefix}birthday list` mostra o aniversário de todo mundo\n"
                       "Só para administradores:\n"
                       f"`{prefix}birthday add <membro> <dd-mm>` define o aniversário de um membro\n"
                       f"`{prefix}birthday remove <membro>` remove o aniversário\n"
                       f"`{prefix}birthday setchannel [canal]` escolhe onde os aniversários são anunciados\n"
                       f"`{prefix}birthday removechannel` volta a usar o canal de sistema do servidor")

    # > Commands <  
    @commands.has_permissions(administrator=True)
    @birthday.command(name="add")
    @app_commands.describe(membro="De quem é o aniversário", data="Dia e mês, como 25-12")
    async def add_birthday(self, ctx, membro: discord.Member, data: str):
        """Adiciona o aniversário de um membro (formato: dd-mm)"""
        try:
            data = parse_birthday(data)

            self.birthdays[str(membro.id)] = data
            self.save_birthdays()
            
            await ctx.send(f"Aniversário de {membro.mention} definido para {data}! 🎂")
        except ValueError:
            await ctx.send("Data inválida! Use dd-mm (exemplo: 25-12)")

    @commands.has_permissions(administrator=True)
    @birthday.command(name="remove")
    @app_commands.describe(membro="De quem remover o aniversário")
    async def remove_birthday(self, ctx, membro: discord.Member):
        """Remove o aniversário de um membro"""
        if str(membro.id) in self.birthdays:
            del self.birthdays[str(membro.id)]
            self.save_birthdays()
            await ctx.send(f"O aniversário de {membro.mention} foi removido!")
        else:
            await ctx.send(f"{membro.mention} não tem aniversário cadastrado!")

    @birthday.command(name="list")
    async def list_birthdays(self, ctx):
        """Lista todos os aniversários"""
        if not self.birthdays:
            await ctx.send("Nenhum aniversário cadastrado!")
            return

        embed = discord.Embed(title="🎂 Aniversários", color=discord.Color.blue())

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
            embed.set_footer(text=f"…e mais {len(members) - EMBED_FIELD_LIMIT}")

        await ctx.send(embed=embed)

    @commands.has_permissions(administrator=True)
    @birthday.command(name="setchannel")
    @app_commands.describe(canal="Onde anunciar os aniversários (padrão: este canal)")
    async def set_channel(self, ctx, canal: discord.TextChannel = None):
        """Define o canal dos anúncios de aniversário"""
        canal = canal or ctx.channel
        self.channels[str(ctx.guild.id)] = str(canal.id)
        self.save_channels()
        await ctx.send(f"Os aniversários agora serão anunciados em {canal.mention}! 🎉")

    @commands.has_permissions(administrator=True)
    @birthday.command(name="removechannel")
    async def remove_channel(self, ctx):
        """Remove o canal de aniversários (volta a usar o canal de sistema)"""
        if str(ctx.guild.id) in self.channels:
            del self.channels[str(ctx.guild.id)]
            self.save_channels()
            await ctx.send("Canal de aniversários removido! Vou usar o canal de sistema do servidor.")
        else:
            await ctx.send("Nenhum canal de aniversários estava definido!")

async def setup(bot):
    await bot.add_cog(Birthday(bot))
