import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cogs.music import INACTIVITY_MESSAGES, Music
from tests.music_helpers import make_ctx, make_music, make_voice_client

BOT = SimpleNamespace(bot=True)
LISTENER = SimpleNamespace(bot=False)
NO_DELAY = {'idle': 0, 'alone': 0}


def voice_channel(*members):
    return SimpleNamespace(members=list(members))


class InactivityTimerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel = voice_channel(BOT, LISTENER)
        self.vc = make_voice_client(channel=self.channel)
        self.ctx = make_ctx(voice_client=self.vc)
        self.music = make_music()
        self.music.voice_clients['123'] = self.vc

    def timer_reason(self):
        timer = self.music.inactivity_timers.get('123')
        return timer[0] if timer else None

    async def test_empty_queue_starts_the_idle_timer(self):
        await self.music.play_next(self.ctx)

        self.assertEqual(self.timer_reason(), 'idle')

    async def test_starting_a_track_cancels_the_idle_timer(self):
        self.music.refresh_inactivity_timer(self.ctx.guild)
        task = self.music.inactivity_timers['123'][1]

        async def start_playing(ctx, url):
            self.vc.is_playing.return_value = True
            return True

        self.music.play_song = AsyncMock(side_effect=start_playing)
        await self.music.play_or_enqueue(self.ctx, self.vc, "https://youtu.be/dQw4w9WgXcQ")
        await asyncio.sleep(0)

        self.assertIsNone(self.timer_reason())
        self.assertTrue(task.cancelled())

    async def test_queued_tracks_keep_the_bot_from_idling(self):
        self.music.queue['123'] = [{'url': None, 'title': 'Next'}]

        self.music.refresh_inactivity_timer(self.ctx.guild)

        self.assertIsNone(self.timer_reason())

    async def test_being_alone_starts_the_alone_timer_even_while_playing(self):
        self.vc.is_playing.return_value = True
        self.channel.members.remove(LISTENER)

        self.music.refresh_inactivity_timer(self.ctx.guild)

        self.assertEqual(self.timer_reason(), 'alone')

    async def test_a_running_countdown_is_not_restarted(self):
        self.music.refresh_inactivity_timer(self.ctx.guild)
        task = self.music.inactivity_timers['123'][1]

        self.music.refresh_inactivity_timer(self.ctx.guild)

        self.assertIs(self.music.inactivity_timers['123'][1], task)

    async def test_leaves_and_announces_when_the_timer_runs_out(self):
        text_channel = SimpleNamespace(send=AsyncMock())
        self.music.announce_channels['123'] = text_channel
        self.music.queue['123'] = []
        self.music.current_songs['123'] = 'Old song'

        with patch('cogs.music.INACTIVITY_DELAYS', NO_DELAY):
            self.music.refresh_inactivity_timer(self.ctx.guild)
            await self.music.inactivity_timers['123'][1]

        self.vc.disconnect.assert_awaited_once()
        text_channel.send.assert_awaited_once_with(INACTIVITY_MESSAGES['idle'])
        self.assertNotIn('123', self.music.voice_clients)
        self.assertNotIn('123', self.music.current_songs)
        self.assertNotIn('123', self.music.inactivity_timers)

    async def test_stays_when_playback_resumed_before_the_timer_ran_out(self):
        with patch('cogs.music.INACTIVITY_DELAYS', NO_DELAY):
            self.music.refresh_inactivity_timer(self.ctx.guild)
            task = self.music.inactivity_timers['123'][1]
            self.vc.is_playing.return_value = True
            await task

        self.vc.disconnect.assert_not_awaited()

    async def test_disconnecting_cancels_the_timer(self):
        self.music.refresh_inactivity_timer(self.ctx.guild)
        task = self.music.inactivity_timers['123'][1]

        await self.music.disconnect_voice_client('123', self.ctx.guild)
        await asyncio.sleep(0)

        self.assertTrue(task.cancelled())
        self.assertNotIn('123', self.music.inactivity_timers)


class VoiceStateUpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel = voice_channel(BOT)
        self.vc = make_voice_client(channel=self.channel)
        self.guild = SimpleNamespace(id=123, voice_client=self.vc)
        self.music = make_music()
        self.music.bot.user = SimpleNamespace(id=999)
        self.music.refresh_inactivity_timer = Mock()

    def member(self, member_id=1):
        return SimpleNamespace(id=member_id, guild=self.guild)

    async def test_leaving_the_bot_channel_refreshes_the_timer(self):
        before = SimpleNamespace(channel=self.channel)
        after = SimpleNamespace(channel=None)

        await self.music.on_voice_state_update(self.member(), before, after)

        self.music.refresh_inactivity_timer.assert_called_once_with(self.guild)

    async def test_mute_and_deafen_are_ignored(self):
        state = SimpleNamespace(channel=self.channel)

        await self.music.on_voice_state_update(self.member(), state, state)

        self.music.refresh_inactivity_timer.assert_not_called()

    async def test_moves_between_other_channels_are_ignored(self):
        before = SimpleNamespace(channel=voice_channel())
        after = SimpleNamespace(channel=voice_channel())

        await self.music.on_voice_state_update(self.member(), before, after)

        self.music.refresh_inactivity_timer.assert_not_called()


class NowPlayingTests(unittest.IsolatedAsyncioTestCase):
    async def test_now_playing_is_cleared_when_the_queue_runs_out(self):
        vc = make_voice_client(channel=voice_channel(BOT, LISTENER))
        music = make_music()
        music.voice_clients['123'] = vc
        music.current_songs['123'] = 'Finished song'

        await music.play_next(make_ctx(voice_client=vc))

        self.assertNotIn('123', music.current_songs)

    async def test_now_playing_is_kept_when_a_track_started_meanwhile(self):
        vc = make_voice_client(playing=True, channel=voice_channel(BOT, LISTENER))
        music = make_music()
        music.voice_clients['123'] = vc
        music.current_songs['123'] = 'Current song'

        await music.play_next(make_ctx(voice_client=vc))

        self.assertEqual(music.current_songs['123'], 'Current song')

    async def test_leave_clears_now_playing(self):
        vc = make_voice_client(playing=True, channel=voice_channel(BOT, LISTENER))
        music = make_music()
        music.voice_clients['123'] = vc
        music.current_songs['123'] = 'Current song'
        ctx = make_ctx(voice_client=vc)
        ctx.author.guild_permissions.move_members = True

        await Music.leave.callback(music, ctx)

        self.assertNotIn('123', music.current_songs)


if __name__ == "__main__":
    unittest.main()
