"""Tests for the smaller cogs: birthdays, memes, dice, currency, TimeGuessr."""
import asyncio
import datetime
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from discord.ext import commands

from cogs.birthday import (
    EMBED_FIELD_LIMIT, MIDNIGHT, TIMEZONE, Birthday, days_until, is_birthday, parse_birthday,
)
from cogs.coin import CoinConverter
from cogs.membercount import MemberCount
from cogs.music import Music
from cogs.reddit import EMBED_TITLE_LIMIT, Reddit
from cogs.roll import DiceError, Roll, parse_dice
from cogs.timeguessr import TimeGuessr
from tests.test_gemini import Typing


def sent_text(ctx):
    return "\n".join(str(call.args[0]) for call in ctx.send.await_args_list if call.args)


class TempDataDirMixin:
    def use_temp_data_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.dict(os.environ, {'DATA_DIR': tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        return tmp.name


class BirthdayDateTests(unittest.TestCase):
    def test_dates_are_stored_zero_padded(self):
        self.assertEqual(parse_birthday("1-5"), "01-05")
        self.assertEqual(parse_birthday("25-12"), "25-12")

    def test_accepts_february_29(self):
        self.assertEqual(parse_birthday("29-02"), "29-02")

    def test_rejects_impossible_dates(self):
        for text in ("31-02", "32-01", "12/25", "abc"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_birthday(text)

    def test_february_29_is_celebrated_on_the_28th_in_common_years(self):
        self.assertTrue(is_birthday("29-02", datetime.date(2027, 2, 28)))
        self.assertFalse(is_birthday("29-02", datetime.date(2028, 2, 28)))
        self.assertTrue(is_birthday("29-02", datetime.date(2028, 2, 29)))

    def test_days_until_the_next_birthday(self):
        today = datetime.date(2027, 12, 30)
        self.assertEqual(days_until("30-12", today), 0)
        self.assertEqual(days_until("31-12", today), 1)
        self.assertEqual(days_until("29-12", today), 365)  # 2028 has 366 days
        # 2028 is a leap year, so 29-02 isn't moved to the 28th.
        self.assertEqual(days_until("29-02", today), 61)

    def test_check_runs_at_midnight_in_sao_paulo(self):
        self.assertEqual(Birthday.check_birthdays.time, [MIDNIGHT])
        self.assertEqual(MIDNIGHT.tzinfo, TIMEZONE)


class BirthdayStorageTests(TempDataDirMixin, unittest.TestCase):
    def setUp(self):
        self.data_dir = self.use_temp_data_dir()

    def test_saved_birthdays_win_over_the_env_var(self):
        with open(os.path.join(self.data_dir, 'birthdays.json'), 'w') as f:
            json.dump({'1': '01-05'}, f)

        with patch.dict(os.environ, {'BIRTHDAYS': '{"2": "02-02"}'}):
            self.assertEqual(Birthday.load_saved('birthdays.json', 'BIRTHDAYS'), {'1': '01-05'})

    def test_env_var_seeds_the_file_when_nothing_was_saved(self):
        with patch.dict(os.environ, {'BIRTHDAYS': '{"2": "02-02"}'}):
            self.assertEqual(Birthday.load_saved('birthdays.json', 'BIRTHDAYS'), {'2': '02-02'})

        with open(os.path.join(self.data_dir, 'birthdays.json')) as f:
            self.assertEqual(json.load(f), {'2': '02-02'})


class BirthdayCommandTests(TempDataDirMixin, unittest.IsolatedAsyncioTestCase):
    async def test_add_stores_the_normalized_date(self):
        self.use_temp_data_dir()
        cog = Birthday.__new__(Birthday)
        cog.birthdays = {}
        ctx = SimpleNamespace(send=AsyncMock())
        member = SimpleNamespace(id=7, mention="@seven")

        await Birthday.add_birthday.callback(cog, ctx, member, "1-5")

        self.assertEqual(cog.birthdays, {'7': '01-05'})


class BirthdayHelpTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_shows_how_the_subcommands_are_really_used(self):
        ctx = SimpleNamespace(clean_prefix="%", send=AsyncMock())

        await Birthday.birthday.callback(Birthday.__new__(Birthday), ctx)

        self.assertIn("%birthday add <member> <dd-mm>", sent_text(ctx))
        self.assertIn("%birthday remove <member>", sent_text(ctx))


class BirthdayAnnouncementTests(TempDataDirMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.use_temp_data_dir()

    def make_cog(self, guilds):
        cog = Birthday.__new__(Birthday)
        cog.bot = SimpleNamespace(guilds=guilds)
        cog.birthdays = {'7': '12-06'}
        cog.channels = {}
        return cog

    def make_guild(self, guild_id, channel):
        member = SimpleNamespace(mention="@seven")
        return SimpleNamespace(
            id=guild_id, get_member=lambda user_id: member, system_channel=channel
        )

    async def announce_on(self, cog, day):
        now = datetime.datetime.combine(day, datetime.time(0, 5), TIMEZONE)
        with patch('cogs.birthday.datetime.datetime') as fake_datetime:
            fake_datetime.now.return_value = now
            await cog.announce_today()

    async def test_a_failing_channel_does_not_stop_other_announcements(self):
        broken = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("Forbidden")))
        working = SimpleNamespace(send=AsyncMock())
        cog = self.make_cog([self.make_guild(1, broken), self.make_guild(2, working)])

        with self.assertLogs('cogs.birthday', 'ERROR'):
            await self.announce_on(cog, datetime.date(2027, 6, 12))

        working.send.assert_awaited_once()

    async def test_announces_only_once_per_day(self):
        channel = SimpleNamespace(send=AsyncMock())
        cog = self.make_cog([self.make_guild(1, channel)])

        # Midnight run, then a restart later the same day.
        await self.announce_on(cog, datetime.date(2027, 6, 12))
        await self.announce_on(cog, datetime.date(2027, 6, 12))

        channel.send.assert_awaited_once()

    async def test_a_restart_announces_birthdays_missed_at_midnight(self):
        channel = SimpleNamespace(send=AsyncMock())
        cog = self.make_cog([self.make_guild(1, channel)])
        await self.announce_on(cog, datetime.date(2027, 6, 11))

        await self.announce_on(cog, datetime.date(2027, 6, 12))

        channel.send.assert_awaited_once()


class BirthdayListTests(unittest.IsolatedAsyncioTestCase):
    async def list_embed(self, birthdays, today):
        cog = Birthday.__new__(Birthday)
        cog.birthdays = birthdays
        guild = SimpleNamespace(
            get_member=lambda user_id: SimpleNamespace(display_name=f"user{user_id}")
        )
        ctx = SimpleNamespace(guild=guild, send=AsyncMock())
        now = datetime.datetime.combine(today, datetime.time(12), TIMEZONE)
        with patch('cogs.birthday.datetime.datetime') as fake_datetime:
            fake_datetime.now.return_value = now
            fake_datetime.strptime = datetime.datetime.strptime
            await Birthday.list_birthdays.callback(cog, ctx)
        return ctx.send.await_args.kwargs['embed']

    async def test_upcoming_birthdays_come_first(self):
        embed = await self.list_embed(
            {'1': '01-01', '2': '15-06', '3': '10-06', '4': '01-12'}, datetime.date(2027, 6, 12)
        )

        self.assertEqual([f.name for f in embed.fields], ["user2", "user4", "user1", "user3"])

    async def test_list_fits_in_one_embed(self):
        birthdays = {str(i): '01-01' for i in range(EMBED_FIELD_LIMIT + 3)}
        embed = await self.list_embed(birthdays, datetime.date(2027, 6, 12))

        self.assertEqual(len(embed.fields), EMBED_FIELD_LIMIT)
        self.assertEqual(embed.footer.text, "…and 3 more")


class MemberCountTests(TempDataDirMixin, unittest.IsolatedAsyncioTestCase):
    def make_cog(self, guilds):
        self.use_temp_data_dir()
        cog = MemberCount.__new__(MemberCount)
        cog.bot = SimpleNamespace(get_guild=guilds.get)
        cog.config = {'1': '10', '2': '20'}
        return cog

    async def test_guilds_the_bot_left_are_skipped_quietly(self):
        channel = SimpleNamespace(name="old", edit=AsyncMock())
        guild = SimpleNamespace(member_count=5, get_channel=lambda channel_id: channel)
        cog = self.make_cog({2: guild})

        with self.assertNoLogs('cogs.membercount', 'ERROR'):
            await MemberCount.update_channel.coro(cog)

        channel.edit.assert_awaited_once_with(name="👥 Membros: 5")

    async def test_leaving_a_guild_forgets_its_channel(self):
        cog = self.make_cog({})

        await cog.on_guild_remove(SimpleNamespace(id=1))

        self.assertEqual(cog.config, {'2': '20'})
        self.assertEqual(cog.load_config(), {'2': '20'})


class GuildOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_commands_refuse_direct_messages(self):
        for cog_class in (Music, Birthday, MemberCount):
            with self.subTest(cog=cog_class.__name__):
                cog = cog_class.__new__(cog_class)
                with self.assertRaises(commands.NoPrivateMessage):
                    await cog.cog_check(SimpleNamespace(guild=None))
                self.assertTrue(await cog.cog_check(SimpleNamespace(guild=object())))


class FakeSubreddit:
    def __init__(self, posts):
        self.posts = posts

    async def hot(self, limit):
        for post in self.posts[:limit]:
            yield post


def make_post(title="Meme", url="https://i.redd.it/x.png", author="someone",
              over_18=False, stickied=False):
    return SimpleNamespace(
        title=title, url=url, selftext="", over_18=over_18, stickied=stickied,
        author=SimpleNamespace(name=author) if author else None,
    )


class MemeTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, posts):
        cog = Reddit.__new__(Reddit)
        cog.reddit = SimpleNamespace(subreddit=AsyncMock(return_value=FakeSubreddit(posts)))
        return cog

    def make_ctx(self, nsfw=False):
        return SimpleNamespace(
            send=AsyncMock(),
            typing=Typing,
            channel=SimpleNamespace(is_nsfw=lambda: nsfw),
            author=SimpleNamespace(name="user", display_avatar=SimpleNamespace(url="https://cdn/a.png")),
        )

    async def meme(self, cog, ctx):
        await Reddit.meme.callback(cog, ctx)
        return ctx.send.await_args.kwargs.get('embed')

    async def test_posts_from_deleted_accounts_still_work(self):
        cog = self.make_cog([make_post(author=None)])

        embed = await self.meme(cog, self.make_ctx())

        self.assertIn("u/[deleted]", embed.footer.text)

    async def test_long_titles_fit_in_the_embed(self):
        cog = self.make_cog([make_post(title="x" * 300)])

        embed = await self.meme(cog, self.make_ctx())

        self.assertEqual(len(embed.title), EMBED_TITLE_LIMIT)

    async def test_fetches_only_one_subreddit_when_it_has_memes(self):
        cog = self.make_cog([make_post()])

        await self.meme(cog, self.make_ctx())

        cog.reddit.subreddit.assert_awaited_once()

    async def test_nsfw_posts_stay_out_of_regular_channels(self):
        cog = self.make_cog([make_post(over_18=True)])

        ctx = self.make_ctx(nsfw=False)
        embed = await self.meme(cog, ctx)

        self.assertIsNone(embed)
        self.assertIn("Couldn't find a meme", sent_text(ctx))

    async def test_nsfw_posts_are_allowed_in_nsfw_channels(self):
        cog = self.make_cog([make_post(over_18=True)])

        embed = await self.meme(cog, self.make_ctx(nsfw=True))

        self.assertIsNotNone(embed)

    async def test_without_credentials_the_command_explains_it(self):
        env = {'REDDIT_CLIENT_ID': '', 'REDDIT_CLIENT_SECRET': '', 'REDDIT_USER_AGENT': ''}
        with patch.dict(os.environ, env), self.assertLogs('cogs.reddit', 'WARNING'):
            cog = Reddit(bot=None)
        ctx = self.make_ctx()

        await Reddit.meme.callback(cog, ctx)

        self.assertIn("Reddit API credentials", sent_text(ctx))
        await cog.cog_unload()


class DiceTests(unittest.TestCase):
    def test_parses_dice_and_modifiers(self):
        self.assertEqual(parse_dice("2d20+5"), ([(1, 2, 20)], 5))
        self.assertEqual(parse_dice("3d6 + 2d4 - 2"), ([(1, 3, 6), (1, 2, 4)], -2))

    def test_negative_modifiers_are_not_ignored(self):
        self.assertEqual(parse_dice("1d20-2"), ([(1, 1, 20)], -2))

    def test_a_single_die_needs_no_count(self):
        self.assertEqual(parse_dice("d20"), ([(1, 1, 20)], 0))

    def test_rejects_malformed_expressions(self):
        for expression in ("2d6abc", "d", "1d20 5", "", "1d0"):
            with self.subTest(expression=expression), self.assertRaises(DiceError):
                parse_dice(expression)

    def test_limits_dice_sides_and_terms(self):
        for expression in ("101d6", "1d101", "+".join(["1d6"] * 21)):
            with self.subTest(expression=expression[:20]), self.assertRaises(DiceError):
                parse_dice(expression)


class RollCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_total_includes_the_negative_modifier(self):
        ctx = SimpleNamespace(send=AsyncMock())
        with patch('cogs.roll.random.randint', return_value=10):
            await Roll.roll.callback(Roll(bot=None), ctx, dice_expression="1d20-2")

        embed = ctx.send.await_args.kwargs['embed']
        self.assertEqual(embed.fields[-1].value, "8")

    async def test_invalid_expressions_get_a_friendly_message(self):
        ctx = SimpleNamespace(send=AsyncMock())

        await Roll.roll.callback(Roll(bot=None), ctx, dice_expression="abc")

        self.assertIn("Invalid dice format", sent_text(ctx))


class FakeHTTPSession:
    def __init__(self, error=None, json_data=None, responses=None):
        self.error = error
        self.json_data = json_data
        # Optional (status, json) per request, in order; else 200 + json_data.
        self.responses = list(responses or [])
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        self.urls.append(url)
        if self.error:
            raise self.error
        status, data = self.responses.pop(0) if self.responses else (200, self.json_data)

        def raise_for_status():
            if status >= 400:
                raise aiohttp.ClientResponseError(Mock(), (), status=status)

        response = SimpleNamespace(
            status=status, json=AsyncMock(return_value=data), raise_for_status=raise_for_status
        )

        class Request:
            async def __aenter__(self_inner):
                return response

            async def __aexit__(self_inner, *exc):
                return False

        return Request()


class CoinTests(unittest.IsolatedAsyncioTestCase):
    async def convert(self, session, *args):
        ctx = SimpleNamespace(send=AsyncMock())
        with patch('cogs.coin.aiohttp.ClientSession', return_value=session):
            await CoinConverter.coin_convert.callback(CoinConverter(bot=None), ctx, *args)
        return ctx

    async def test_converts_with_the_fetched_rate(self):
        session = FakeHTTPSession(json_data={'rates': {'USD': 0.2}})

        ctx = await self.convert(session, 5.0, "brl", "usd")

        self.assertIn("**1.00 USD**", ctx.send.await_args.kwargs['embed'].description)

    async def test_network_errors_get_a_friendly_message_without_details(self):
        session = FakeHTTPSession(error=aiohttp.ClientConnectionError("10.0.0.1 refused"))

        with self.assertLogs('cogs.coin', level='ERROR'):
            ctx = await self.convert(session, 5.0, "BRL", "USD")

        self.assertIn("isn't responding", sent_text(ctx))
        self.assertNotIn("10.0.0.1", sent_text(ctx))

    async def test_currency_codes_are_validated_before_any_request(self):
        session = FakeHTTPSession()

        ctx = await self.convert(session, 5.0, "../x", "USD")

        self.assertEqual(session.urls, [])
        self.assertIn("isn't a currency code", sent_text(ctx))


class TimeGuessrGeocodeTests(unittest.IsolatedAsyncioTestCase):
    URL = "https://restcountries.com/v3.1/name/"

    async def geocode(self, cog, session, name):
        with patch('cogs.timeguessr.aiohttp.ClientSession', return_value=session):
            return await cog.geocode_country(name)

    async def test_country_names_are_url_encoded(self):
        session = FakeHTTPSession(json_data=[{'latlng': [1, 2]}])

        self.assertEqual(
            await self.geocode(TimeGuessr(bot=Mock()), session, "Bosnia/Herzegovina?x=1"), (1, 2)
        )

        self.assertEqual(session.urls, [self.URL + "Bosnia%2FHerzegovina%3Fx%3D1?fullText=true"])

    async def test_partial_names_are_the_fallback(self):
        session = FakeHTTPSession(responses=[(404, None), (200, [{'latlng': [3, 4]}])])

        self.assertEqual(await self.geocode(TimeGuessr(bot=Mock()), session, "Bosnia"), (3, 4))

        self.assertEqual(session.urls, [self.URL + "Bosnia?fullText=true", self.URL + "Bosnia"])

    async def test_unknown_countries_are_cached(self):
        cog = TimeGuessr(bot=Mock())
        await self.geocode(cog, FakeHTTPSession(responses=[(404, None), (404, None)]), "Narnia")

        session = FakeHTTPSession(json_data=[{'latlng': [1, 2]}])
        self.assertIsNone(await self.geocode(cog, session, "narnia"))
        self.assertEqual(session.urls, [])

    async def test_service_failures_are_not_cached(self):
        cog = TimeGuessr(bot=Mock())
        for failing in (
            FakeHTTPSession(error=asyncio.TimeoutError()),
            FakeHTTPSession(responses=[(503, None)]),
        ):
            with self.subTest(failing=failing), self.assertLogs('cogs.timeguessr', 'WARNING'):
                self.assertIsNone(await self.geocode(cog, failing, "Brazil"))

        working = FakeHTTPSession(json_data=[{'latlng': [-10, -55]}])
        self.assertEqual(await self.geocode(cog, working, "Brazil"), (-10, -55))


if __name__ == "__main__":
    unittest.main()
