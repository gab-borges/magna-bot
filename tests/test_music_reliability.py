import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from cogs.music import MAX_DOWNLOADED_TRACKS, Music
from tests.music_helpers import (
    make_ctx,
    make_member,
    make_music,
    make_voice_client,
    sent_text,
)

VIDEO_ID = "dQw4w9WgXcQ"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
OTHER_URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
THIRD_URL = "https://www.youtube.com/watch?v=bbbbbbbbbbb"


class TempCacheDirMixin:
    def make_cache_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)


class QueuedTitleLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_title_lookup_still_reports_the_track_as_queued(self):
        channel = object()
        vc = make_voice_client(playing=True, channel=channel)
        music = make_music()
        music.ensure_voice_client = AsyncMock(return_value=vc)
        music.extract_info = AsyncMock(side_effect=RuntimeError("yt-dlp failed"))
        ctx = make_ctx(author=make_member(channel=channel), voice_client=vc)

        await Music.play.callback(music, ctx, query=VIDEO_URL)

        self.assertEqual(music.queue['123'], [{'url': VIDEO_URL, 'title': VIDEO_URL}])
        self.assertIn("Added to queue", sent_text(ctx))
        self.assertNotIn("Unable", sent_text(ctx))


class QueueAdvanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.music = make_music()
        self.music.voice_clients['123'] = make_voice_client()
        self.ctx = make_ctx()

    async def test_unplayable_track_does_not_stall_the_queue(self):
        self.music.queue['123'] = [
            {'url': OTHER_URL, 'title': 'Broken'},
            {'url': THIRD_URL, 'title': 'Works'},
        ]
        self.music.play_song = AsyncMock(side_effect=[False, True])

        await self.music.play_next(self.ctx)

        self.assertEqual(
            [call.args[1] for call in self.music.play_song.await_args_list],
            [OTHER_URL, THIRD_URL],
        )
        self.assertEqual(self.music.queue['123'], [])

    async def test_failed_loop_replay_moves_on_to_the_queue(self):
        self.music.loop_enabled['123'] = True
        self.music.current_urls['123'] = VIDEO_URL
        self.music.queue['123'] = [{'url': OTHER_URL, 'title': 'Next'}]
        self.music.play_song = AsyncMock(side_effect=[False, True])

        await self.music.play_next(self.ctx)

        self.assertEqual(
            [call.args[1] for call in self.music.play_song.await_args_list],
            [VIDEO_URL, OTHER_URL],
        )

    async def test_does_not_drain_the_queue_without_a_voice_connection(self):
        self.music.voice_clients['123'].is_connected.return_value = False
        self.music.queue['123'] = [{'url': OTHER_URL, 'title': 'Next'}]
        self.music.play_song = AsyncMock()

        await self.music.play_next(self.ctx)

        self.music.play_song.assert_not_awaited()
        self.assertEqual(len(self.music.queue['123']), 1)


class CachedStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_stream_plays_with_its_own_title(self):
        music = make_music()
        vc = make_voice_client()
        music.voice_clients['123'] = vc
        music.current_songs['123'] = 'Previous song'
        await music.cache_stream(VIDEO_ID, 'https://media.example/audio', 'Cached song')
        ctx = make_ctx(voice_client=vc)

        with patch('cogs.music.discord.FFmpegOpusAudio'):
            self.assertTrue(await music.play_song(ctx, VIDEO_URL))

        embed = ctx.send.await_args.kwargs['embed']
        self.assertIn('Cached song', embed.description)
        self.assertEqual(music.current_songs['123'], 'Cached song')

    async def test_stream_urls_are_dropped_before_they_expire(self):
        music = make_music()
        soon = int(time.time()) + 60
        await music.cache_stream(
            VIDEO_ID, f'https://media.example/audio?expire={soon}&id=1', 'Song'
        )

        self.assertIsNone(await music.get_cached_stream(VIDEO_ID))

    async def test_stream_urls_are_reused_while_far_from_expiry(self):
        music = make_music()
        later = int(time.time()) + 6 * 3600
        url = f'https://media.example/audio?expire={later}&id=1'
        await music.cache_stream(VIDEO_ID, url, 'Song', 'Artist', 245)

        self.assertEqual(
            await music.get_cached_stream(VIDEO_ID),
            {'url': url, 'title': 'Song', 'artist': 'Artist', 'duration': 245},
        )


class DownloadedAudioTests(TempCacheDirMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.music = make_music(cache_dir=self.make_cache_dir())

    async def test_reuses_an_existing_download(self):
        existing = self.music.cache_dir / f"{VIDEO_ID}.mp3"
        existing.write_bytes(b"audio")
        self.music.downloaded_tracks[VIDEO_ID] = {'title': 'Downloaded song'}
        self.music.extract_info = AsyncMock()

        stream = await self.music.get_downloaded_stream(VIDEO_ID, VIDEO_URL)

        self.music.extract_info.assert_not_awaited()
        self.assertEqual(stream['url'], str(existing))
        self.assertEqual(stream['title'], 'Downloaded song')
        self.assertTrue(stream['is_local'])

    async def test_downloads_when_there_is_no_copy(self):
        async def download(opts, url, download=False):
            (self.music.cache_dir / f"{VIDEO_ID}.mp3").write_bytes(b"audio")
            return {'title': 'Fresh song'}

        self.music.extract_info = AsyncMock(side_effect=download)

        stream = await self.music.get_downloaded_stream(VIDEO_ID, VIDEO_URL)

        self.music.extract_info.assert_awaited_once()
        self.assertEqual(stream['title'], 'Fresh song')

    def test_ignores_partial_downloads(self):
        (self.music.cache_dir / f"{VIDEO_ID}.webm.part").write_bytes(b"partial")

        self.assertIsNone(self.music.find_downloaded_audio(VIDEO_ID))

    def test_keeps_only_the_most_recent_downloads(self):
        now = time.time()
        paths = []
        for i in range(MAX_DOWNLOADED_TRACKS + 2):
            path = self.music.cache_dir / f"track{i:07d}.mp3"
            path.write_bytes(b"audio")
            os.utime(path, (now + i, now + i))
            paths.append(path)

        self.music.prune_downloaded_audio()

        remaining = sorted(self.music.cache_dir.iterdir())
        self.assertEqual(remaining, paths[2:])


class CookiesFileTests(TempCacheDirMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = self.make_cache_dir()
        self.operator_file = root / 'youtube_cookies.txt'
        self.operator_file.write_text('operator cookies')
        private_dir = root / '.private'
        private_dir.mkdir()
        self.music = make_music(
            cookies_file=self.operator_file,
            generated_cookies_file=private_dir / 'youtube_cookies.txt',
            ydl_opts={'cookiefile': str(self.operator_file)},
        )

    async def test_env_cookies_do_not_overwrite_the_operator_file(self):
        with patch.dict(os.environ, {'YOUTUBE_COOKIES': 'env cookies'}):
            self.assertTrue(await self.music.update_cookies())

        self.assertEqual(self.operator_file.read_text(), 'operator cookies')
        generated = self.music.generated_cookies_file
        self.assertEqual(generated.read_text(), 'env cookies')
        self.assertEqual(generated.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.music.ydl_opts['cookiefile'], str(generated))

    def test_unloading_removes_only_the_generated_file(self):
        self.music.generated_cookies_file.write_text('env cookies')
        self.music.voice_diagnostic_handler = None

        self.music.cog_unload()
        del self.music

        self.assertTrue(self.operator_file.exists())


class ResolvingTrackTests(unittest.IsolatedAsyncioTestCase):
    async def test_play_from_another_channel_waits_while_a_track_resolves(self):
        bot_channel = object()
        idle_vc = make_voice_client(channel=bot_channel)
        music = make_music()
        music.ensure_voice_client = AsyncMock(return_value=idle_vc)
        music.play_song = AsyncMock()
        ctx = make_ctx(author=make_member(channel=object()), voice_client=idle_vc)

        async with music.playback_lock('123'):
            # Joining the busy lock instead of refusing would wait forever.
            await asyncio.wait_for(
                Music.play.callback(music, ctx, query=VIDEO_URL), timeout=1
            )

        music.ensure_voice_client.assert_not_awaited()
        self.assertIn("voice channel", sent_text(ctx))


if __name__ == "__main__":
    unittest.main()


class YouTubeFallbackTests(TempCacheDirMixin, unittest.IsolatedAsyncioTestCase):
    async def test_search_retries_with_cookies_after_the_bot_check(self):
        music = make_music()
        music.update_cookies = AsyncMock(return_value=True)
        music.extract_info = AsyncMock(side_effect=[
            RuntimeError("Sign in to confirm you're not a bot"),
            {'entries': [{'id': VIDEO_ID, 'title': 'Song', 'url': VIDEO_URL}]},
        ])

        results = await music.search_youtube("song")

        music.update_cookies.assert_awaited_once()
        self.assertEqual([r['webpage_url'] for r in results], [VIDEO_URL])

    async def test_search_failure_returns_no_results(self):
        music = make_music()
        music.extract_info = AsyncMock(side_effect=RuntimeError("offline"))

        self.assertEqual(await music.search_youtube("song"), [])

    async def test_playback_falls_back_to_downloading_the_track(self):
        music = make_music(cache_dir=self.make_cache_dir())
        vc = make_voice_client()
        music.voice_clients['123'] = vc
        music.extract_info = AsyncMock(side_effect=RuntimeError("yt-dlp failed"))
        downloaded = music.cache_dir / f"{VIDEO_ID}.mp3"
        downloaded.write_bytes(b"audio")
        music.downloaded_tracks[VIDEO_ID] = {'title': 'Downloaded song'}

        with patch('cogs.music.discord.FFmpegOpusAudio') as audio:
            self.assertTrue(await music.play_song(make_ctx(voice_client=vc), VIDEO_URL))

        audio.assert_called_once()
        self.assertEqual(audio.call_args.args[0], str(downloaded))
        vc.play.assert_called_once()


class TrackDetailsTests(unittest.TestCase):
    def test_reads_artist_and_duration(self):
        from cogs.music import track_details, format_duration
        details = track_details({'title': 'Song', 'uploader': 'Band - Topic', 'duration': 245})
        self.assertEqual(details, {'title': 'Song', 'artist': 'Band', 'duration': 245})
        self.assertEqual(format_duration(245), '4:05')
        self.assertEqual(format_duration(3725), '1:02:05')
        self.assertIsNone(format_duration(None))
