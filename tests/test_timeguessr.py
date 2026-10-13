import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from cogs.timeguessr import TimeGuessr

ROUND = {'URL': 'https://img.example/1.jpg', 'Year': '2016', 'Country': 'Brazil'}


async def reactions_never_arrive(event, check=None, timeout=None):
    """Behave like bot.wait_for when nobody reacts."""
    if timeout is None:
        await asyncio.Future()  # would wait forever
    await asyncio.sleep(timeout)
    raise asyncio.TimeoutError


def make_cog():
    bot = SimpleNamespace(wait_for=AsyncMock(side_effect=reactions_never_arrive), get_user=Mock())
    cog = TimeGuessr(bot)
    cog.intro_delay = 0
    cog.reaction_timeout = 0.01
    cog.fetch_rounds = AsyncMock(return_value=[ROUND, ROUND])
    cog.play_round = AsyncMock(return_value={})
    cog.score_round = AsyncMock(return_value={})
    cog.show_answer = AsyncMock(return_value=SimpleNamespace(id=99, add_reaction=AsyncMock()))
    cog.show_final_scoreboard = AsyncMock()
    return cog


def make_ctx(*, user_id=1, manage_messages=False):
    permissions = SimpleNamespace(manage_messages=manage_messages)
    return SimpleNamespace(
        author=SimpleNamespace(id=user_id),
        channel=SimpleNamespace(id=42, permissions_for=Mock(return_value=permissions)),
        send=AsyncMock(),
    )


def sent_text(ctx):
    return "\n".join(str(call.args[0]) for call in ctx.send.await_args_list if call.args)


class AbandonedGameTests(unittest.IsolatedAsyncioTestCase):
    async def test_game_ends_when_nobody_continues_to_the_next_round(self):
        cog = make_cog()
        ctx = make_ctx()

        await asyncio.wait_for(TimeGuessr.timeguessr.callback(cog, ctx), timeout=2)

        self.assertNotIn(42, cog.active_games)
        self.assertEqual(cog.play_round.await_count, 1)
        self.assertIn("inatividade", sent_text(ctx))
        cog.show_final_scoreboard.assert_awaited_once()


class TimeLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_round_times_outside_the_allowed_range(self):
        for tempo in (0, 5, 10_000):
            with self.subTest(tempo=tempo):
                cog = make_cog()
                ctx = make_ctx()

                await asyncio.wait_for(TimeGuessr.timeguessr.callback(cog, ctx, tempo), timeout=1)

                cog.fetch_rounds.assert_not_awaited()
                self.assertNotIn(42, cog.active_games)
                self.assertIn("segundos", sent_text(ctx))


class StopCommandTests(unittest.IsolatedAsyncioTestCase):
    async def start_game(self, cog):
        cog.reaction_timeout = 60
        game = asyncio.create_task(TimeGuessr.timeguessr.callback(cog, make_ctx(user_id=1)))
        async def until_started():
            while 42 not in cog.active_games:
                await asyncio.sleep(0)

        await asyncio.wait_for(until_started(), timeout=1)
        return game

    async def test_starter_can_stop_the_game(self):
        cog = make_cog()
        game = await self.start_game(cog)
        ctx = make_ctx(user_id=1)

        await TimeGuessr.timeguessr_stop.callback(cog, ctx)
        await asyncio.wait([game], timeout=1)

        self.assertTrue(game.done())
        self.assertNotIn(42, cog.active_games)
        self.assertIn("encerrad", sent_text(ctx))

    async def test_moderator_can_stop_someone_elses_game(self):
        cog = make_cog()
        game = await self.start_game(cog)

        await TimeGuessr.timeguessr_stop.callback(cog, make_ctx(user_id=2, manage_messages=True))
        await asyncio.wait([game], timeout=1)

        self.assertTrue(game.done())

    async def test_other_players_cannot_stop_the_game(self):
        cog = make_cog()
        game = await self.start_game(cog)
        ctx = make_ctx(user_id=2)

        try:
            await TimeGuessr.timeguessr_stop.callback(cog, ctx)
            await asyncio.sleep(0)

            self.assertFalse(game.done())
            self.assertIn(42, cog.active_games)
            self.assertIn("Só quem começou", sent_text(ctx))
        finally:
            game.cancel()
            await asyncio.wait([game], timeout=1)

    async def test_stop_without_a_game(self):
        cog = make_cog()
        ctx = make_ctx()

        await TimeGuessr.timeguessr_stop.callback(cog, ctx)

        self.assertIn("Nenhum jogo", sent_text(ctx))


if __name__ == "__main__":
    unittest.main()
