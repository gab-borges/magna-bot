import unittest
import urllib.parse

import aiohttp
from unittest.mock import AsyncMock, patch

from cogs.music import Music
from tests.music_helpers import make_ctx, make_music, make_voice_client

TRACK_ID = "11dFghVXANMlKmJXsNCbNl"
TRACK_URL = f"https://open.spotify.com/track/{TRACK_ID}"
SHORT_URL = "https://spotify.link/abc123"
TRACK_PAGE = (
    "<html><head><title>Track - song and lyrics by Artist | Spotify</title></head></html>"
)


class FakeResponse:
    def __init__(self, url, status=200, location=None, text="", json=None):
        self.url = url
        self.status = status
        self.headers = {'Location': location} if location else {}
        self._text = text
        self._json = json

    async def text(self):
        return self._text

    async def json(self, content_type=None):
        return self._json


class FakeRequest:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *exc):
        return False


class FakeSpotifySession:
    """Serves canned responses and, like aiohttp, follows redirects by default."""

    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, params=None, allow_redirects=True, **kwargs):
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        for _ in range(10):  # aiohttp's default max_redirects
            self.requested.append(url)
            response = self.responses.get(url) or FakeResponse(url, status=404)
            if not (allow_redirects and response.headers.get('Location')):
                return FakeRequest(response)
            url = urllib.parse.urljoin(url, response.headers['Location'])
        raise aiohttp.ClientError("too many redirects")


def redirect(url, location):
    return FakeResponse(url, status=302, location=location)


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
        music = make_music()
        music.ensure_voice_client = AsyncMock()
        music.handle_spotify_url = AsyncMock(return_value="Track Artist")
        music.search_youtube = AsyncMock(return_value=[{
            'title': 'Track — Artist',
            'webpage_url': 'https://www.youtube.com/watch?v=abcdefghijk',
        }])
        music.play_song = AsyncMock()

        voice_client = make_voice_client()
        music.ensure_voice_client.return_value = voice_client

        ctx = make_ctx(voice_client=voice_client)

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


class SpotifyRedirectTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, responses, url=SHORT_URL):
        session = FakeSpotifySession(responses)
        with patch('cogs.music.aiohttp.ClientSession', return_value=session):
            result = await make_music().handle_spotify_url(url)
        return result, session.requested

    async def test_follows_short_link_redirects_within_spotify(self):
        result, requested = await self.resolve({
            SHORT_URL: redirect(SHORT_URL, f"{TRACK_URL}?si=xyz"),
            f"{TRACK_URL}?si=xyz": FakeResponse(f"{TRACK_URL}?si=xyz", text=TRACK_PAGE),
        })

        self.assertEqual(result, "Track Artist")
        self.assertEqual(requested, [SHORT_URL, f"{TRACK_URL}?si=xyz"])

    async def test_does_not_follow_redirects_to_other_hosts(self):
        internal = "http://127.0.0.1:8080/admin"
        result, requested = await self.resolve({
            SHORT_URL: redirect(SHORT_URL, internal),
            internal: FakeResponse(internal, text=TRACK_PAGE),
        })

        self.assertNotIn(internal, requested)
        self.assertIsNone(result)

    async def test_does_not_follow_redirects_that_drop_https(self):
        insecure = TRACK_URL.replace("https://", "http://")
        result, requested = await self.resolve({
            SHORT_URL: redirect(SHORT_URL, insecure),
            insecure: FakeResponse(insecure, text=TRACK_PAGE),
        })

        self.assertNotIn(insecure, requested)
        self.assertIsNone(result)

    async def test_gives_up_on_redirect_loops(self):
        other = "https://spotify.link/other"
        result, requested = await self.resolve({
            SHORT_URL: redirect(SHORT_URL, other),
            other: redirect(other, SHORT_URL),
        })

        self.assertIsNone(result)
        self.assertLess(len(requested), 20)

    async def test_resolves_relative_redirects(self):
        result, _ = await self.resolve({
            SHORT_URL: redirect(SHORT_URL, f"/track/{TRACK_ID}"),
            f"https://spotify.link/track/{TRACK_ID}": redirect(
                f"https://spotify.link/track/{TRACK_ID}", TRACK_URL
            ),
            TRACK_URL: FakeResponse(TRACK_URL, text=TRACK_PAGE),
        })

        self.assertEqual(result, "Track Artist")

    async def test_falls_back_to_oembed_when_the_page_is_unavailable(self):
        oembed = f"https://open.spotify.com/oembed?{urllib.parse.urlencode({'url': TRACK_URL})}"
        result, _ = await self.resolve({
            TRACK_URL: FakeResponse(TRACK_URL, status=403),
            oembed: FakeResponse(oembed, json={
                'title': 'Track Artist',
                'iframe_url': f"https://open.spotify.com/embed/track/{TRACK_ID}",
            }),
        }, url=TRACK_URL)

        self.assertEqual(result, "Track Artist")

    async def test_short_links_must_use_https(self):
        self.assertIsNone(Music.normalize_spotify_track_url("http://spotify.link/abc123"))
        self.assertIsNone(Music.normalize_spotify_track_url("https://spotify.link:8443/abc123"))


if __name__ == "__main__":
    unittest.main()
