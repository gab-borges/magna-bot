import json
import unittest
import urllib.parse
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from cogs.music import Music
from tests.music_helpers import make_ctx, make_music, make_voice_client, sent_text

TRACK_ID = "11dFghVXANMlKmJXsNCbNl"
TRACK_URL = f"https://open.spotify.com/track/{TRACK_ID}"
EMBED_URL = f"https://open.spotify.com/embed/track/{TRACK_ID}"
SHORT_URL = "https://spotify.link/abc123"
ALBUM_ID = "1ATL5GLyefJaxhQzSPVrLX"
PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"
PLAYLIST_URL = f"https://open.spotify.com/playlist/{PLAYLIST_ID}"
PLAYLIST_EMBED_URL = f"https://open.spotify.com/embed/playlist/{PLAYLIST_ID}"
YOUTUBE_URL = "https://www.youtube.com/watch?v=abcdefghijk"


def embed_page(entity):
    """An embed page shaped like Spotify's (Next.js data in a script tag)."""
    data = {'props': {'pageProps': {'state': {'data': {'entity': entity}}}}}
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        f'{json.dumps(data)}</script></body></html>'
    )


def playlist_entity(track_list, playlist_id=PLAYLIST_ID, name="Mix"):
    return {
        'type': 'playlist',
        'id': playlist_id,
        'uri': f"spotify:playlist:{playlist_id}",
        'name': name,
        'trackList': track_list,
    }


def list_item(title, subtitle, track_id=TRACK_ID, duration=None):
    item = {'uri': f"spotify:track:{track_id}", 'title': title, 'subtitle': subtitle}
    if duration is not None:
        item['duration'] = duration
    return item


def track_entity(name="Track", artists=("Artist",), track_id=TRACK_ID, duration=None):
    entity = {
        'type': 'track',
        'id': track_id,
        'uri': f"spotify:track:{track_id}",
        'name': name,
        'artists': [{'name': artist} for artist in artists],
    }
    if duration is not None:
        entity['duration'] = duration
    return entity


def spotify_track(name="Track", artists=("Artist",), duration_ms=None):
    return {'name': name, 'artists': list(artists), 'duration_ms': duration_ms}


TRACK = spotify_track()


class FakeResponse:
    def __init__(self, url, status=200, location=None, text=""):
        self.url = url
        self.status = status
        self.headers = {'Location': location} if location else {}
        self._text = text

    async def text(self):
        return self._text


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

    def get(self, url, allow_redirects=True, **kwargs):
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

    def test_does_not_treat_album_uri_as_track(self):
        self.assertIsNone(Music.normalize_spotify_track_url(f"spotify:album:{self.TRACK_ID}"))

    def test_recognizes_album_and_playlist_urls_and_uris(self):
        for reference, expected in {
            f"https://open.spotify.com/album/{ALBUM_ID}?si=abc": ('album', ALBUM_ID),
            f"https://open.spotify.com/intl-pt/playlist/{PLAYLIST_ID}": ('playlist', PLAYLIST_ID),
            f"spotify:playlist:{PLAYLIST_ID}": ('playlist', PLAYLIST_ID),
            f"spotify:album:{ALBUM_ID}": ('album', ALBUM_ID),
        }.items():
            with self.subTest(reference):
                self.assertEqual(
                    Music.spotify_entity(reference, ('album', 'playlist')), expected
                )

    def test_rejects_other_spotify_entities_and_hosts(self):
        for reference in (
            f"https://open.spotify.com/track/{self.TRACK_ID}",
            f"https://open.spotify.com/artist/{ALBUM_ID}",
            f"https://open.spotify.com/playlist/{PLAYLIST_ID}x",
            f"https://open.spotify.com.evil.example/playlist/{PLAYLIST_ID}",
            f"spotify:artist:{ALBUM_ID}",
        ):
            with self.subTest(reference):
                self.assertIsNone(Music.spotify_entity(reference, ('album', 'playlist')))


class SpotifyPlaybackTests(unittest.IsolatedAsyncioTestCase):
    def make_music(self, *, playing=False, resolved=TRACK, match=YOUTUBE_URL):
        music = make_music()
        self.vc = make_voice_client(playing=playing)
        music.ensure_voice_client = AsyncMock(return_value=self.vc)
        music.handle_spotify_url = AsyncMock(return_value=resolved)
        music.find_spotify_match = AsyncMock(return_value=match)
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

        music.find_spotify_match.assert_not_awaited()
        music.play_song.assert_not_awaited()
        self.assertIn("couldn't read this Spotify track", sent_text(ctx))

    async def test_track_without_a_reliable_match_is_skipped(self):
        music = self.make_music(match=None)

        ctx = await self.play(music)

        music.play_song.assert_not_awaited()
        self.assertIn(
            "couldn't find a reliable match for **Track — Artist** on YouTube, so I skipped it",
            sent_text(ctx),
        )

    async def test_track_is_queued_with_its_spotify_title_while_music_plays(self):
        music = self.make_music(playing=True)

        ctx = await self.play(music)

        music.play_song.assert_not_awaited()
        self.assertEqual(music.queue['123'], [{'url': YOUTUBE_URL, 'title': 'Track — Artist'}])
        self.assertIn("Added to queue: **Track — Artist**", sent_text(ctx))

    async def test_artist_link_is_rejected_without_network_requests(self):
        music = self.make_music()
        del music.handle_spotify_url  # use the real resolver

        with patch('cogs.music.aiohttp.ClientSession') as session:
            ctx = await self.play(music, "https://open.spotify.com/artist/0gxyHStUsqpMadRV0Di1Qt")

        session.assert_not_called()
        music.find_spotify_match.assert_not_awaited()
        self.assertIn("Only track, album and playlist links are supported", sent_text(ctx))

    async def test_spotify_track_plays_its_match_without_prompt(self):
        music = self.make_music()

        ctx = await self.play(music)

        music.find_spotify_match.assert_awaited_once_with(TRACK)
        music.play_song.assert_awaited_once_with(ctx, YOUTUBE_URL)


class SpotifyRedirectTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, responses, url=SHORT_URL):
        session = FakeSpotifySession(responses)
        with patch('cogs.music.aiohttp.ClientSession', return_value=session):
            result = await make_music().handle_spotify_url(url)
        return result, session.requested

    async def test_follows_short_link_redirects_within_spotify(self):
        result, requested = await self.resolve({
            SHORT_URL: redirect(SHORT_URL, f"{TRACK_URL}?si=xyz"),
            f"{TRACK_URL}?si=xyz": FakeResponse(f"{TRACK_URL}?si=xyz"),
            EMBED_URL: FakeResponse(EMBED_URL, text=embed_page(track_entity())),
        })

        self.assertEqual(result, TRACK)
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

        self.assertEqual(result, TRACK)

    async def test_short_link_to_something_else_resolves_to_none(self):
        result, requested = await self.resolve({
            SHORT_URL: redirect(SHORT_URL, PLAYLIST_URL),
            PLAYLIST_URL: FakeResponse(PLAYLIST_URL),
        })

        self.assertIsNone(result)
        self.assertEqual(requested, [SHORT_URL, PLAYLIST_URL])

    async def test_short_links_must_use_https(self):
        self.assertIsNone(Music.normalize_spotify_track_url("http://spotify.link/abc123"))
        self.assertIsNone(Music.normalize_spotify_track_url("https://spotify.link:8443/abc123"))


class SpotifyMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, responses, url=TRACK_URL):
        session = FakeSpotifySession(responses)
        with patch('cogs.music.aiohttp.ClientSession', return_value=session):
            result = await make_music().handle_spotify_url(url)
        return result, session.requested

    async def test_reads_name_every_artist_and_duration_from_the_embed(self):
        entity = track_entity("Despacito", ["Luis Fonsi", "Daddy Yankee"], duration=229360)

        result, requested = await self.resolve({
            EMBED_URL: FakeResponse(EMBED_URL, text=embed_page(entity)),
        })

        self.assertEqual(result, spotify_track("Despacito", ["Luis Fonsi", "Daddy Yankee"], 229360))
        self.assertEqual(requested, [EMBED_URL])

    async def test_resolves_spotify_uris(self):
        result, _ = await self.resolve({
            EMBED_URL: FakeResponse(EMBED_URL, text=embed_page(track_entity())),
        }, url=f"spotify:track:{TRACK_ID}")

        self.assertEqual(result, TRACK)

    async def test_ignores_invalid_durations(self):
        for duration in (0, -1, "229360", True):
            with self.subTest(duration=duration):
                result, _ = await self.resolve({
                    EMBED_URL: FakeResponse(EMBED_URL, text=embed_page(
                        {**track_entity(), 'duration': duration}
                    )),
                })

                self.assertIsNone(result['duration_ms'])

    async def test_unreadable_embeds_resolve_to_none(self):
        # A name alone can't be checked against YouTube results, so there is
        # no fallback to Spotify's name-only oEmbed endpoint.
        pages = {
            "unavailable": FakeResponse(EMBED_URL, status=403),
            "no data script": FakeResponse(EMBED_URL, text="<html><body>Spotify</body></html>"),
            "invalid json": FakeResponse(EMBED_URL, text='<script id="__NEXT_DATA__">{not json</script>'),
            "no entity": FakeResponse(
                EMBED_URL, text='<script id="__NEXT_DATA__">{"props": {"pageProps": {}}}</script>'
            ),
            "no artists": FakeResponse(EMBED_URL, text=embed_page({**track_entity(), 'artists': []})),
            "an album": FakeResponse(EMBED_URL, text=embed_page({**track_entity(), 'type': 'album'})),
            "another track": FakeResponse(EMBED_URL, text=embed_page(track_entity(track_id="0" * 22))),
        }
        for label, response in pages.items():
            with self.subTest(label):
                result, _ = await self.resolve({EMBED_URL: response})

                self.assertIsNone(result)

    async def test_network_errors_resolve_to_none(self):
        session = FakeSpotifySession({})
        session.get = Mock(side_effect=aiohttp.ClientConnectionError("offline"))

        with patch('cogs.music.aiohttp.ClientSession', return_value=session):
            result = await make_music().handle_spotify_url(TRACK_URL)

        self.assertIsNone(result)


class SpotifyCollectionParsingTests(unittest.TestCase):
    def parse(self, entity, kind='playlist', entity_id=PLAYLIST_ID):
        return Music.spotify_collection_from_embed(embed_page(entity), kind, entity_id)

    def test_reads_the_name_and_every_track(self):
        collection = self.parse(playlist_entity([
            list_item("Despacito", "Luis Fonsi,\u00a0Daddy Yankee", duration=229360),
            list_item("Mr. Brightside", "The Killers"),
        ]))

        self.assertEqual(collection, {'name': 'Mix', 'tracks': [
            spotify_track("Despacito", ["Luis Fonsi", "Daddy Yankee"], 229360),
            spotify_track("Mr. Brightside", ["The Killers"]),
        ]})

    def test_prefers_the_artist_list_when_there_is_one(self):
        item = {
            'uri': f"spotify:track:{TRACK_ID}",
            'title': 'Who Dat Boy',
            'subtitle': 'Tyler, The Creator, A$AP Rocky',
            'artists': [{'name': 'Tyler, The Creator'}, {'name': 'A$AP Rocky'}],
        }

        collection = self.parse(playlist_entity([item]))

        self.assertEqual(collection['tracks'][0]['artists'], ['Tyler, The Creator', 'A$AP Rocky'])

    def test_skips_episodes_local_files_and_items_without_title_or_artists(self):
        collection = self.parse(playlist_entity([
            {'uri': f"spotify:episode:{TRACK_ID}", 'title': 'A podcast', 'subtitle': 'Host'},
            {'uri': 'spotify:local:Artist:Album:Song:180', 'title': 'Song', 'subtitle': 'Artist'},
            list_item("", "Nobody"),
            list_item("No artist", ""),
            "not a track",
            list_item("Track", "Artist"),
        ]))

        self.assertEqual(collection['tracks'], [TRACK])

    def test_reads_albums(self):
        entity = {**playlist_entity([list_item("Track", "Artist")]),
                  'type': 'album', 'uri': f"spotify:album:{ALBUM_ID}", 'name': 'Record'}

        collection = self.parse(entity, 'album', ALBUM_ID)

        self.assertEqual(collection['name'], 'Record')

    def test_rejects_pages_without_a_usable_track_list(self):
        pages = {
            "another playlist": playlist_entity([list_item("Track", "Artist")], "0" * 22),
            "an album": {**playlist_entity([list_item("Track", "Artist")]), 'type': 'album'},
            "no track list": {**playlist_entity([]), 'trackList': None},
            "only episodes": playlist_entity([
                {'uri': f"spotify:episode:{TRACK_ID}", 'title': 'A podcast'},
            ]),
        }
        for label, entity in pages.items():
            with self.subTest(label):
                self.assertIsNone(self.parse(entity))


class SpotifyCollectionResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_the_playlist_embed_page(self):
        session = FakeSpotifySession({
            PLAYLIST_EMBED_URL: FakeResponse(PLAYLIST_EMBED_URL, text=embed_page(
                playlist_entity([list_item("Track", "Artist")])
            )),
        })

        with patch('cogs.music.aiohttp.ClientSession', return_value=session):
            collection = await make_music().handle_spotify_collection_url(f"{PLAYLIST_URL}?si=x")

        self.assertEqual(collection['tracks'], [TRACK])
        self.assertEqual(session.requested, [PLAYLIST_EMBED_URL])

    async def test_unavailable_playlist_resolves_to_none(self):
        session = FakeSpotifySession({})

        with patch('cogs.music.aiohttp.ClientSession', return_value=session):
            self.assertIsNone(await make_music().handle_spotify_collection_url(PLAYLIST_URL))

    async def test_network_errors_resolve_to_none(self):
        session = FakeSpotifySession({})
        session.get = Mock(side_effect=aiohttp.ClientConnectionError("offline"))

        with patch('cogs.music.aiohttp.ClientSession', return_value=session):
            self.assertIsNone(await make_music().handle_spotify_collection_url(PLAYLIST_URL))


def numbered_track(number):
    return spotify_track(f"Song {number}")


def collection(count, name="Mix"):
    return {'name': name, 'tracks': [numbered_track(i) for i in range(1, count + 1)]}


def queued(number):
    return {'url': None, 'spotify': numbered_track(number), 'title': f"Song {number} — Artist"}


class SpotifyCollectionPlaybackTests(unittest.IsolatedAsyncioTestCase):
    def make_music(self, *, playing=False, tracks=None):
        music = make_music()
        self.vc = make_voice_client(playing=playing)
        music.voice_clients['123'] = self.vc
        music.ensure_voice_client = AsyncMock(return_value=self.vc)
        music.handle_spotify_collection_url = AsyncMock(
            return_value=collection(3) if tracks is None else tracks
        )
        music.find_spotify_match = AsyncMock(return_value=YOUTUBE_URL)

        async def start_playing(ctx, url):
            self.vc.is_playing.return_value = True

        music.play_song = AsyncMock(side_effect=start_playing)
        return music

    async def play(self, music, query=PLAYLIST_URL):
        ctx = make_ctx(voice_client=self.vc)
        ctx.author.voice.channel = self.vc.channel
        await Music.play.callback(music, ctx, query=query)
        return ctx

    async def test_playlist_starts_its_first_track_and_queues_the_rest(self):
        music = self.make_music()

        ctx = await self.play(music)

        music.find_spotify_match.assert_awaited_once_with(numbered_track(1))
        music.play_song.assert_awaited_once_with(ctx, YOUTUBE_URL)
        self.assertEqual(music.queue['123'], [queued(2), queued(3)])
        self.assertIn("Added 3 tracks from **Mix** to the queue", sent_text(ctx))

    async def test_playlist_is_queued_without_searching_while_music_plays(self):
        music = self.make_music(playing=True)
        music.queue['123'] = [{'url': YOUTUBE_URL, 'title': 'Earlier'}]

        await self.play(music)

        music.find_spotify_match.assert_not_awaited()
        music.play_song.assert_not_awaited()
        self.assertEqual(
            music.queue['123'],
            [{'url': YOUTUBE_URL, 'title': 'Earlier'}, queued(1), queued(2), queued(3)],
        )

    async def test_long_playlists_are_capped(self):
        music = self.make_music(playing=True, tracks=collection(150))

        ctx = await self.play(music)

        self.assertEqual(len(music.queue['123']), 100)
        self.assertIn("(only the first 100)", sent_text(ctx))

    async def test_unreadable_playlist_reports_an_error(self):
        music = self.make_music()
        music.handle_spotify_collection_url.return_value = None

        ctx = await self.play(music)

        music.play_song.assert_not_awaited()
        self.assertIn("couldn't read the tracks of this Spotify album or playlist", sent_text(ctx))

    async def test_short_link_to_a_playlist_queues_the_playlist(self):
        music = self.make_music(playing=True)
        music.follow_spotify_short_link = AsyncMock(return_value=f"{PLAYLIST_URL}?si=x")
        music.handle_spotify_url = AsyncMock()

        await self.play(music, SHORT_URL)

        music.handle_spotify_collection_url.assert_awaited_once_with(f"{PLAYLIST_URL}?si=x")
        music.handle_spotify_url.assert_not_awaited()
        self.assertEqual(len(music.queue['123']), 3)

    async def test_short_link_to_a_track_plays_the_track(self):
        music = self.make_music()
        music.follow_spotify_short_link = AsyncMock(return_value=f"{TRACK_URL}?si=x")
        music.handle_spotify_url = AsyncMock(return_value=TRACK)

        await self.play(music, SHORT_URL)

        music.handle_spotify_url.assert_awaited_once_with(f"{TRACK_URL}?si=x")
        music.handle_spotify_collection_url.assert_not_awaited()
        music.play_song.assert_awaited_once()


class QueuedSearchTests(unittest.IsolatedAsyncioTestCase):
    def make_music(self):
        music = make_music()
        self.vc = make_voice_client()
        music.voice_clients['123'] = self.vc
        music.find_spotify_match = AsyncMock()

        async def start_playing(ctx, url):
            self.vc.is_playing.return_value = True

        music.play_song = AsyncMock(side_effect=start_playing)
        return music

    async def test_tracks_without_a_reliable_match_are_skipped(self):
        music = self.make_music()
        music.queue['123'] = [queued(1), queued(2)]
        music.find_spotify_match.side_effect = [None, YOUTUBE_URL]
        ctx = make_ctx(voice_client=self.vc)

        await music.play_next(ctx)

        music.play_song.assert_awaited_once_with(ctx, YOUTUBE_URL)
        self.assertEqual(music.queue['123'], [])
        self.assertIn(
            "couldn't find a reliable match for **Song 1 — Artist** on YouTube, so I skipped it",
            sent_text(ctx),
        )

    async def test_tracks_that_fail_to_start_are_skipped(self):
        music = self.make_music()
        other_url = "https://www.youtube.com/watch?v=bcdefghijkl"
        music.queue['123'] = [{'url': YOUTUBE_URL, 'title': 'Broken'},
                              {'url': other_url, 'title': 'Works'}]

        async def play_song(ctx, url):
            if url == other_url:
                self.vc.is_playing.return_value = True

        music.play_song = AsyncMock(side_effect=play_song)

        await music.play_next(make_ctx(voice_client=self.vc))

        self.assertEqual([call.args[1] for call in music.play_song.await_args_list],
                         [YOUTUBE_URL, other_url])

    async def test_stop_during_a_search_keeps_the_found_track_from_playing(self):
        music = self.make_music()
        music.queue['123'] = [queued(1), queued(2)]

        async def search_then_stop(track):
            music.queue['123'] = []  # what %stop does
            return YOUTUBE_URL

        music.find_spotify_match.side_effect = search_then_stop

        await music.play_next(make_ctx(voice_client=self.vc))

        music.play_song.assert_not_awaited()

    async def test_stops_when_the_voice_connection_is_gone(self):
        music = self.make_music()
        music.play_song = AsyncMock()  # never starts playing
        self.vc.is_connected.return_value = False
        music.queue['123'] = [{'url': YOUTUBE_URL, 'title': 'One'},
                              {'url': YOUTUBE_URL, 'title': 'Two'}]

        await music.play_next(make_ctx(voice_client=self.vc))

        music.play_song.assert_awaited_once()
        self.assertEqual(music.queue['123'], [{'url': YOUTUBE_URL, 'title': 'Two'}])

    async def test_queue_listing_fits_in_one_embed(self):
        music = self.make_music()
        music.queue['123'] = [queued(number) for number in range(1, 31)]
        ctx = make_ctx(voice_client=self.vc)

        await Music.queue.callback(music, ctx)

        embed = ctx.send.await_args.kwargs['embed']
        self.assertEqual(len(embed.fields), 20)
        self.assertEqual(embed.footer.text, "…and 10 more")


if __name__ == "__main__":
    unittest.main()
