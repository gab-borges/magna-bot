import logging
import math

from discord.ext import commands

logger = logging.getLogger(__name__)


def usage(ctx):
    command = ctx.command
    text = f"{ctx.clean_prefix}{command.qualified_name} {command.signature}".strip()
    return f"`{text}`"


def permission_names(permissions):
    return ", ".join(f"`{name.replace('_', ' ').title()}`" for name in permissions)


class ErrorHandler(commands.Cog):
    """Tells users why a command didn't run, and logs unexpected errors."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        # Commands and cogs with their own error handler deal with it there.
        if ctx.command and ctx.command.has_error_handler():
            return
        if ctx.cog and ctx.cog.has_error_handler():
            return

        message = self.describe(ctx, error)
        if message:
            await ctx.send(message)

    def describe(self, ctx, error):
        """Return the message to show for `error`, or None to stay silent."""
        if isinstance(error, commands.CommandNotFound):
            return None
        if isinstance(error, commands.CommandOnCooldown):
            return f"⏳ Calma! Tente de novo em {math.ceil(error.retry_after)}s."
        if isinstance(error, commands.MissingRequiredArgument):
            return f"⚠️ Faltou o argumento `{error.param.name}`. Uso: {usage(ctx)}"
        if isinstance(error, commands.BadArgument):
            return f"⚠️ Argumento inválido. Uso: {usage(ctx)}"
        if isinstance(error, commands.MissingPermissions):
            return f"⛔ Você precisa da permissão {permission_names(error.missing_permissions)}."
        if isinstance(error, commands.BotMissingPermissions):
            return f"⚠️ Eu preciso da permissão {permission_names(error.missing_permissions)}."
        if isinstance(error, commands.NoPrivateMessage):
            return "⚠️ Esse comando só funciona em servidores."
        if isinstance(error, commands.CheckFailure):
            return "⛔ Você não pode usar esse comando aqui."
        if isinstance(error, commands.UserInputError):
            return f"⚠️ Não entendi o comando. Uso: {usage(ctx)}"

        original = getattr(error, 'original', error)
        logger.error(
            "Unhandled error in command %s", ctx.command,
            exc_info=(type(original), original, original.__traceback__),
        )
        return "❌ Algo deu errado ao executar esse comando."


async def setup(bot):
    await bot.add_cog(ErrorHandler(bot))
