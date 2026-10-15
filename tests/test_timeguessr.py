import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from discord.ext import commands

from cogs import timeguessr
from cogs.timeguessr import (
    GameMode, Guess, GuessModal, GuessRound, Result, TimeGuessr, fit_lines, month_range,
    page_data, street_view_link, unflatten,
)
from magna import countries

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
    cog.score_round = Mock(return_value={})
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


class RoundCountTests(TempDataDir):
    async def test_rejects_round_counts_outside_the_allowed_range(self):
        for rodadas in (0, -1, 16):
            with self.subTest(rodadas=rodadas):
                cog = make_cog()
                ctx = make_ctx()

                await asyncio.wait_for(TimeGuessr.timeguessr.callback(cog, ctx, rodadas=rodadas), timeout=1)

                cog.fetch_rounds.assert_not_awaited()
                self.assertNotIn(42, cog.active_games)
                self.assertIn("rodadas", sent_text(ctx))

    async def test_asks_for_and_plays_that_many_rounds(self):
        cog = make_cog()
        cog.fetch_rounds = AsyncMock(return_value=[photo(str(i)) for i in range(10)])
        cog.bot.wait_for = AsyncMock()  # someone always continues
        ctx = make_ctx()

        await asyncio.wait_for(TimeGuessr.timeguessr.callback(cog, ctx, rodadas=10), timeout=2)

        cog.fetch_rounds.assert_awaited_once_with('aleatorio', '7', 10)
        self.assertEqual(cog.play_round.await_count, 10)
        intro = ctx.send.await_args_list[0].kwargs["embed"].description
        self.assertIn("**10 rodadas**", intro)
        self.assertNotIn("ℹ️", intro)

    async def test_says_when_there_are_fewer_photos_than_asked(self):
        for modo, expected in (('aleatorio', "Só achei 3 fotos novas"), ('diario', "diário tem só 3 fotos")):
            with self.subTest(modo=modo):
                cog = make_cog()
                cog.fetch_rounds = AsyncMock(return_value=[photo(str(i)) for i in range(3)])
                ctx = make_ctx()

                await asyncio.wait_for(TimeGuessr.timeguessr.callback(cog, ctx, modo, rodadas=8), timeout=2)

                self.assertIn(expected, ctx.send.await_args_list[0].kwargs["embed"].description)

    async def test_one_round(self):
        cog = make_cog()
        cog.fetch_rounds = AsyncMock(return_value=[ROUND])
        ctx = make_ctx()

        await asyncio.wait_for(TimeGuessr.timeguessr.callback(cog, ctx, rodadas=1), timeout=2)

        self.assertIn("**1 rodada**", ctx.send.await_args_list[0].kwargs["embed"].description)


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
                cog.fetch_rounds.assert_awaited_once_with(expected, '7', 5)

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

    async def fetch(self, cog, session, scope='7', count=5):
        with patch('cogs.timeguessr.aiohttp.ClientSession', return_value=session):
            return await cog.fetch_rounds('aleatorio', scope, count)

    async def test_longer_games_fetch_more_photos(self):
        cog = TimeGuessr(bot=Mock())
        batches = iter(range(100))

        def new_batch(prefix):
            return lambda: page({'playArray': [photo(f'{prefix}{next(batches)}') for _ in range(5)]})

        session = FakeSession(self.routes(new_batch('f'), new_batch('d')))

        rounds = await self.fetch(cog, session, count=15)

        self.assertEqual(len({r['ImageId'] for r in rounds}), 15)
        # Two passes of both sources were enough.
        self.assertEqual(session.requests.count((timeguessr.RANDOM_URL, ())), 2)

    async def test_longer_games_get_more_passes_before_settling(self):
        cog = TimeGuessr(bot=Mock())
        session = FakeSession(self.routes(page({'playArray': [photo('f')]}), page({'playArray': [photo('d')]})))

        self.assertEqual(len(await self.fetch(cog, session, count=15)), 2)
        self.assertEqual(session.requests.count((timeguessr.RANDOM_URL, ())), timeguessr.MAX_FETCHES + 2)

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

    async def test_fewer_rounds_cut_the_daily(self):
        session = FakeSession({(timeguessr.DAILY_URL, ()): [photo('a'), photo('b'), photo('c'), 0]})

        with patch('cogs.timeguessr.aiohttp.ClientSession', return_value=session):
            rounds = await TimeGuessr(bot=Mock()).fetch_rounds('diario', '7', 2)

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


class CountryTests(unittest.TestCase):
    def assertFinds(self, text, code):
        found = countries.find(text)
        self.assertIsNotNone(found, text)
        self.assertEqual(found.code, code, text)

    def test_portuguese_english_and_codes(self):
        for text, code in [
            ("Brasil", "BRA"), ("Brazil", "BRA"), ("BR", "BRA"),
            ("EUA", "USA"), ("Estados Unidos", "USA"), ("USA", "USA"), ("US", "USA"),
            ("Alemanha", "DEU"), ("Japão", "JPN"), ("Holanda", "NLD"), ("Países Baixos", "NLD"),
            ("Inglaterra", "GBR"), ("UK", "GBR"), ("Escócia", "GBR"), ("UAE", "ARE"),
            ("Turkey", "TUR"), ("Irã", "IRN"), ("Irão", "IRN"), ("URSS", "RUS"),
        ]:
            with self.subTest(text=text):
                self.assertFinds(text, code)

    def test_ignores_accents_case_and_spaces(self):
        for text in ("japao", "JAPÃO", "  Japão  "):
            with self.subTest(text=text):
                self.assertFinds(text, "JPN")

    def test_exact_names_win_over_close_ones(self):
        self.assertFinds("Niger", "NER")
        self.assertFinds("Nigéria", "NGA")
        self.assertFinds("Austria", "AUT")
        self.assertFinds("Australia", "AUS")

    def test_start_of_a_name_and_typos(self):
        self.assertFinds("Bosnia", "BIH")
        self.assertFinds("Alemanah", "DEU")
        self.assertFinds("Swizterland", "CHE")

    def test_unknown(self):
        for text in ("Narnia", "", "a", "xy"):
            with self.subTest(text=text):
                self.assertIsNone(countries.find(text))

    def test_answers_can_name_two_countries(self):
        found = countries.find_all("Palestinian territories or Israel")
        self.assertEqual([country.code for country in found], ["PSE", "ISR"])

    def test_names_shown_are_brazilian(self):
        self.assertEqual(countries.find("Poland").name, "Polônia")
        self.assertEqual(countries.find("Vietnam").name, "Vietnã")


def make_interaction(user_id=1, name="Ana"):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, display_name=name),
        response=SimpleNamespace(send_message=AsyncMock(), send_modal=AsyncMock()),
    )


def replies(interaction):
    return "\n".join(call.args[0] for call in interaction.response.send_message.await_args_list)


class GuessRoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_guess_is_only_shown_to_whoever_made_it(self):
        round_ = GuessRound(1)
        round_.message = SimpleNamespace(edit=AsyncMock())
        round_.embed = timeguessr.discord.Embed()
        interaction = make_interaction()

        await round_.submit(interaction, " japao ", "1985")

        guess = round_.guesses[1]
        self.assertEqual((guess.text, guess.year, guess.country.code), ("japao", 1985, "JPN"))
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        self.assertIn("Japão", replies(interaction))
        # The round message only says who guessed.
        self.assertEqual(round_.embed.footer.text, "✏️ Já palpitaram: Ana")
        self.assertNotIn("Jap", round_.embed.footer.text)

    async def test_a_new_guess_replaces_the_old_one(self):
        round_ = GuessRound(1)
        await round_.submit(make_interaction(), "Brasil", "1990")
        await round_.submit(make_interaction(), "Chile", "1991")

        self.assertEqual(round_.guesses[1].country.code, "CHL")
        self.assertEqual(len(round_.guesses), 1)

    async def test_the_year_must_be_a_number(self):
        round_ = GuessRound(1)
        interaction = make_interaction()

        await round_.submit(interaction, "Brasil", "anos 80")

        self.assertEqual(round_.guesses, {})
        self.assertIn("não é um ano", replies(interaction))

    async def test_an_unknown_country_still_counts_with_a_warning(self):
        round_ = GuessRound(1)
        interaction = make_interaction()

        await round_.submit(interaction, "Narnia", "1950")

        self.assertIsNone(round_.guesses[1].country)
        self.assertIn("não reconheci", replies(interaction))

    async def test_no_guesses_after_the_round(self):
        round_ = GuessRound(1)
        round_.open = False
        interaction = make_interaction()

        await round_.submit(interaction, "Brasil", "1990")

        self.assertEqual(round_.guesses, {})
        self.assertIn("já acabou", replies(interaction))

    async def test_everyone_in_once_all_players_guessed(self):
        round_ = GuessRound(2, players={1, 2})
        await round_.submit(make_interaction(1), "Brasil", "1990")
        self.assertFalse(round_.everyone_in.is_set())

        await round_.submit(make_interaction(3, "Novato"), "Chile", "1990")
        self.assertFalse(round_.everyone_in.is_set())

        await round_.submit(make_interaction(2, "Bia"), "Peru", "1990")
        self.assertTrue(round_.everyone_in.is_set())

    async def test_first_round_has_no_known_players_so_it_runs_to_the_end(self):
        round_ = GuessRound(1)
        await round_.submit(make_interaction(), "Brasil", "1990")
        self.assertFalse(round_.everyone_in.is_set())

    async def test_the_form_starts_with_the_previous_guess(self):
        round_ = GuessRound(3)
        await round_.submit(make_interaction(), "Japão", "1985")

        modal = GuessModal(round_, 1)
        self.assertEqual((modal.country.default, modal.year.default), ("Japão", "1985"))
        self.assertIsNone(GuessModal(round_, 2).country.default)
        self.assertEqual(modal.title, "Palpite — Rodada 3")


def make_round_ctx():
    message = SimpleNamespace(edit=AsyncMock())
    ctx = make_ctx()
    ctx.send = AsyncMock(return_value=message)
    return ctx, message


class PlayRoundTests(TempDataDir):
    def make_cog(self):
        cog = TimeGuessr(bot=SimpleNamespace())
        cog.warning_lead = 0.05
        return cog

    async def test_ends_early_once_every_player_guessed(self):
        cog = self.make_cog()
        ctx, message = make_round_ctx()
        cog.active_games[42] = {}

        async def players_guess():
            while 'round' not in cog.active_games[42]:
                await asyncio.sleep(0)
            round_ = cog.active_games[42]['round']
            await round_.submit(make_interaction(1), "Brasil", "2016")
            await round_.submit(make_interaction(2, "Bia"), "Chile", "2000")

        guessing = asyncio.create_task(players_guess())
        guesses = await asyncio.wait_for(
            cog.play_round(ctx, 2, 5, ROUND, tempo=30, players={1, 2}), timeout=2
        )
        await guessing

        self.assertEqual(set(guesses), {1, 2})
        self.assertIn("Todo mundo já palpitou", sent_text(ctx))
        self.assertNotIn("segundos restantes", sent_text(ctx))
        self.assertFalse(cog.active_games[42]['round'].open)
        # The button is turned off at the end.
        view = message.edit.await_args.kwargs["view"]
        self.assertTrue(all(item.disabled for item in view.children))

    async def test_runs_out_the_clock_with_a_warning(self):
        cog = self.make_cog()
        ctx, _ = make_round_ctx()

        guesses = await asyncio.wait_for(
            cog.play_round(ctx, 1, 5, ROUND, tempo=0.1), timeout=2
        )

        self.assertEqual(guesses, {})
        self.assertIn("segundos restantes", sent_text(ctx))
        self.assertNotIn("Todo mundo", sent_text(ctx))
        first = ctx.send.await_args_list[0]
        self.assertEqual(first.kwargs["embed"].image.url, ROUND['URL'])
        self.assertEqual(first.kwargs["view"].children[0].label, "Palpitar")

    async def test_players_of_earlier_rounds_are_passed_on(self):
        cog = make_cog()
        cog.reaction_timeout = 60
        cog.bot.wait_for = AsyncMock()  # someone always continues
        cog.play_round = AsyncMock(return_value={})
        cog.score_round = Mock(side_effect=[{1: Result(100, 0, None, False)}, {}])

        await asyncio.wait_for(TimeGuessr.timeguessr.callback(cog, make_ctx()), timeout=2)

        first, second = cog.play_round.await_args_list
        self.assertEqual(set(first.args[5]), set())
        self.assertEqual(set(second.args[5]), {1})


class ChatGuessTests(TempDataDir):
    def make_message(self, content, user_id=1):
        return SimpleNamespace(
            content=content,
            author=SimpleNamespace(id=user_id, bot=False, mention=f"<@{user_id}>"),
            channel=SimpleNamespace(id=42, send=AsyncMock()),
            delete=AsyncMock(),
        )

    def make_cog(self, round_open=True):
        cog = TimeGuessr(bot=SimpleNamespace())
        round_ = GuessRound(1)
        round_.open = round_open
        cog.active_games[42] = {'round': round_}
        return cog

    async def test_a_guess_in_the_chat_is_deleted_with_a_hint_once(self):
        cog = self.make_cog()
        first, second = self.make_message("Brasil, 1990"), self.make_message("Chile, 1991")

        await cog.on_message(first)
        await cog.on_message(second)

        first.delete.assert_awaited_once()
        second.delete.assert_awaited_once()
        first.channel.send.assert_awaited_once()
        self.assertIn("Palpitar", first.channel.send.await_args.args[0])
        second.channel.send.assert_not_awaited()
        # It doesn't count as a guess.
        self.assertEqual(cog.active_games[42]['round'].guesses, {})

    async def test_other_messages_are_left_alone(self):
        for content, round_open in (("bom dia", True), ("%tgstop", True), ("Brasil, 1990", False)):
            with self.subTest(content=content, round_open=round_open):
                cog = self.make_cog(round_open)
                message = self.make_message(content)

                await cog.on_message(message)

                message.delete.assert_not_awaited()
                message.channel.send.assert_not_awaited()

    async def test_without_permission_to_delete_it_still_hints(self):
        cog = self.make_cog()
        message = self.make_message("Brasil, 1990")
        message.delete = AsyncMock(side_effect=timeguessr.discord.Forbidden(Mock(status=403), "no"))

        await cog.on_message(message)

        message.channel.send.assert_awaited_once()


PHOTO = {
    'URL': 'https://img.example/1.jpg', 'Year': '1985', 'Country': 'USA',
    'Location': {'lat': 40.7, 'lng': -74.0}, 'Description': 'Nova York',
    'StreetView': 'https://www.google.com/maps/embed?pb=!4v1!6m8!1m7!1sPANO!2m2!1d40.7!2d-74.0!3f10.5!4f-2.5!5f0.78',
}


def guess(text, year, user_id=1, name="Ana"):
    return Guess(SimpleNamespace(id=user_id, display_name=name), text, year, countries.find(text))


class ScoringTests(TempDataDir):
    def score(self, guesses, photo=PHOTO):
        return TimeGuessr(bot=SimpleNamespace()).score_round(photo, guesses)

    def test_right_country_in_portuguese_gets_full_location_points(self):
        result = self.score({1: guess("EUA", 1985)})[1]
        self.assertEqual((result.score, result.years_off, result.right_country), (5000, 0, True))

    def test_wrong_country_scores_by_distance(self):
        result = self.score({1: guess("Canadá", 1990)})[1]
        self.assertFalse(result.right_country)
        self.assertEqual(result.years_off, 5)
        self.assertGreater(result.km, 1000)
        self.assertEqual(result.score, 2000 + timeguessr.location_score(result.km))

    def test_unknown_country_gets_no_location_points(self):
        result = self.score({1: guess("Narnia", 1985)})[1]
        self.assertEqual((result.score, result.km, result.right_country), (2500, None, False))

    def test_either_country_of_an_or_answer_is_right(self):
        photo = dict(PHOTO, Country="Palestinian territories or Israel", Location={'lat': 31.7, 'lng': 35.2})
        self.assertTrue(self.score({1: guess("Israel", 1985)}, photo)[1].right_country)
        self.assertTrue(self.score({1: guess("Palestina", 1985)}, photo)[1].right_country)

    def test_typed_like_the_answer_is_right_even_if_unknown(self):
        photo = dict(PHOTO, Country="Atlantis")
        self.assertTrue(self.score({1: guess("atlantis", 1985)}, photo)[1].right_country)


class AnswerTests(TempDataDir):
    async def show(self, guesses, scores, photo=PHOTO):
        ctx = make_ctx()
        await TimeGuessr(bot=SimpleNamespace()).show_answer(ctx, 1, photo, guesses, scores)
        embed = ctx.send.await_args.kwargs["embed"]
        return {field.name: field.value for field in embed.fields}

    async def test_shows_how_far_off_each_player_was(self):
        guesses = {1: guess("EUA", 1985), 2: guess("Canadá", 1990, 2, "Bia"), 3: guess("Narnia", 1984, 3, "Cris")}
        scores = {
            1: Result(5000, 0, 0, True),
            2: Result(3000, 5, 1234.4, False),
            3: Result(2400, 1, None, False),
        }

        fields = await self.show(guesses, scores)

        board = fields["🏆 Pontuação"]
        self.assertLess(board.index("Ana"), board.index("Bia"))
        self.assertIn("Estados Unidos, 1985 → **5.000** pts", board)
        self.assertIn("✅ país certo · 📅 ano exato!", board)
        self.assertIn("📍 a 1.234 km · 📅 5 anos de diferença", board)
        self.assertIn("Narnia, 1984", board)
        self.assertIn("❓ país não reconhecido · 📅 1 ano de diferença", board)
        self.assertEqual(fields["🌎 País"], "Estados Unidos")

    async def test_links_to_street_view_when_there_is_one(self):
        fields = await self.show({}, {})
        self.assertIn("[Street View](https://www.google.com/maps/@?api=1&map_action=pano&pano=PANO", fields["🗺️ Mapa"])
        self.assertIn("Ninguém respondeu", fields["💬 Respostas"])

        fields = await self.show({}, {}, dict(PHOTO, StreetView=""))
        self.assertNotIn("Street View", fields["🗺️ Mapa"])

    async def test_a_long_board_is_cut_between_players(self):
        guesses = {i: guess("Canadá", 1990, i, "Jogador" * 5) for i in range(30)}
        scores = {i: Result(3000, 5, 1234, False) for i in range(30)}

        board = (await self.show(guesses, scores))["🏆 Pontuação"]

        self.assertLessEqual(len(board), 1024)
        self.assertRegex(board, r"…e mais \d+$")


class FormattingTests(unittest.TestCase):
    def test_street_view_link(self):
        self.assertEqual(
            street_view_link(PHOTO['StreetView']),
            "https://www.google.com/maps/@?api=1&map_action=pano&pano=PANO&heading=10.5&pitch=-2.5",
        )
        self.assertEqual(street_view_link("https://example.com/sv"), "https://example.com/sv")

    def test_fit_lines(self):
        self.assertEqual(fit_lines(["a", "b"], 20), "a\nb")
        self.assertEqual(fit_lines(["aaaa", "bbbb", "cccc"], 16), "aaaa\n…e mais 2")


if __name__ == "__main__":
    unittest.main()
