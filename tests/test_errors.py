import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from discord.ext import commands

from cogs.errors import ErrorHandler
from cogs.gemini import Gemini
from cogs.music import Music
from cogs.reddit import Reddit


def make_ctx(command=Music.play):
    return SimpleNamespace(command=command, cog=None, clean_prefix='%', send=AsyncMock())


class ErrorHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.handler = ErrorHandler(bot=None)

    async def reply_to(self, error, ctx=None):
        ctx = ctx or make_ctx()
        await ErrorHandler.on_command_error(self.handler, ctx, error)
        return ctx

    async def test_unknown_commands_are_ignored(self):
        ctx = await self.reply_to(commands.CommandNotFound('Command "nope" is not found'))

        ctx.send.assert_not_awaited()

    async def test_cooldown_says_when_to_retry(self):
        cooldown = commands.Cooldown(1, 15)
        ctx = await self.reply_to(
            commands.CommandOnCooldown(cooldown, 4.2, commands.BucketType.user)
        )

        ctx.send.assert_awaited_once_with("⏳ Calma! Tente de novo em 5s.")

    async def test_missing_argument_shows_usage(self):
        param = Music.play.clean_params['query']
        ctx = await self.reply_to(commands.MissingRequiredArgument(param))

        message = ctx.send.await_args.args[0]
        self.assertIn("`query`", message)
        self.assertIn("`%play <query>`", message)

    async def test_bad_argument_shows_usage(self):
        ctx = await self.reply_to(commands.MemberNotFound("someone"))

        self.assertIn("Argumento inválido", ctx.send.await_args.args[0])

    async def test_missing_permissions_names_them(self):
        ctx = await self.reply_to(commands.MissingPermissions(['administrator']))

        self.assertIn("`Administrator`", ctx.send.await_args.args[0])

    async def test_unexpected_errors_are_logged_with_a_generic_reply(self):
        error = commands.CommandInvokeError(RuntimeError("boom"))

        with self.assertLogs('cogs.errors', level='ERROR') as logs:
            ctx = await self.reply_to(error)

        self.assertIn("Algo deu errado", ctx.send.await_args.args[0])
        self.assertIn("RuntimeError: boom", "\n".join(logs.output))

    async def test_commands_with_their_own_handler_are_left_alone(self):
        async def custom(ctx):
            pass

        async def on_error(ctx, error):
            pass

        command = commands.Command(custom)
        command.error(on_error)

        ctx = await self.reply_to(commands.CheckFailure(), make_ctx(command))

        ctx.send.assert_not_awaited()


class CooldownTests(unittest.TestCase):
    def test_commands_that_call_external_services_have_a_cooldown(self):
        for command, per in ((Gemini.ask, 15), (Reddit.meme, 10), (Music.play, 3)):
            with self.subTest(command=command.name):
                self.assertEqual(command.cooldown.rate, 1)
                self.assertEqual(command.cooldown.per, per)


if __name__ == "__main__":
    unittest.main()
