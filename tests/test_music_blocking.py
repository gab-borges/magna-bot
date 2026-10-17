import asyncio
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from cogs.music import Music
from tests.music_helpers import make_ctx, make_music, make_voice_client

VIDEO_ID = "dQw4w9WgXcQ"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


class SlowCall:
    """A blocking stand-in for yt-dlp.

    It waits for the event loop to set `released`. When the call runs on the
    loop thread the loop can't do that, so the wait times out instead.
    """

    def __init__(self, result=None):
        self.released = threading.Event()
        self.result = result
        self.freed_by_loop = []

    def __call__(self, *args, **kwargs):
        self.freed_by_loop.append(self.released.wait(timeout=0.5))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def release_soon(self):
        await asyncio.sleep(0.01)
        self.released.set()


def fake_youtube_dl(slow_call):
    class FakeYoutubeDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            return slow_call(url, download=download)

    return FakeYoutubeDL


class EventLoopResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.music = make_music()

    async def run_alongside(self, slow_call, coro):
        await asyncio.gather(coro, slow_call.release_soon())
        self.assertTrue(slow_call.freed_by_loop, "the slow call never ran")
        self.assertTrue(all(slow_call.freed_by_loop), "the event loop was blocked")

    async def test_search_does_not_block_the_loop(self):
        slow = SlowCall(RuntimeError("offline"))
        with patch('cogs.music.yt_dlp.YoutubeDL', fake_youtube_dl(slow)):
            await self.run_alongside(slow, self.music.search_youtube("song"))

    async def test_stream_extraction_does_not_block_the_loop(self):
        slow = SlowCall({'url': 'https://media.example/audio', 'title': 'Song'})
        vc = make_voice_client()
        self.music.voice_clients['123'] = vc
        ctx = make_ctx(voice_client=vc)
        with patch('cogs.music.yt_dlp.YoutubeDL', fake_youtube_dl(slow)), \
                patch('cogs.music.discord.FFmpegOpusAudio'):
            await self.run_alongside(slow, self.music.play_song(ctx, VIDEO_URL))

        vc.play.assert_called_once()


class QueueTitleTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_lists_stored_titles_without_extracting_again(self):
        music = make_music()
        music.queue['123'] = [
            {'url': VIDEO_URL, 'title': 'First song'},
            {'url': VIDEO_URL, 'title': 'Second song'},
        ]
        ctx = make_ctx()

        with patch('cogs.music.yt_dlp.YoutubeDL') as youtube_dl:
            await Music.queue.callback(music, ctx)

        youtube_dl.assert_not_called()
        embed = ctx.send.await_args.kwargs['embed']
        self.assertEqual(
            [field.value for field in embed.fields],
            ['First song', 'Second song'],
        )

    async def test_queued_url_keeps_the_title_found_when_it_was_added(self):
        channel = object()
        vc = make_voice_client(playing=True, channel=channel)
        music = make_music()
        music.ensure_voice_client = AsyncMock(return_value=vc)
        ctx = make_ctx(voice_client=vc)
        ctx.author.voice.channel = channel

        slow = SlowCall({'title': 'Queued song'})
        slow.released.set()
        with patch('cogs.music.yt_dlp.YoutubeDL', fake_youtube_dl(slow)):
            await Music.play.callback(music, ctx, busca=VIDEO_URL)

        self.assertEqual(music.queue['123'], [{'url': VIDEO_URL, 'title': 'Queued song'}])

    async def test_play_next_plays_the_queued_url(self):
        music = make_music()
        music.voice_clients['123'] = make_voice_client()
        music.queue['123'] = [{'url': VIDEO_URL, 'title': 'Song'}]
        music.play_song = AsyncMock()
        ctx = make_ctx()

        await music.play_next(ctx)

        music.play_song.assert_awaited_once_with(ctx, VIDEO_URL)


class StreamCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_caching_a_stream_drops_expired_entries(self):
        music = make_music()
        music.stream_cache['old'] = {
            'url': 'https://media.example/old',
            'timestamp': time.time() - music.cache_timeout - 1,
        }

        await music.cache_stream('new', 'https://media.example/new')

        self.assertEqual(list(music.stream_cache), ['new'])


if __name__ == "__main__":
    unittest.main()
