import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from cogs.music import Music


class SpotifyReferenceTests(unittest.TestCase):
    TRACK_ID = "11dFghVXANMlKmJXsNCbNl"

    def test_recognizes_spotify_urls_and_uris(self):
        self.assertTrue(Music.is_spotify_reference(
            f"https://open.spotify.com/track/{self.TRACK_ID}"
        ))
        self.assertTrue(Music.is_spotify_reference(f"spotify:track:{self.TRACK_ID}"))
        self.assertTrue(Music.is_spotify_reference("https://spotify.link/example"))

    def test_rejects_spoofed_spotify_hostname(self):
        self.assertFalse(Music.is_spotify_reference(
            f"https://open.spotify.com.evil.example/track/{self.TRACK_ID}"
        ))

    def test_normalizes_regular_and_localized_track_urls(self):
        expected = f"https://open.spotify.com/track/{self.TRACK_ID}"
        self.assertEqual(
            Music.normalize_spotify_track_url(
                f"https://open.spotify.com/track/{self.TRACK_ID}?si=abc"
            ),
            expected,
        )
        self.assertEqual(
            Music.normalize_spotify_track_url(
                f"https://open.spotify.com/intl-pt/track/{self.TRACK_ID}"
            ),
            expected,
        )
        self.assertEqual(
            Music.normalize_spotify_track_url(f"spotify:track:{self.TRACK_ID}"),
            expected,
        )

    def test_does_not_treat_album_as_track(self):
        self.assertIsNone(Music.normalize_spotify_track_url(
            f"https://open.spotify.com/album/{self.TRACK_ID}"
        ))

    def test_builds_search_query_with_track_and_artist(self):
        page_title = (
            "Cut To The Feeling - song and lyrics by Carly Rae Jepsen | Spotify"
        )
        self.assertEqual(
            Music.spotify_query_from_page_title(page_title),
            "Cut To The Feeling Carly Rae Jepsen",
        )

    def test_ignores_generic_page_title(self):
        self.assertIsNone(Music.spotify_query_from_page_title("Spotify - Web Player"))


class SpotifyPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def test_spotify_track_plays_first_youtube_match_without_prompt(self):
        music = Music.__new__(Music)
        music.cookies_file = None
        music.queue = {}
        music.ensure_voice_client = AsyncMock()
        music.handle_spotify_url = AsyncMock(return_value="Track Artist")
        music.search_youtube = AsyncMock(return_value=[{
            'title': 'Track — Artist',
            'webpage_url': 'https://www.youtube.com/watch?v=abcdefghijk',
        }])
        music.play_song = AsyncMock()

        voice_client = Mock()
        voice_client.is_playing.return_value = False
        voice_client.is_paused.return_value = False
        music.ensure_voice_client.return_value = voice_client

        ctx = SimpleNamespace(
            author=SimpleNamespace(voice=object()),
            guild=SimpleNamespace(id=123),
            send=AsyncMock(),
        )

        await Music.play.callback(
            music,
            ctx,
            query="https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl",
        )

        music.search_youtube.assert_awaited_once_with("Track Artist")
        music.play_song.assert_awaited_once_with(
            ctx,
            'https://www.youtube.com/watch?v=abcdefghijk',
        )


if __name__ == "__main__":
    unittest.main()
