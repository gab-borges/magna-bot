import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from cogs.music import Music
from tests.music_helpers import make_ctx, make_music, make_voice_client, sent_text

VIDEO_ID = "dQw4w9WgXcQ"
CANONICAL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


class YouTubeUrlNormalizationTests(unittest.TestCase):
    def test_canonicalizes_supported_youtube_urls(self):
        urls = [
            f"https://www.youtube.com/watch?v={VIDEO_ID}",
            f"https://youtube.com/watch?feature=share&v={VIDEO_ID}",
            f"http://m.youtube.com/watch?v={VIDEO_ID}&t=30",
            f"https://music.youtube.com/watch?v={VIDEO_ID}&list=RD",
            f"https://youtu.be/{VIDEO_ID}?si=abc",
            f"https://www.youtube.com/shorts/{VIDEO_ID}",
            f"https://www.youtube.com/embed/{VIDEO_ID}",
            f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(Music.normalize_youtube_url(url), CANONICAL)

    def test_rejects_non_youtube_hosts(self):
        urls = [
            "http://127.0.0.1:8080/audio.mp3",
            "http://localhost/watch?v=dQw4w9WgXcQ",
            f"https://youtube.com.evil.example/watch?v={VIDEO_ID}",
            f"https://evil.example/?next=youtube.com/watch?v={VIDEO_ID}",
            f"https://notyoutube.com/watch?v={VIDEO_ID}",
            f"ftp://youtube.com/watch?v={VIDEO_ID}",
            f"file:///watch?v={VIDEO_ID}",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(Music.normalize_youtube_url(url))

    def test_rejects_youtube_urls_without_a_video_id(self):
        urls = [
            "https://www.youtube.com/",
            "https://www.youtube.com/channel/UC1234567890",
            "https://www.youtube.com/watch?v=short",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(Music.normalize_youtube_url(url))


class PlayCommandUrlTests(unittest.IsolatedAsyncioTestCase):
    def make_music(self, voice_client):
        music = make_music()
        music.ensure_voice_client = AsyncMock(return_value=voice_client)
        music.play_song = AsyncMock()
        music.search_youtube = AsyncMock(return_value=[])
        return music

    async def test_rejects_internal_url_before_any_network_access(self):
        music = self.make_music(make_voice_client())
        ctx = make_ctx()

        with patch('cogs.music.yt_dlp.YoutubeDL') as youtube_dl:
            await Music.play.callback(music, ctx, query="http://127.0.0.1:8080/audio.mp3")

        youtube_dl.assert_not_called()
        music.play_song.assert_not_awaited()
        music.search_youtube.assert_not_awaited()
        self.assertIn("YouTube", sent_text(ctx))

    async def test_plays_canonical_url_for_youtube_short_link(self):
        music = self.make_music(make_voice_client())
        ctx = make_ctx()

        await Music.play.callback(music, ctx, query=f"https://youtu.be/{VIDEO_ID}?t=3")

        music.play_song.assert_awaited_once_with(ctx, CANONICAL)


class PlaySongUrlTests(unittest.IsolatedAsyncioTestCase):
    async def test_does_not_extract_non_youtube_urls(self):
        music = make_music()
        ctx = make_ctx()

        with patch('cogs.music.yt_dlp.YoutubeDL') as youtube_dl:
            await music.play_song(ctx, "http://127.0.0.1:8080/audio.mp3")

        youtube_dl.assert_not_called()


class StreamUrlValidationTests(unittest.TestCase):
    def test_accepts_local_file_only_inside_cache_dir(self):
        with TemporaryDirectory() as root:
            cache_dir = Path(root) / 'cache'
            cache_dir.mkdir()
            cached = cache_dir / f'{VIDEO_ID}.mp3'
            cached.write_bytes(b'')
            outside = Path(root) / 'secret.txt'
            outside.write_text('x')

            music = make_music(cache_dir=cache_dir)

            self.assertTrue(music.is_valid_stream_url(str(cached)))
            self.assertFalse(music.is_valid_stream_url(str(outside)))
            self.assertFalse(music.is_valid_stream_url(str(cache_dir / '..' / 'secret.txt')))

    def test_rejects_non_http_stream_schemes(self):
        music = make_music()
        self.assertFalse(music.is_valid_stream_url("file:///etc/passwd"))
        self.assertTrue(music.is_valid_stream_url("https://rr1.googlevideo.com/videoplayback?x=1"))


if __name__ == "__main__":
    unittest.main()
