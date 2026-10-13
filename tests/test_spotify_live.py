"""Checks against the real Spotify and YouTube services.

They are skipped by default (and in CI) because they need network access and
depend on third-party services. Run them with:

    MAGNA_LIVE_TESTS=1 python -m unittest tests.test_spotify_live -v

Set SPOTIFY_SHORT_LINK to a spotify.link URL shared from the Spotify app to
also check short-link resolution.
"""
import os
import unittest
from types import SimpleNamespace

from cogs.music import Music
from magna import youtube

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

    async def test_resolves_track_name_and_artists(self):
        for track_id, (name, artists) in KNOWN_TRACKS.items():
            with self.subTest(name):
                query = await self.music.handle_spotify_url(
                    f"https://open.spotify.com/track/{track_id}?si=test"
                )
                self.assertEqual(query, " ".join([name, *artists]))

    async def test_resolves_uris_and_localized_urls(self):
        track_id = "3n3Ppam7vgaVa1iaRUc9Lp"
        for reference in (
            f"spotify:track:{track_id}",
            f"https://open.spotify.com/intl-pt/track/{track_id}",
        ):
            with self.subTest(reference):
                self.assertEqual(
                    await self.music.handle_spotify_url(reference),
                    "Mr. Brightside The Killers",
                )

    async def test_does_not_resolve_albums(self):
        self.assertIsNone(await self.music.handle_spotify_url(
            "https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX"
        ))

    @unittest.skipUnless(os.getenv('SPOTIFY_SHORT_LINK'), "set SPOTIFY_SHORT_LINK to check short links")
    async def test_resolves_short_link(self):
        query = await self.music.handle_spotify_url(os.environ['SPOTIFY_SHORT_LINK'])
        self.assertTrue(query)

    async def test_first_youtube_result_matches_the_track(self):
        # %play plays the first result without asking, so that one must match.
        for track_id in ("6habFhsOp2NvshLv26DqMb", "3n3Ppam7vgaVa1iaRUc9Lp"):
            name, _ = KNOWN_TRACKS[track_id]
            with self.subTest(name):
                query = await self.music.handle_spotify_url(
                    f"https://open.spotify.com/track/{track_id}"
                )
                results = await self.music.search_youtube(query)

                self.assertTrue(results, f"no YouTube results for {query!r}")
                first = results[0]
                self.assertIsNotNone(youtube.normalize_url(first['webpage_url']))
                self.assertIn(name.casefold(), first['title'].casefold())


if __name__ == "__main__":
    unittest.main()
