import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from cogs.music import Music
from tests.music_helpers import (
    make_ctx,
    make_member,
    make_music,
    make_voice_client,
    sent_text,
)

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
OTHER_URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
OTHER_ENTRY = {'url': OTHER_URL, 'title': 'Other song'}


def queued_urls(music):
    return [entry['url'] for entry in music.queue['123']]


class PausedPlayerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel = object()
        self.vc = make_voice_client(paused=True, channel=self.channel)
        self.ctx = make_ctx(
            author=make_member(channel=self.channel),
            voice_client=self.vc,
        )
        self.music = make_music()
        self.music.queue['123'] = [OTHER_ENTRY]
        self.music.ensure_voice_client = AsyncMock(return_value=self.vc)
        self.music.play_song = AsyncMock()

    async def test_stop_ends_a_paused_track(self):
        await Music.stop.callback(self.music, self.ctx)

        self.vc.stop.assert_called_once()
        self.assertEqual(self.music.queue['123'], [])

    async def test_skip_ends_a_paused_track(self):
        await Music.skip.callback(self.music, self.ctx)

        self.vc.stop.assert_called_once()

    async def test_play_url_while_paused_queues_instead_of_replacing(self):
        with patch('cogs.music.yt_dlp.YoutubeDL'):
            await Music.play.callback(self.music, self.ctx, query=VIDEO_URL)

        self.music.play_song.assert_not_awaited()
        self.assertEqual(queued_urls(self.music), [OTHER_URL, VIDEO_URL])

    async def test_search_selection_while_paused_queues(self):
        self.music.search_youtube = AsyncMock(return_value=[
            {'title': 'Song', 'webpage_url': VIDEO_URL},
        ])
        self.music.bot.wait_for = AsyncMock(return_value=type(
            'Message', (), {'content': '1'}
        )())

        await Music.play.callback(self.music, self.ctx, query="some song")

        self.music.play_song.assert_not_awaited()
        self.assertEqual(queued_urls(self.music), [OTHER_URL, VIDEO_URL])


class VoiceChannelPermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot_channel = object()
        self.vc = make_voice_client(playing=True, channel=self.bot_channel)
        self.music = make_music()
        self.music.queue['123'] = [OTHER_ENTRY]
        self.music.current_urls['123'] = VIDEO_URL
        self.music.ensure_voice_client = AsyncMock(return_value=self.vc)
        self.music.play_song = AsyncMock()

    def ctx_for(self, member):
        return make_ctx(author=member, voice_client=self.vc)

    async def test_members_outside_the_bot_channel_cannot_control_playback(self):
        outsider = make_member(channel=object())
        for command in (Music.stop, Music.skip, Music.pause, Music.leave, Music.loop_track):
            with self.subTest(command=command.name):
                ctx = self.ctx_for(outsider)
                self.music.disconnect_voice_client = AsyncMock()

                await command.callback(self.music, ctx)

                self.vc.stop.assert_not_called()
                self.vc.pause.assert_not_called()
                self.music.disconnect_voice_client.assert_not_awaited()
                self.assertEqual(self.music.queue['123'], [OTHER_ENTRY])
                self.assertFalse(self.music.loop_enabled.get('123'))
                self.assertIn("voice channel", sent_text(ctx))

    async def test_members_without_voice_cannot_stop_playback(self):
        ctx = self.ctx_for(make_member())

        await Music.stop.callback(self.music, ctx)

        self.vc.stop.assert_not_called()

    async def test_members_in_the_bot_channel_can_stop_playback(self):
        ctx = self.ctx_for(make_member(channel=self.bot_channel))

        await Music.stop.callback(self.music, ctx)

        self.vc.stop.assert_called_once()

    async def test_moderators_can_stop_playback_from_anywhere(self):
        ctx = self.ctx_for(make_member(channel=object(), move_members=True))

        await Music.stop.callback(self.music, ctx)

        self.vc.stop.assert_called_once()

    async def test_play_from_another_channel_does_not_move_a_busy_bot(self):
        ctx = self.ctx_for(make_member(channel=object()))

        await Music.play.callback(self.music, ctx, query=VIDEO_URL)

        self.music.ensure_voice_client.assert_not_awaited()
        self.music.play_song.assert_not_awaited()
        self.assertEqual(self.music.queue['123'], [OTHER_ENTRY])
        self.assertIn("voice channel", sent_text(ctx))

    async def test_play_from_another_channel_may_move_an_idle_bot(self):
        idle_vc = make_voice_client(channel=self.bot_channel)
        self.music.queue['123'] = []
        self.music.ensure_voice_client.return_value = idle_vc
        ctx = make_ctx(author=make_member(channel=object()), voice_client=idle_vc)

        await Music.play.callback(self.music, ctx, query=VIDEO_URL)

        self.music.ensure_voice_client.assert_awaited_once()
        self.music.play_song.assert_awaited_once_with(ctx, VIDEO_URL)


class PlaybackSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_play_commands_start_only_one_track(self):
        state = {'playing': False}
        vc = make_voice_client()
        vc.is_playing.side_effect = lambda: state['playing']
        channel = object()
        vc.channel = channel

        music = make_music()
        music.ensure_voice_client = AsyncMock(return_value=vc)

        async def start_track(ctx, url):
            await asyncio.sleep(0)  # resolving a stream yields to the loop
            state['playing'] = True

        music.play_song = AsyncMock(side_effect=start_track)
        ctx = make_ctx(author=make_member(channel=channel), voice_client=vc)

        with patch('cogs.music.yt_dlp.YoutubeDL'):
            await asyncio.gather(
                Music.play.callback(music, ctx, query=VIDEO_URL),
                Music.play.callback(music, ctx, query=OTHER_URL),
            )

        music.play_song.assert_awaited_once()
        self.assertEqual(len(music.queue['123']), 1)

    async def test_play_next_does_not_interrupt_a_track_started_meanwhile(self):
        vc = make_voice_client(playing=True)
        music = make_music()
        music.voice_clients['123'] = vc
        music.queue['123'] = [OTHER_ENTRY]
        music.play_song = AsyncMock()

        await music.play_next(make_ctx(voice_client=vc))

        music.play_song.assert_not_awaited()
        self.assertEqual(music.queue['123'], [OTHER_ENTRY])

    async def test_leave_does_not_start_the_next_track_while_disconnecting(self):
        channel = object()
        vc = make_voice_client(playing=True, channel=channel)
        music = make_music()
        music.voice_clients['123'] = vc
        music.queue['123'] = [OTHER_ENTRY]
        music.play_song = AsyncMock()
        ctx = make_ctx(author=make_member(channel=channel), voice_client=vc)

        async def disconnect(guild_id, guild=None):
            # Disconnecting ends the track, which fires the `after` callback.
            vc.is_playing.return_value = False
            await music.play_next(ctx)

        music.disconnect_voice_client = AsyncMock(side_effect=disconnect)

        await Music.leave.callback(music, ctx)

        music.play_song.assert_not_awaited()

    async def test_track_end_callback_does_not_wait_for_the_next_track(self):
        release = asyncio.Event()
        started = asyncio.Event()

        async def slow_play_next(ctx):
            started.set()
            await release.wait()

        music = make_music()
        music.bot.loop = asyncio.get_running_loop()
        music.play_next = slow_play_next

        try:
            # discord.py calls `after` from its audio thread.
            await asyncio.wait_for(
                asyncio.to_thread(music.after_playback, make_ctx(), None),
                timeout=1,
            )
            await asyncio.wait_for(started.wait(), timeout=1)
        finally:
            release.set()

    async def test_track_end_callback_logs_playback_errors(self):
        music = make_music()
        music.bot.loop = asyncio.get_running_loop()
        music.play_next = AsyncMock()

        with self.assertLogs('cogs.music', level='ERROR') as logs:
            music.after_playback(make_ctx(), RuntimeError("ffmpeg died"))
            await asyncio.sleep(0)

        self.assertIn("ffmpeg died", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
