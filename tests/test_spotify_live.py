"""Checks against the real Spotify and YouTube services.

They are skipped by default (and in CI) because they need network access and
depend on third-party services. Run them with:

    MAGNA_LIVE_TESTS=1 python -m unittest tests.test_spotify_live -v

Set SPOTIFY_SHORT_LINK to a spotify.link URL shared from the Spotify app to
also check short-link resolution, and SPOTIFY_PLAYLIST_URL to check one of
your own playlists (how many tracks are read, and how many of the first ten
find a YouTube match, is printed).
"""
import os
import unittest
from types import SimpleNamespace

from cogs.music import Music

LIVE = os.getenv('MAGNA_LIVE_TESTS') == '1'

# track ID -> (name, artists), as listed on Spotify
KNOWN_TRACKS = {
    "11dFghVXANMlKmJXsNCbNl": ("Cut To The Feeling", ["Carly Rae Jepsen"]),
    "6habFhsOp2NvshLv26DqMb": ("Despacito", ["Luis Fonsi", "Daddy Yankee"]),
    "3n3Ppam7vgaVa1iaRUc9Lp": ("Mr. Brightside", ["The Killers"]),
    "4uLU6hMCjMI75M1A2tKUQC": ("Never Gonna Give You Up", ["Rick Astley"]),
}


@unittest.skipUnless(LIVE, "set MAGNA_LIVE_TESTS=1 to run live Spotify/YouTube checks")
class SpotifyLiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.music = Music(SimpleNamespace())

    def tearDown(self):
        self.music.cog_unload()

    async def test_resolves_track_name_artists_and_duration(self):
        for track_id, (name, artists) in KNOWN_TRACKS.items():
            with self.subTest(name):
                track = await self.music.handle_spotify_url(
                    f"https://open.spotify.com/track/{track_id}?si=test"
                )
                self.assertEqual(track['name'], name)
                self.assertEqual(track['artists'], artists)
                self.assertGreater(track['duration_ms'] or 0, 60_000)

    async def test_resolves_uris_and_localized_urls(self):
        track_id = "3n3Ppam7vgaVa1iaRUc9Lp"
        for reference in (
            f"spotify:track:{track_id}",
            f"https://open.spotify.com/intl-pt/track/{track_id}",
        ):
            with self.subTest(reference):
                track = await self.music.handle_spotify_url(reference)
                self.assertEqual((track['name'], track['artists']), ("Mr. Brightside", ["The Killers"]))

    async def test_does_not_resolve_albums(self):
        self.assertIsNone(await self.music.handle_spotify_url(
            "https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX"
        ))

    def assert_collection(self, collection):
        self.assertIsNotNone(collection)
        self.assertTrue(collection['name'])
        self.assertTrue(collection['tracks'])
        for track in collection['tracks']:
            self.assertTrue(track['name'])
            self.assertTrue(track['artists'])

    async def test_reads_album_tracks(self):
        collection = await self.music.handle_spotify_collection_url(
            "https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX"
        )
        self.assert_collection(collection)

    async def test_reads_spotify_editorial_playlist(self):
        # "Today's Top Hits": the official Web API no longer lists the tracks
        # of playlists the caller doesn't own, so this checks the embed path.
        collection = await self.music.handle_spotify_collection_url(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        )
        self.assert_collection(collection)

    @unittest.skipUnless(os.getenv('SPOTIFY_PLAYLIST_URL'), "set SPOTIFY_PLAYLIST_URL to check a playlist")
    async def test_reads_given_playlist(self):
        collection = await self.music.handle_spotify_collection_url(os.environ['SPOTIFY_PLAYLIST_URL'])
        self.assert_collection(collection)
        print(f"\n{collection['name']}: read {len(collection['tracks'])} tracks")

    @unittest.skipUnless(os.getenv('SPOTIFY_SHORT_LINK'), "set SPOTIFY_SHORT_LINK to check short links")
    async def test_resolves_short_link(self):
        track = await self.music.handle_spotify_url(os.environ['SPOTIFY_SHORT_LINK'])
        self.assertTrue(track)

    async def test_finds_a_reliable_youtube_match(self):
        for track_id, (name, _) in KNOWN_TRACKS.items():
            with self.subTest(name):
                track = await self.music.handle_spotify_url(
                    f"https://open.spotify.com/track/{track_id}"
                )
                url = await self.music.find_spotify_match(track)

                self.assertIsNotNone(url, f"no reliable match for {track}")
                self.assertIsNotNone(Music.normalize_youtube_url(url))

    async def test_youtube_music_finds_official_songs(self):
        candidates = await self.music.ytmusic_candidates("Mr. Brightside The Killers", 'songs')

        self.assertTrue(candidates)
        self.assertTrue(all(candidate['duration'] for candidate in candidates))

    @unittest.skipUnless(os.getenv('SPOTIFY_PLAYLIST_URL'), "set SPOTIFY_PLAYLIST_URL to check a playlist")
    async def test_matches_given_playlist(self):
        # Prints how many of the first tracks find a reliable YouTube match.
        collection = await self.music.handle_spotify_collection_url(os.environ['SPOTIFY_PLAYLIST_URL'])
        self.assert_collection(collection)
        tracks = collection['tracks'][:10]
        missing = [
            Music.spotify_track_title(track)
            for track in tracks
            if not await self.music.find_spotify_match(track)
        ]
        print(f"\n{len(tracks) - len(missing)}/{len(tracks)} matched; no match for: {missing}")


if __name__ == "__main__":
    unittest.main()
