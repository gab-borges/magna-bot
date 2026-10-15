import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from discord.ext import commands

from cogs import timeguessr
from cogs.timeguessr import GameMode, TimeGuessr, month_range, page_data, unflatten

ROUND = {'URL': 'https://img.example/1.jpg', 'Year': '2016', 'Country': 'Brazil'}


def photo(key, year='2000'):
    return {'ImageId': key, 'URL': f'https://img.example/{key}.jpg', 'Year': year,
            'Location': {'lat': 1, 'lng': 2}, 'Country': 'Brazil'}


def flatten(value):
    """Encode `value` the way SvelteKit's devalue does (the inverse of unflatten)."""
    values = []

    def add(item):
        index = len(values)
        values.append(None)
        if isinstance(item, list):
            values[index] = [add(x) for x in item]
        elif isinstance(item, dict):
            values[index] = {k: add(x) for k, x in item.items()}
        else:
            values[index] = item
        return index

    add(value)
    return values


def page(data):
    """A __data.json response: the layout node, then the page's."""
    return {'type': 'data', 'nodes': [{'type': 'skip'}, {'type': 'data', 'data': flatten(data)}]}


class FakeSession:
    """Answers GETs from `routes`: (url, params as a sorted tuple) -> JSON or exception."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def get(self, url, params=None):
        key = (url, tuple(sorted((params or {}).items())))
        self.requests.append(key)
        answer = self.routes[key]

        class Response:
            async def __aenter__(self):
                if isinstance(answer, Exception):
                    raise answer
                return self

            async def __aexit__(self, *exc):
                return False

            def raise_for_status(self):
                pass

            async def json(self, content_type=None):
                return answer() if callable(answer) else answer

        return Response()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TempDataDir(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.dict(os.environ, {'DATA_DIR': tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.data_dir = tmp.name


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


def make_ctx(*, user_id=1, manage_messages=False, guild_id=7):
    permissions = SimpleNamespace(manage_messages=manage_messages)
    return SimpleNamespace(
        author=SimpleNamespace(id=user_id),
        guild=SimpleNamespace(id=guild_id),
        channel=SimpleNamespace(id=42, permissions_for=Mock(return_value=permissions)),
        send=AsyncMock(),
    )


def sent_text(ctx):
    return "\n".join(str(call.args[0]) for call in ctx.send.await_args_list if call.args)


class AbandonedGameTests(TempDataDir):
    async def test_game_ends_when_nobody_continues_to_the_next_round(self):
        cog = make_cog()
        ctx = make_ctx()

        await asyncio.wait_for(TimeGuessr.timeguessr.callback(cog, ctx), timeout=2)

        self.assertNotIn(42, cog.active_games)
        self.assertEqual(cog.play_round.await_count, 1)
        self.assertIn("inatividade", sent_text(ctx))
        cog.show_final_scoreboard.assert_awaited_once()


class TimeLimitTests(TempDataDir):
    async def test_rejects_round_times_outside_the_allowed_range(self):
        for tempo in (0, 5, 10_000):
            with self.subTest(tempo=tempo):
                cog = make_cog()
                ctx = make_ctx()

                await asyncio.wait_for(TimeGuessr.timeguessr.callback(cog, ctx, tempo=tempo), timeout=1)

                cog.fetch_rounds.assert_not_awaited()
                self.assertNotIn(42, cog.active_games)
                self.assertIn("segundos", sent_text(ctx))


class StopCommandTests(TempDataDir):
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


class ModeTests(TempDataDir):
    async def test_random_is_the_default_and_daily_is_a_choice(self):
        for modo, expected in ((None, 'aleatorio'), ('diario', 'diario')):
            with self.subTest(modo=modo):
                cog = make_cog()
                await asyncio.wait_for(TimeGuessr.timeguessr.callback(cog, make_ctx(), modo), timeout=2)
                cog.fetch_rounds.assert_awaited_once_with(expected, '7')

    async def test_mode_names_ignore_accents_and_case(self):
        for text in ('diario', 'Diário', 'DIARIO', 'aleatório'):
            with self.subTest(text=text):
                self.assertIn(await GameMode().convert(None, text), GameMode.choices)
        with self.assertRaises(commands.BadArgument):
            await GameMode().convert(None, '45')

    async def test_no_new_photos_left(self):
        cog = make_cog()
        cog.fetch_rounds = AsyncMock(return_value=[])
        ctx = make_ctx()

        await asyncio.wait_for(TimeGuessr.timeguessr.callback(cog, ctx), timeout=2)

        cog.play_round.assert_not_awaited()
        self.assertIn("fotos novas", sent_text(ctx))


class SeenPhotosTests(TempDataDir):
    async def test_shown_photos_are_saved_per_server(self):
        cog = make_cog()
        cog.fetch_rounds = AsyncMock(return_value=[photo('a'), photo('b')])

        await asyncio.wait_for(TimeGuessr.timeguessr.callback(cog, make_ctx(guild_id=7)), timeout=2)

        # Nobody continued after round 1, so only its photo was shown.
        self.assertEqual(TimeGuessr(bot=Mock()).seen, {'7': {'a'}})
        with open(os.path.join(self.data_dir, 'timeguessr_seen.json')) as f:
            self.assertEqual(json.load(f), {'7': ['a']})

    async def test_a_broken_file_starts_over(self):
        with open(os.path.join(self.data_dir, 'timeguessr_seen.json'), 'w') as f:
            f.write('{not json')

        with self.assertLogs('cogs.timeguessr', 'ERROR'):
            self.assertEqual(TimeGuessr(bot=Mock()).seen, {})

    async def test_dm_games_are_tracked_by_channel(self):
        ctx = make_ctx()
        ctx.guild = None
        self.assertEqual(TimeGuessr.scope_of(ctx), 'channel-42')


class DevalueTests(unittest.TestCase):
    def test_round_trip(self):
        data = {'playArray': [photo('a'), photo('b'), 'token'], 'isPro': None}
        self.assertEqual(unflatten(flatten(data)), data)

    def test_negative_indices_are_none(self):
        self.assertEqual(unflatten([{'isPro': -1, 'x': 1}, 3]), {'isPro': None, 'x': 3})

    def test_page_data_skips_other_nodes(self):
        doc = {'nodes': [{'type': 'data', 'data': flatten({'user': None})}, page({'archive': 1})['nodes'][1]]}
        self.assertEqual(page_data(doc, 'archive'), {'archive': 1})
        self.assertIsNone(page_data(doc, 'playArray'))

    def test_month_range(self):
        self.assertEqual(month_range('2025-11', '2026-02'), ['2025-11', '2025-12', '2026-01', '2026-02'])
        self.assertEqual(month_range('2026-10', '2026-10'), ['2026-10'])


ARCHIVE = {'archive': {
    'month': '2026-10', 'currentMonth': '2026-10', 'minMonth': '2026-10',
    'days': [
        {'dailyNumber': 1219, 'isToday': False, 'link': '/play?mode=daily&archive=1219-abc'},
        {'dailyNumber': 1232, 'isToday': True, 'link': '/play?mode=daily&archive=1232-def'},
    ],
}}


class RandomRoundsTests(TempDataDir):
    def routes(self, free_play, archive_day):
        return {
            (timeguessr.RANDOM_URL, ()): free_play,
            (timeguessr.ARCHIVE_URL, ()): page(ARCHIVE),
            (timeguessr.DAY_URL, (('archive', '1219-abc'), ('mode', 'daily'))): archive_day,
        }

    async def fetch(self, cog, session, scope='7'):
        with patch('cogs.timeguessr.aiohttp.ClientSession', return_value=session):
            return await cog.fetch_rounds('aleatorio', scope)

    async def test_mixes_free_play_and_past_dailies_without_repeats(self):
        cog = TimeGuessr(bot=Mock())
        cog.seen = {'7': {'f1', 'd1'}}
        free = page({'playArray': [photo(f'f{i}') for i in range(1, 4)] + ['token']})
        day = page({'playArray': [photo(f'd{i}') for i in range(1, 4)]})
        session = FakeSession(self.routes(free, day))

        rounds = await self.fetch(cog, session)

        self.assertEqual(sorted(r['ImageId'] for r in rounds), ['d2', 'd3', 'f2', 'f3'])
        # Too few new photos after one try, so it kept looking, never on today's daily.
        self.assertEqual(session.requests.count((timeguessr.RANDOM_URL, ())), timeguessr.MAX_FETCHES)
        self.assertNotIn('1232-def', str(session.requests))

    async def test_stops_once_it_has_a_game(self):
        cog = TimeGuessr(bot=Mock())
        free = page({'playArray': [photo(f'f{i}') for i in range(5)]})
        day = page({'playArray': [photo(f'd{i}') for i in range(5)]})
        session = FakeSession(self.routes(free, day))

        rounds = await self.fetch(cog, session)

        self.assertEqual(len(rounds), timeguessr.ROUNDS_PER_GAME)
        self.assertEqual(session.requests.count((timeguessr.RANDOM_URL, ())), 1)

    async def test_one_broken_source_is_skipped(self):
        cog = TimeGuessr(bot=Mock())
        free = page({'playArray': [photo(f'f{i}') for i in range(5)]})
        session = FakeSession(self.routes(free, ValueError('site changed')))

        with self.assertLogs('cogs.timeguessr', 'WARNING'):
            rounds = await self.fetch(cog, session)

        self.assertEqual(len(rounds), 5)

    async def test_none_when_every_source_fails(self):
        cog = TimeGuessr(bot=Mock())
        session = FakeSession(self.routes(ValueError('down'), ValueError('down')))
        session.routes[(timeguessr.ARCHIVE_URL, ())] = ValueError('down')

        with self.assertLogs('cogs.timeguessr', 'WARNING'):
            self.assertIsNone(await self.fetch(cog, session))

    async def test_empty_when_everything_was_seen(self):
        cog = TimeGuessr(bot=Mock())
        cog.seen = {'7': {'f0', 'd0'}}
        session = FakeSession(self.routes(page({'playArray': [photo('f0')]}), page({'playArray': [photo('d0')]})))

        self.assertEqual(await self.fetch(cog, session), [])
        # Another server hasn't seen them.
        self.assertEqual(len(await self.fetch(cog, session, scope='8')), 2)

    async def test_daily_keeps_todays_rounds_as_they_are(self):
        cog = TimeGuessr(bot=Mock())
        cog.seen = {'7': {'a'}}
        session = FakeSession({(timeguessr.DAILY_URL, ()): [photo('a'), photo('b'), 0]})

        with patch('cogs.timeguessr.aiohttp.ClientSession', return_value=session):
            rounds = await cog.fetch_rounds('diario', '7')

        self.assertEqual([r['ImageId'] for r in rounds], ['a', 'b'])

    async def test_an_empty_daily_is_a_failure(self):
        session = FakeSession({(timeguessr.DAILY_URL, ()): [0]})

        with patch('cogs.timeguessr.aiohttp.ClientSession', return_value=session):
            self.assertIsNone(await TimeGuessr(bot=Mock()).fetch_rounds('diario', '7'))

    async def test_past_months_are_fetched_once(self):
        cog = TimeGuessr(bot=Mock())
        archive = json.loads(json.dumps(ARCHIVE))
        archive['archive']['minMonth'] = '2026-09'
        september = {'archive': dict(archive['archive'], month='2026-09')}
        day = page({'playArray': [photo('d0')]})
        session = FakeSession({
            (timeguessr.ARCHIVE_URL, ()): page(archive),
            (timeguessr.ARCHIVE_URL, (('month', '2026-09'),)): page(september),
            (timeguessr.DAY_URL, (('archive', '1219-abc'), ('mode', 'daily'))): day,
        })

        with patch('cogs.timeguessr.random.choice', side_effect=lambda options: options[0]):
            for _ in range(2):
                self.assertEqual(await cog.fetch_archive_day(session), [photo('d0')])

        self.assertEqual(session.requests.count((timeguessr.ARCHIVE_URL, (('month', '2026-09'),))), 1)


if __name__ == "__main__":
    unittest.main()
