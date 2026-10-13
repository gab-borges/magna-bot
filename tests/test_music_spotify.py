import json
import unittest
import urllib.parse
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from cogs.music import Music
from magna import spotify
from tests.music_helpers import make_ctx, make_music, make_voice_client, sent_text

TRACK_ID = "11dFghVXANMlKmJXsNCbNl"
TRACK_URL = f"https://open.spotify.com/track/{TRACK_ID}"
EMBED_URL = f"https://open.spotify.com/embed/track/{TRACK_ID}"
OEMBED_URL = f"https://open.spotify.com/oembed?{urllib.parse.urlencode({'url': TRACK_URL})}"
SHORT_URL = "https://spotify.link/abc123"
YOUTUBE_URL = "https://www.youtube.com/watch?v=abcdefghijk"


def embed_page(entity):
    """An embed page shaped like Spotify's (Next.js data in a script tag)."""
    data = {'props': {'pageProps': {'state': {'data': {'entity': entity}}}}}
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        f'{json.dumps(data)}</script></body></html>'
    )


def track_entity(name="Track", artists=("Artist",), track_id=TRACK_ID):
    return {
        'type': 'track',
        'id': track_id,
        'uri': f"spotify:track:{track_id}",
        'name': name,
        'artists': [{'name': artist} for artist in artists],
    }


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
        self.assertTrue(spotify.is_spotify_reference(
            f"https://open.spotify.com/track/{self.TRACK_ID}"
        ))
        self.assertTrue(spotify.is_spotify_reference(f"spotify:track:{self.TRACK_ID}"))
        self.assertTrue(spotify.is_spotify_reference("https://spotify.link/example"))

    def test_rejects_spoofed_spotify_hostname(self):
        self.assertFalse(spotify.is_spotify_reference(
            f"https://open.spotify.com.evil.example/track/{self.TRACK_ID}"
        ))

    def test_normalizes_regular_and_localized_track_urls(self):
        expected = f"https://open.spotify.com/track/{self.TRACK_ID}"
        self.assertEqual(
            spotify.normalize_track_url(
                f"https://open.spotify.com/track/{self.TRACK_ID}?si=abc"
            ),
            expected,
        )
        self.assertEqual(
            spotify.normalize_track_url(
                f"https://open.spotify.com/intl-pt/track/{self.TRACK_ID}"
            ),
            expected,
        )
        self.assertEqual(
            spotify.normalize_track_url(f"spotify:track:{self.TRACK_ID}"),
            expected,
        )

    def test_does_not_treat_album_as_track(self):
        self.assertIsNone(spotify.normalize_track_url(
            f"https://open.spotify.com/album/{self.TRACK_ID}"
        ))

    def test_does_not_treat_album_uri_as_track(self):
        self.assertIsNone(spotify.normalize_track_url(f"spotify:album:{self.TRACK_ID}"))


class SpotifyPlaybackTests(unittest.IsolatedAsyncioTestCase):
    def make_music(self, *, playing=False, resolved="Track Artist", results=None):
        music = make_music()
        self.vc = make_voice_client(playing=playing)
        music.ensure_voice_client = AsyncMock(return_value=self.vc)
        music.handle_spotify_url = AsyncMock(return_value=resolved)
        music.search_youtube = AsyncMock(return_value=[
            {'title': 'Track — Artist', 'webpage_url': YOUTUBE_URL},
        ] if results is None else results)
        music.play_song = AsyncMock()
        return music

    async def play(self, music, query=TRACK_URL):
        ctx = make_ctx(voice_client=self.vc)
        ctx.author.voice.channel = self.vc.channel
        await Music.play.callback(music, ctx, query=query)
        return ctx

    async def test_unreadable_track_reports_an_error_without_searching(self):
        music = self.make_music(resolved=None)

        ctx = await self.play(music)

        music.search_youtube.assert_not_awaited()
        music.play_song.assert_not_awaited()
        self.assertIn("couldn't read this Spotify track", sent_text(ctx))

    async def test_track_without_youtube_match_reports_it(self):
        music = self.make_music(results=[])

        ctx = await self.play(music)

        music.play_song.assert_not_awaited()
        self.assertIn("couldn't find this Spotify track on YouTube", sent_text(ctx))

    async def test_track_is_queued_with_its_title_while_music_plays(self):
        music = self.make_music(playing=True)

        ctx = await self.play(music)

        music.play_song.assert_not_awaited()
        self.assertEqual(music.queue['123'], [{'url': YOUTUBE_URL, 'title': 'Track — Artist'}])
        self.assertIn("Added to queue: **Track — Artist**", sent_text(ctx))

    async def test_album_link_is_rejected_without_network_requests(self):
        music = self.make_music()
        del music.handle_spotify_url  # use the real resolver

        with patch('magna.spotify.aiohttp.ClientSession') as session:
            ctx = await self.play(music, "https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX")

        session.assert_not_called()
        music.search_youtube.assert_not_awaited()
        self.assertIn("only individual track links are supported", sent_text(ctx))

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
        with patch('magna.spotify.aiohttp.ClientSession', return_value=session):
            result = await make_music().handle_spotify_url(url)
        return result, session.requested

    async def test_follows_short_link_redirects_within_spotify(self):
        result, requested = await self.resolve({
            SHORT_URL: redirect(SHORT_URL, f"{TRACK_URL}?si=xyz"),
            f"{TRACK_URL}?si=xyz": FakeResponse(f"{TRACK_URL}?si=xyz"),
            EMBED_URL: FakeResponse(EMBED_URL, text=embed_page(track_entity())),
        })

        self.assertEqual(result, "Track Artist")
        self.assertEqual(requested, [SHORT_URL, f"{TRACK_URL}?si=xyz", EMBED_URL])

    async def test_does_not_follow_redirects_to_other_hosts(self):
        internal = "http://127.0.0.1:8080/admin"
        result, requested = await self.resolve({
            SHORT_URL: redirect(SHORT_URL, internal),
            internal: FakeResponse(internal, text=embed_page(track_entity())),
        })

        self.assertNotIn(internal, requested)
        self.assertIsNone(result)

    async def test_does_not_follow_redirects_that_drop_https(self):
        insecure = TRACK_URL.replace("https://", "http://")
        result, requested = await self.resolve({
            SHORT_URL: redirect(SHORT_URL, insecure),
            insecure: FakeResponse(insecure, text=embed_page(track_entity())),
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
            TRACK_URL: FakeResponse(TRACK_URL),
            EMBED_URL: FakeResponse(EMBED_URL, text=embed_page(track_entity())),
        })

        self.assertEqual(result, "Track Artist")

    async def test_short_links_must_use_https(self):
        self.assertIsNone(spotify.normalize_track_url("http://spotify.link/abc123"))
        self.assertIsNone(spotify.normalize_track_url("https://spotify.link:8443/abc123"))


class SpotifyMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, responses, url=TRACK_URL):
        session = FakeSpotifySession(responses)
        with patch('magna.spotify.aiohttp.ClientSession', return_value=session):
            result = await make_music().handle_spotify_url(url)
        return result, session.requested

    def oembed(self, title="Track"):
        return FakeResponse(OEMBED_URL, json={
            'title': title,
            'iframe_url': f"https://open.spotify.com/embed/track/{TRACK_ID}?utm_source=oembed",
        })

    async def test_uses_track_name_and_every_artist_from_the_embed(self):
        entity = track_entity("Despacito", ["Luis Fonsi", "Daddy Yankee"])

        result, requested = await self.resolve({
            EMBED_URL: FakeResponse(EMBED_URL, text=embed_page(entity)),
        })

        self.assertEqual(result, "Despacito Luis Fonsi Daddy Yankee")
        self.assertEqual(requested, [EMBED_URL])

    async def test_resolves_spotify_uris(self):
        result, _ = await self.resolve({
            EMBED_URL: FakeResponse(EMBED_URL, text=embed_page(track_entity())),
        }, url=f"spotify:track:{TRACK_ID}")

        self.assertEqual(result, "Track Artist")

    async def test_falls_back_to_oembed_when_the_embed_is_unavailable(self):
        result, _ = await self.resolve({
            EMBED_URL: FakeResponse(EMBED_URL, status=403),
            OEMBED_URL: self.oembed("Track"),
        })

        self.assertEqual(result, "Track")

    async def test_falls_back_to_oembed_when_the_embed_layout_changes(self):
        pages = {
            "no data script": "<html><body>Spotify</body></html>",
            "invalid json": '<script id="__NEXT_DATA__">{not json</script>',
            "no entity": '<script id="__NEXT_DATA__">{"props": {"pageProps": {}}}</script>',
            "no artists": embed_page({**track_entity(), 'artists': []}),
        }
        for label, page in pages.items():
            with self.subTest(label):
                result, _ = await self.resolve({
                    EMBED_URL: FakeResponse(EMBED_URL, text=page),
                    OEMBED_URL: self.oembed("Track"),
                })

                self.assertEqual(result, "Track")

    async def test_ignores_embed_data_for_something_other_than_the_track(self):
        for label, entity in {
            "an album": {**track_entity(), 'type': 'album'},
            "another track": track_entity(track_id="0" * 22),
        }.items():
            with self.subTest(label):
                result, _ = await self.resolve({
                    EMBED_URL: FakeResponse(EMBED_URL, text=embed_page(entity)),
                    OEMBED_URL: self.oembed("Track"),
                })

                self.assertEqual(result, "Track")

    async def test_rejects_oembed_metadata_that_is_not_for_a_track(self):
        result, _ = await self.resolve({
            EMBED_URL: FakeResponse(EMBED_URL, status=404),
            OEMBED_URL: FakeResponse(OEMBED_URL, json={
                'title': 'Some Album',
                'iframe_url': 'https://open.spotify.com/embed/album/1ATL5GLyefJaxhQzSPVrLX',
            }),
        })

        self.assertIsNone(result)

    async def test_network_errors_resolve_to_none(self):
        session = FakeSpotifySession({})
        session.get = Mock(side_effect=aiohttp.ClientConnectionError("offline"))

        with patch('magna.spotify.aiohttp.ClientSession', return_value=session):
            result = await make_music().handle_spotify_url(TRACK_URL)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
