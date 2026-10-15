"""Tests for running the commands as slash commands."""
import asyncio
import os
import re
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import discord
from discord.ext import commands
from discord.webhook.async_ import async_context

import cogs.music as music_module
from cogs.coin import MAX_CHOICES, currency_choices
from cogs.gemini import QUOTE_LIMIT, quote
from cogs.music import CHOICE_NAME_LIMIT, NOT_IN_PLAYER_CHANNEL_MESSAGE, Music
from magna import slash
from tests.music_helpers import make_ctx, make_member, make_music, make_voice_client, sent_text
from tests.test_gemini import make_client, text_response

BOT_ID = 5
USER_ID = 7
CHANNEL_ID = 20
EPHEMERAL = 64
# Interaction response types.
CHANNEL_MESSAGE = 4
DEFERRED_CHANNEL_MESSAGE = 5
AUTOCOMPLETE_RESULT = 8

GUILD_ONLY = {
    'play', 'stop', 'skip', 'queue', 'loop', 'leave', 'pause',
    'birthday', 'setmembercount', 'stopmembercount',
}
NAME_PATTERN = re.compile(r'^[-_\w]{1,32}$')


def user_payload(user_id, name, *, bot=False):
    return {'id': str(user_id), 'username': name, 'discriminator': '0', 'avatar': None, 'bot': bot}


class FakeAdapter:
    """Records what would be sent to Discord's interaction endpoints."""

    def __init__(self):
        self.calls = []

    @staticmethod
    def message(payload):
        payload = payload or {}
        return {
            'id': '900', 'channel_id': str(CHANNEL_ID), 'author': user_payload(BOT_ID, 'magna', bot=True),
            'content': payload.get('content') or '', 'embeds': payload.get('embeds') or [],
            'timestamp': '2026-01-01T00:00:00+00:00', 'edited_timestamp': None, 'tts': False,
            'mention_everyone': False, 'mentions': [], 'mention_roles': [], 'attachments': [],
            'pinned': False, 'type': 0, 'flags': payload.get('flags', 0),
        }

    async def create_interaction_response(self, interaction_id, token, *, params, **kwargs):
        self.calls.append(('response', params.payload))
        return {'interaction': {'id': str(interaction_id), 'type': 2}}

    async def get_original_interaction_response(self, application_id, token, **kwargs):
        return self.message(None)

    async def execute_webhook(self, webhook_id, token, *, payload=None, **kwargs):
        self.calls.append(('followup', payload))
        return self.message(payload)

    async def edit_original_interaction_response(self, application_id, token, *, payload=None, **kwargs):
        self.calls.append(('edit', payload))
        return self.message(payload)

    async def edit_webhook_message(self, application_id, token, message_id, *, payload=None, **kwargs):
        self.calls.append(('edit followup', payload))
        return self.message(payload)

    def contents(self):
        """The text of every message sent, in order."""
        texts = []
        for _, payload in self.calls:
            data = payload.get('data', payload) if payload else {}
            if data.get('content'):
                texts.append(data['content'])
        return texts


async def make_bot():
    """A bot with every cog loaded, like main.py's, that never logs in.

    Call close_bot when done.
    """
    bot = commands.Bot(command_prefix='%', intents=discord.Intents.all())
    # Loading an extension imports its module again; keep the modules the
    # other tests imported (and patch) in place.
    bot.saved_modules = {name: module for name, module in sys.modules.items() if name.startswith('cogs.')}
    await bot._async_setup_hook()
    bot._connection.application_id = BOT_ID
    bot._connection.user = discord.ClientUser(state=bot._connection, data=user_payload(BOT_ID, 'magna', bot=True))
    for filename in sorted(os.listdir('cogs')):
        if filename.endswith('.py'):
            await bot.load_extension(f'cogs.{filename[:-3]}')
    return bot


async def close_bot(bot):
    for extension in list(bot.extensions):
        await bot.unload_extension(extension)
    sys.modules.update(bot.saved_modules)


async def dispatched_events():
    """Let the event handlers bot.dispatch started (like on_command_error) finish."""
    events = [
        task for task in asyncio.all_tasks()
        if task.get_name().startswith('discord.py: ') and task is not asyncio.current_task()
    ]
    await asyncio.gather(*events)


def interaction_payload(kind, data, *, created=None, **extra):
    """The gateway payload of an interaction from a DM."""
    created = created or discord.utils.utcnow()
    return {
        'id': str(discord.utils.time_snowflake(created)), 'application_id': str(BOT_ID),
        'type': kind, 'token': 'token', 'version': 1, 'locale': 'pt-BR',
        'channel_id': str(CHANNEL_ID), 'channel': {'id': str(CHANNEL_ID), 'type': 1},
        'user': user_payload(USER_ID, 'gab'), 'attachment_size_limit': 8 * 1024 * 1024,
        'entitlements': [], 'authorizing_integration_owners': {},
        'data': data, **extra,
    }


def interaction(bot, name, options=(), *, kind=2, expired=False, subcommand=None):
    """A slash command (kind 2) or autocomplete (kind 4) interaction from a DM."""
    created = discord.utils.utcnow()
    if expired:
        created -= discord.utils.utcnow() - discord.utils.snowflake_time(1000)
    options = [dict(option) for option in options]
    if subcommand:
        options = [{'name': subcommand, 'type': 1, 'options': options}]
    data = interaction_payload(
        kind, {'id': '1', 'name': name, 'type': 1, 'options': options}, created=created
    )
    return discord.Interaction(data=data, state=bot._connection)


def option(name, value, *, kind=3, focused=False):
    data = {'name': name, 'type': kind, 'value': value}
    if focused:
        data['focused'] = True
    return data


class BotTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {'DATA_DIR': tmp.name, 'GEMINI_API_KEY': ''})
        env.start()
        self.addCleanup(env.stop)

        self.bot = await make_bot()
        self.adapter = FakeAdapter()
        async_context.set(self.adapter)

    async def asyncTearDown(self):
        await close_bot(self.bot)

    async def run_slash(self, name, *options, **kwargs):
        await self.bot.tree._call(interaction(self.bot, name, options, **kwargs))
        await dispatched_events()
        return self.adapter.calls


class CommandTreeTests(BotTestCase):
    def payloads(self):
        return [command.to_dict(self.bot.tree) for command in self.bot.tree.get_commands()]

    def test_every_command_is_also_a_slash_command(self):
        slash_names = {command.name for command in self.bot.tree.get_commands()}
        # %help is discord.py's own and %sync must work when slash commands don't.
        prefix_only = {'help', 'sync'}

        for command in self.bot.commands:
            if command.name in prefix_only:
                continue
            with self.subTest(command=command.name):
                self.assertIsInstance(command, commands.HybridCommand | commands.HybridGroup)
                self.assertIn(command.name, slash_names)

    def test_sync_is_owner_only_and_not_a_slash_command(self):
        command = self.bot.get_command('sync')
        self.assertNotIsInstance(command, commands.HybridCommand)
        self.assertIsNone(self.bot.tree.get_command('sync'))
        self.assertTrue(command.checks)

    def test_payload_fits_discords_limits(self):
        def check(entry, path):
            with self.subTest(command=path):
                self.assertRegex(entry['name'], NAME_PATTERN)
                self.assertEqual(entry['name'], entry['name'].lower())
                self.assertTrue(1 <= len(entry['description']) <= 100, entry['description'])
                self.assertNotEqual(entry['description'], '…')
                options = entry.get('options', [])
                self.assertLessEqual(len(options), 25)
                required = [option.get('required', False) for option in options]
                self.assertEqual(required, sorted(required, reverse=True), "required options come first")
            for child in options:
                check(child, f"{path} {child['name']}")

        payloads = self.payloads()
        self.assertLessEqual(len(payloads), 100)
        for payload in payloads:
            check(payload, payload['name'])

    def test_server_commands_are_hidden_in_dms(self):
        for payload in self.payloads():
            with self.subTest(command=payload['name']):
                self.assertEqual(payload['dm_permission'], payload['name'] not in GUILD_ONLY)

    def test_admin_commands_are_hidden_from_other_members(self):
        administrator = discord.Permissions(administrator=True).value
        for name in ('setmembercount', 'stopmembercount'):
            payload = self.bot.tree.get_command(name).to_dict(self.bot.tree)
            self.assertEqual(payload['default_member_permissions'], administrator)

    def test_arguments_are_described(self):
        for payload in self.payloads():
            for entry in payload.get('options', []):
                # Subcommands are described by their docstring instead.
                if entry['type'] == 1:
                    continue
                with self.subTest(command=payload['name'], option=entry['name']):
                    self.assertNotEqual(entry['description'], '…')

    def test_autocompleted_arguments(self):
        def autocompleted(name):
            payload = self.bot.tree.get_command(name).to_dict(self.bot.tree)
            return {entry['name'] for entry in payload['options'] if entry.get('autocomplete')}

        self.assertEqual(autocompleted('play'), {'query'})
        self.assertEqual(autocompleted('coin'), {'from_currency', 'to_currency'})

    def test_birthday_subcommands(self):
        group = self.bot.tree.get_command('birthday')
        self.assertEqual(
            {command.name for command in group.commands},
            {'help', 'add', 'remove', 'list', 'setchannel', 'removechannel'},
        )

    def test_prefix_aliases_still_work(self):
        for alias, name in (('hi', 'hello'), ('tg', 'timeguessr'), ('tgparar', 'tgstop')):
            with self.subTest(alias=alias):
                self.assertEqual(self.bot.get_command(alias).name, name)
        self.assertEqual(self.bot.get_command('birthday add').name, 'add')


class InteractionTests(BotTestCase):
    async def test_hello_replies_to_the_interaction(self):
        calls = await self.run_slash('hello')

        self.assertEqual(calls, [('response', {
            'type': CHANNEL_MESSAGE, 'data': {'tts': False, 'content': f"Hello there, <@{USER_ID}>!"},
        })])

    async def test_roll_takes_its_expression_as_an_option(self):
        calls = await self.run_slash('roll', option('dice_expression', '2d6+1'))

        (kind, payload), = calls
        embed = payload['data']['embeds'][0]
        self.assertEqual(payload['type'], CHANNEL_MESSAGE)
        self.assertEqual(embed['fields'][0]['name'], '2d6 rolls')
        self.assertEqual(embed['fields'][1], {'name': 'Modifier', 'value': '+1', 'inline': False})

    async def test_server_commands_explain_themselves_in_dms_only_to_the_user(self):
        calls = await self.run_slash('play', option('query', 'never gonna give you up'))

        (kind, payload), = calls
        self.assertEqual(payload['type'], CHANNEL_MESSAGE)
        self.assertEqual(payload['data']['flags'], EPHEMERAL)
        self.assertIn("só funciona em servidores", payload['data']['content'])

    async def test_slow_commands_defer_before_answering(self):
        with patch('cogs.coin.aiohttp.ClientSession', side_effect=aiohttp.ClientError):
            calls = await self.run_slash(
                'coin', option('amount', 5, kind=10),
                option('from_currency', 'brl'), option('to_currency', 'usd'),
            )

        self.assertEqual(calls[0], ('response', {'type': DEFERRED_CHANNEL_MESSAGE}))
        self.assertEqual(calls[1][0], 'followup')
        self.assertIn("exchange rate service", calls[1][1]['content'])

    async def test_invalid_input_answers_without_deferring(self):
        calls = await self.run_slash(
            'coin', option('amount', 5, kind=10),
            option('from_currency', 'reais'), option('to_currency', 'usd'),
        )

        self.assertEqual(calls[0][1]['type'], CHANNEL_MESSAGE)
        self.assertIn("isn't a currency code", calls[0][1]['data']['content'])

    async def test_ask_quotes_the_question_above_the_answer(self):
        self.bot.get_cog('Gemini').client = make_client(text_response("Eu sou o Magna!"))

        calls = await self.run_slash('ask', option('question', 'Quem é você?'))

        self.assertEqual(calls[0], ('response', {'type': DEFERRED_CHANNEL_MESSAGE}))
        self.assertEqual(self.adapter.contents(), ["> Quem é você?\n\nEu sou o Magna!"])

    async def test_ask_never_pings_through_the_quoted_question(self):
        self.bot.get_cog('Gemini').client = make_client(text_response("ok"))

        calls = await self.run_slash('ask', option('question', '@everyone olha isso'))

        self.assertEqual(calls[-1][1]['allowed_mentions'], {'parse': []})

    async def test_cooldowns_are_reported_only_to_the_user(self):
        self.bot.get_cog('Gemini').client = make_client(text_response("ok"))

        await self.run_slash('ask', option('question', 'oi'))
        self.adapter.calls.clear()
        calls = await self.run_slash('ask', option('question', 'oi de novo'))

        (kind, payload), = calls
        self.assertEqual(payload['data']['flags'], EPHEMERAL)
        self.assertIn("Calma!", payload['data']['content'])

    async def test_subcommands_run_through_the_group(self):
        calls = await self.run_slash('birthday', subcommand='help')

        # /birthday only works in servers, and this interaction is from a DM.
        self.assertIn("só funciona em servidores", calls[0][1]['data']['content'])

    async def test_replies_after_the_interaction_expired_go_to_the_channel(self):
        with patch.object(discord.abc.Messageable, 'send', AsyncMock()) as channel_send:
            calls = await self.run_slash('hello', expired=True)

        self.assertEqual(calls, [])
        self.assertEqual(channel_send.await_args.kwargs['content'], f"Hello there, <@{USER_ID}>!")

    async def test_play_suggests_songs_while_typing(self):
        music = self.bot.get_cog('Music')
        music.ytmusic_candidates = AsyncMock(return_value=[
            {'video_id': 'dQw4w9WgXcQ', 'title': 'Never Gonna Give You Up',
             'artists': ['Rick Astley'], 'duration': 213},
        ])

        calls = await self.run_slash('play', option('query', 'never gonna', focused=True), kind=4)

        self.assertEqual(calls, [('response', {'type': AUTOCOMPLETE_RESULT, 'data': {'choices': [{
            'name': 'Never Gonna Give You Up — Rick Astley (3:33)',
            'value': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        }]}})])
        music.ytmusic_candidates.assert_awaited_once_with('never gonna', 'songs')

    async def test_coin_suggests_currencies_while_typing(self):
        calls = await self.run_slash(
            'coin', option('amount', 5, kind=10), option('from_currency', 'real', focused=True), kind=4,
        )

        choices = calls[0][1]['data']['choices']
        self.assertEqual(choices, [{'name': 'BRL — Brazilian Real', 'value': 'BRL'}])


class PrefixTests(BotTestCase):
    """The same commands still run from %-prefixed messages."""

    async def run_prefix(self, content):
        channel = discord.DMChannel(me=self.bot.user, state=self.bot._connection, data={
            'id': str(CHANNEL_ID), 'type': 1, 'recipients': [user_payload(USER_ID, 'gab')],
        })
        message = discord.Message(state=self.bot._connection, channel=channel, data={
            **FakeAdapter.message({'content': content}),
            'id': str(discord.utils.time_snowflake(discord.utils.utcnow())),
            'author': user_payload(USER_ID, 'gab'),
        })
        with patch.object(discord.abc.Messageable, 'send', AsyncMock()) as send:
            await self.bot.process_commands(message)
            await dispatched_events()
        return send

    async def test_commands_and_aliases_run_with_the_prefix(self):
        for content in ('%hello', '%hi'):
            with self.subTest(content=content):
                send = await self.run_prefix(content)
                self.assertEqual(send.await_args.kwargs['content'], f"Hello there, <@{USER_ID}>!")

    async def test_rest_of_the_message_is_one_argument(self):
        send = await self.run_prefix('%roll 2d6 + 1')

        embed = send.await_args.kwargs['embed']
        self.assertEqual(embed.fields[1].value, '+1')

    async def test_errors_are_still_sent_to_the_channel(self):
        send = await self.run_prefix('%play never gonna give you up')

        self.assertIn("só funciona em servidores", send.await_args.kwargs['content'])
        self.assertEqual(self.adapter.calls, [])


class TimeGuessrButtonTests(BotTestCase):
    """A guess made through the round's button and form, as Discord sends them."""

    PHOTO = {
        'URL': 'https://img.example/1.jpg', 'Year': '1985', 'Country': 'Japan',
        'Location': {'lat': 35.7, 'lng': 139.7}, 'ImageId': 'photo-1',
    }

    async def until(self, condition):
        for _ in range(500):
            if condition():
                return
            await asyncio.sleep(0.01)
        self.fail("timed out")

    def sent_with_components(self):
        return [payload for _, payload in self.adapter.calls if payload and payload.get('components')]

    async def test_guess_through_the_button(self):
        cog = self.bot.get_cog('TimeGuessr')
        cog.fetch_rounds = AsyncMock(return_value=[self.PHOTO])
        cog.min_round_time, cog.warning_lead, cog.intro_delay = 1, 0.5, 0
        game = asyncio.create_task(self.run_slash('timeguessr', option('tempo', 2, kind=4)))

        await self.until(self.sent_with_components)
        button = self.sent_with_components()[0]['components'][0]['components'][0]
        self.assertEqual(button['label'], 'Palpitar')

        # Clicking it opens the form (response type 9).
        clicked = len(self.adapter.calls)
        self.bot._connection.parse_interaction_create(interaction_payload(
            3, {'custom_id': button['custom_id'], 'component_type': 2},
            message=FakeAdapter.message({}),
        ))
        await self.until(lambda: len(self.adapter.calls) > clicked)
        _, form = self.adapter.calls[clicked]
        self.assertEqual(form['type'], 9)
        country_id, year_id = (row['components'][0]['custom_id'] for row in form['data']['components'])

        submitted = len(self.adapter.calls)
        self.bot._connection.parse_interaction_create(interaction_payload(5, {
            'custom_id': form['data']['custom_id'],
            'components': [
                {'type': 1, 'components': [{'type': 4, 'custom_id': country_id, 'value': 'Japão'}]},
                {'type': 1, 'components': [{'type': 4, 'custom_id': year_id, 'value': '1980'}]},
            ],
        }))
        await self.until(lambda: len(self.adapter.calls) > submitted + 1)

        # Only the player sees the guess; the photo only says who guessed.
        _, reply = self.adapter.calls[submitted]
        self.assertEqual(reply['data']['flags'], EPHEMERAL)
        self.assertIn('Japão**, 1980', reply['data']['content'])
        _, progress = self.adapter.calls[submitted + 1]
        self.assertEqual(progress['embeds'][0]['footer']['text'], '✏️ Já palpitaram: gab')

        await asyncio.wait_for(game, timeout=10)
        answer = self.adapter.calls[-2][1]['embeds'][0]
        board = next(field['value'] for field in answer['fields'] if field['name'] == '🏆 Pontuação')
        self.assertIn('Japão, 1980 → **4.500** pts', board)
        # The button was turned off when the round ended.
        disabled = [payload for kind, payload in self.adapter.calls if kind == 'edit followup' and payload.get('components')]
        self.assertTrue(disabled[-1]['components'][0]['components'][0]['disabled'])


class PlayAutocompleteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.music = make_music()
        self.music.ytmusic_candidates = AsyncMock(return_value=[
            {'video_id': f'video{n:06d}', 'title': f'Song {n}', 'artists': ['A', 'B'], 'duration': 61}
            for n in range(15)
        ])

    async def suggest(self, text):
        return await Music.play_autocomplete(self.music, None, text)

    async def test_links_and_short_queries_get_no_suggestions(self):
        for text in ('', ' a ', 'https://youtu.be/dQw4w9WgXcQ', 'spotify:track:4uLU6hMCjMI75M1A2tKUQC'):
            with self.subTest(text=text):
                self.assertEqual(await self.suggest(text), [])
        self.music.ytmusic_candidates.assert_not_awaited()

    async def test_suggestions_are_capped_and_link_to_youtube(self):
        choices = await self.suggest('song')

        self.assertEqual(len(choices), 10)
        self.assertEqual(choices[0].name, 'Song 0 — A, B (1:01)')
        self.assertEqual(choices[0].value, 'https://www.youtube.com/watch?v=video000000')

    async def test_repeated_queries_are_searched_once(self):
        await self.suggest('Song')
        await self.suggest('song ')

        self.music.ytmusic_candidates.assert_awaited_once()

    async def test_slow_searches_answer_empty_but_finish_for_the_next_keystroke(self):
        release = asyncio.Event()

        async def slow_search(query, kind):
            await release.wait()
            return [{'video_id': 'dQw4w9WgXcQ', 'title': 'Slow', 'artists': [], 'duration': None}]

        self.music.ytmusic_candidates = slow_search
        with patch.object(music_module, 'AUTOCOMPLETE_TIMEOUT', 0.01):
            self.assertEqual(await self.suggest('slow'), [])
            release.set()
            choices = await self.suggest('slow')

        self.assertEqual([choice.name for choice in choices], ['Slow'])

    async def test_failed_searches_answer_empty_and_are_retried(self):
        self.music.ytmusic_candidates = AsyncMock(side_effect=[RuntimeError("down"), []])

        self.assertEqual(await self.suggest('song'), [])
        self.assertEqual(await self.suggest('song'), [])
        self.assertEqual(self.music.ytmusic_candidates.await_count, 2)

    async def test_cache_is_bounded(self):
        with patch.object(music_module, 'AUTOCOMPLETE_CACHE_SIZE', 3):
            for n in range(5):
                await self.suggest(f'song {n}')

        self.assertEqual(list(self.music.suggestions), ['song 2', 'song 3', 'song 4'])

    def test_long_names_are_shortened_but_keep_the_duration(self):
        name = Music.suggestion_name({'title': 'x' * 200, 'artists': ['Someone'], 'duration': 3600})

        self.assertEqual(len(name), CHOICE_NAME_LIMIT)
        self.assertTrue(name.endswith('… (1:00:00)'))


class MusicReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_controls_answer_when_the_bot_is_not_in_a_voice_channel(self):
        for command in (Music.stop, Music.skip, Music.leave, Music.pause):
            with self.subTest(command=command.name):
                ctx = make_ctx()
                await command.callback(make_music(), ctx)
                self.assertEqual(sent_text(ctx), "I'm not in a voice channel!")

    async def test_listeners_elsewhere_are_refused_privately(self):
        vc = make_voice_client(playing=True, channel=object())
        for command in (Music.stop, Music.skip, Music.leave, Music.pause, Music.loop_track):
            with self.subTest(command=command.name):
                music = make_music(current_urls={'123': 'url'})
                ctx = make_ctx(author=make_member(channel=object()), voice_client=vc)
                await command.callback(music, ctx)
                ctx.send.assert_awaited_once_with(NOT_IN_PLAYER_CHANNEL_MESSAGE, ephemeral=True)

    async def test_play_refuses_before_deferring(self):
        response = Mock(is_done=Mock(return_value=False), defer=AsyncMock())
        ctx = make_ctx(author=make_member())
        ctx.interaction = SimpleNamespace(response=response)

        await Music.play.callback(make_music(), ctx, query='song')

        ctx.send.assert_awaited_once_with("❌ You need to be in a voice channel!", ephemeral=True)
        response.defer.assert_not_awaited()

    async def test_play_defers_before_joining_the_channel(self):
        response = Mock(is_done=Mock(return_value=False), defer=AsyncMock())
        ctx = make_ctx()
        ctx.interaction = SimpleNamespace(response=response)
        music = make_music()
        music.ensure_voice_client = AsyncMock(side_effect=lambda ctx: response.defer.assert_awaited_once())
        music.search_youtube = AsyncMock(return_value=[])

        await Music.play.callback(music, ctx, query='song')

        music.ensure_voice_client.assert_awaited_once()
        self.assertIn("No results found", sent_text(ctx))


class DeferTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefix_commands_are_left_alone(self):
        await slash.defer(SimpleNamespace(interaction=None))
        await slash.defer(SimpleNamespace())

    async def test_answered_interactions_are_not_deferred_again(self):
        response = Mock(is_done=Mock(return_value=True), defer=AsyncMock())

        await slash.defer(SimpleNamespace(interaction=SimpleNamespace(response=response)))

        response.defer.assert_not_awaited()


class SyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {'DATA_DIR': tmp.name})
        env.start()
        self.addCleanup(env.stop)

    @staticmethod
    def make_bot(*names, application_id=1):
        commands_ = [Mock(to_dict=Mock(return_value={'name': name, 'description': 'd'})) for name in names]
        tree = Mock(get_commands=Mock(return_value=commands_), sync=AsyncMock(return_value=commands_))
        return SimpleNamespace(application_id=application_id, tree=tree)

    async def test_first_start_syncs(self):
        bot = self.make_bot('play')

        self.assertTrue(await slash.sync_commands(bot))
        bot.tree.sync.assert_awaited_once_with()

    async def test_restart_with_the_same_commands_skips_the_sync(self):
        await slash.sync_commands(self.make_bot('play', 'stop'))
        bot = self.make_bot('stop', 'play')

        self.assertFalse(await slash.sync_commands(bot))
        bot.tree.sync.assert_not_awaited()

    async def test_changed_commands_sync_again(self):
        await slash.sync_commands(self.make_bot('play'))
        bot = self.make_bot('play', 'stop')

        self.assertTrue(await slash.sync_commands(bot))

    async def test_force_always_syncs(self):
        await slash.sync_commands(self.make_bot('play'))
        bot = self.make_bot('play')

        self.assertTrue(await slash.sync_commands(bot, force=True))
        bot.tree.sync.assert_awaited_once()

    async def test_each_bot_account_is_tracked_separately(self):
        await slash.sync_commands(self.make_bot('play', application_id=1))
        bot = self.make_bot('play', application_id=2)

        self.assertTrue(await slash.sync_commands(bot))

    async def test_failed_sync_is_retried_next_start(self):
        bot = self.make_bot('play')
        bot.tree.sync.side_effect = discord.HTTPException(Mock(status=500, reason='x'), 'boom')
        with self.assertRaises(discord.HTTPException):
            await slash.sync_commands(bot)

        bot = self.make_bot('play')
        self.assertTrue(await slash.sync_commands(bot))

    async def test_fingerprint_covers_the_real_command_tree(self):
        env = patch.dict(os.environ, {'GEMINI_API_KEY': ''})
        env.start()
        self.addCleanup(env.stop)
        bot = await make_bot()
        try:
            before = slash.commands_fingerprint(bot.tree)
            bot.tree.get_command('play').description = 'Something else'
            self.assertNotEqual(slash.commands_fingerprint(bot.tree), before)
        finally:
            await close_bot(bot)


class SmallHelperTests(unittest.TestCase):
    def test_currency_suggestions_match_codes_and_names(self):
        self.assertEqual([c.value for c in currency_choices('usd')], ['USD'])
        self.assertEqual([c.value for c in currency_choices('Real')], ['BRL'])
        self.assertIn('ARS', [c.value for c in currency_choices('peso')])

    def test_currency_suggestions_fit_discords_limit(self):
        choices = currency_choices('')
        self.assertEqual(len(choices), MAX_CHOICES)
        self.assertTrue(all(len(c.name) <= 100 for c in choices))

    def test_quote_marks_every_line_and_shortens_long_questions(self):
        self.assertEqual(quote("a\nb"), "> a\n> b")
        quoted = quote("x" * 1000)
        self.assertEqual(len(quoted), len("> ") + QUOTE_LIMIT)
        self.assertTrue(quoted.endswith("…"))


if __name__ == '__main__':
    unittest.main()
