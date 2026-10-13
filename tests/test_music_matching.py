import unittest
from unittest.mock import AsyncMock, Mock

import music_matching
from tests.music_helpers import make_music

BRIGHTSIDE = {'name': 'Mr. Brightside', 'artists': ['The Killers'], 'duration_ms': 222973}


def candidate(title, artist_text, duration, video_id="abcdefghijk"):
    return {'video_id': video_id, 'title': title, 'artist_text': artist_text, 'duration': duration}


class MatchingTests(unittest.TestCase):
    def test_accepts_the_same_recording(self):
        for label, result in {
            "official song": candidate("Mr. Brightside", "The Killers", 223),
            "video naming the artist": candidate(
                "The Killers - Mr. Brightside (Official Music Video)", "The Killers - Topic", 227,
            ),
        }.items():
            with self.subTest(label):
                self.assertTrue(music_matching.is_match(BRIGHTSIDE, result))

    def test_rejects_other_recordings_and_songs(self):
        for label, result in {
            "unrelated song": candidate("Somebody Told Me", "The Killers", 223),
            "another artist": candidate("Mr. Brightside", "Some Cover Band", 223),
            "live version": candidate("Mr. Brightside (Live)", "The Killers", 225),
            "cover": candidate("Mr. Brightside (Cover)", "The Killers fan", 224),
            "music video with a long intro": candidate("Mr. Brightside", "The Killers", 260),
            "unknown duration": candidate("Mr. Brightside", "The Killers", None),
        }.items():
            with self.subTest(label):
                self.assertFalse(music_matching.is_match(BRIGHTSIDE, result))

    def test_keeps_versions_apart(self):
        remix = {'name': 'Despacito - Remix', 'artists': ['Luis Fonsi'], 'duration_ms': 229000}
        self.assertTrue(music_matching.is_match(
            remix, candidate("Despacito (Remix)", "Luis Fonsi, Daddy Yankee", 229)
        ))
        self.assertFalse(music_matching.is_match(
            remix, candidate("Despacito", "Luis Fonsi, Daddy Yankee", 229)
        ))

    def test_titles_with_live_in_their_name_still_match(self):
        track = {'name': 'Live Forever', 'artists': ['Oasis'], 'duration_ms': 276000}
        self.assertTrue(music_matching.is_match(track, candidate("Live Forever", "Oasis", 277)))

    def test_ignores_accents_case_punctuation_and_suffixes(self):
        track = {'name': 'Águas de Março - Remastered 2011',
                 'artists': ['Elis Regina', 'Tom Jobim'], 'duration_ms': 213000}
        self.assertTrue(music_matching.is_match(
            track, candidate("AGUAS DE MARCO", "Elis Regina & Tom Jobim", 214)
        ))

    def test_artist_names_with_commas(self):
        track = {'name': 'Who Dat Boy', 'artists': ['Tyler', 'The Creator'], 'duration_ms': 205000}
        self.assertTrue(music_matching.is_match(
            track, candidate("Who Dat Boy", "Tyler, The Creator", 205)
        ))

    def test_unknown_spotify_duration_relies_on_title_and_artist(self):
        track = {**BRIGHTSIDE, 'duration_ms': None}
        self.assertTrue(music_matching.is_match(track, candidate("Mr. Brightside", "The Killers", 260)))

    def test_artist_is_required(self):
        track = {**BRIGHTSIDE, 'artists': []}
        self.assertFalse(music_matching.is_match(track, candidate("Mr. Brightside", "The Killers", 223)))

    def test_picks_the_first_match_in_ranking_order(self):
        results = [
            candidate("Mr. Brightside (Live)", "The Killers", 225, "aaaaaaaaaaa"),
            candidate("Mr. Brightside", "The Killers", 223, "bbbbbbbbbbb"),
            candidate("Mr. Brightside", "The Killers", 222, "ccccccccccc"),
        ]
        self.assertEqual(music_matching.pick_match(BRIGHTSIDE, results)['video_id'], "bbbbbbbbbbb")
        self.assertIsNone(music_matching.pick_match(BRIGHTSIDE, results[:1]))


SONG_RESULT = {
    'resultType': 'song', 'videoId': 'gGdGFtwCNBE', 'title': 'Mr. Brightside',
    'artists': [{'name': 'The Killers', 'id': 'UC'}], 'duration_seconds': 223,
}
VIDEO_RESULT = {
    'resultType': 'video', 'videoId': 'gGdGFtwCNBE', 'title': 'The Killers - Mr. Brightside',
    'artists': [{'name': 'TheKillersVEVO', 'id': 'UC'}], 'duration_seconds': 227,
}


class FindSpotifyMatchTests(unittest.IsolatedAsyncioTestCase):
    def make_music(self, *, songs=(), videos=(), youtube=()):
        music = make_music()
        music.ytmusic = Mock()
        music.ytmusic.search.side_effect = lambda query, filter, limit: list(
            songs if filter == 'songs' else videos
        )
        music.youtube_candidates = AsyncMock(return_value=list(youtube))
        return music

    async def test_prefers_official_songs(self):
        music = self.make_music(songs=[SONG_RESULT])

        url = await music.find_spotify_match(BRIGHTSIDE)

        self.assertEqual(url, "https://www.youtube.com/watch?v=gGdGFtwCNBE")
        music.ytmusic.search.assert_called_once_with(
            "Mr. Brightside The Killers", filter='songs', limit=10
        )
        music.youtube_candidates.assert_not_awaited()

    async def test_videos_only_need_to_name_the_artist_in_the_title(self):
        music = self.make_music(videos=[VIDEO_RESULT])

        self.assertEqual(
            await music.find_spotify_match(BRIGHTSIDE),
            "https://www.youtube.com/watch?v=gGdGFtwCNBE",
        )

    async def test_falls_back_to_youtube(self):
        music = self.make_music(youtube=[
            candidate("The Killers - Mr. Brightside", "The Killers", 223, "zzzzzzzzzzz"),
        ])

        self.assertEqual(
            await music.find_spotify_match(BRIGHTSIDE),
            "https://www.youtube.com/watch?v=zzzzzzzzzzz",
        )

    async def test_returns_none_instead_of_guessing(self):
        music = self.make_music(
            songs=[{**SONG_RESULT, 'title': 'Somebody Told Me'}],
            videos=[{**VIDEO_RESULT, 'title': 'Mr. Brightside (Live at Wembley)'}],
            youtube=[candidate("Mr. Brightside reaction", "Some Channel", 400)],
        )

        self.assertIsNone(await music.find_spotify_match(BRIGHTSIDE))

    async def test_a_failing_search_moves_on_to_the_next(self):
        music = self.make_music(youtube=[
            candidate("Mr. Brightside", "The Killers", 223, "zzzzzzzzzzz"),
        ])
        music.ytmusic.search.side_effect = Exception("YouTube Music changed")

        self.assertEqual(
            await music.find_spotify_match(BRIGHTSIDE),
            "https://www.youtube.com/watch?v=zzzzzzzzzzz",
        )

    async def test_skips_results_without_a_valid_video_id(self):
        music = self.make_music(songs=[
            {**SONG_RESULT, 'videoId': None},
            {**SONG_RESULT, 'videoId': '../../evil'},
        ])

        self.assertIsNone(await music.find_spotify_match(BRIGHTSIDE))


class YoutubeCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_title_channel_and_duration_from_yt_dlp(self):
        music = make_music()
        music.extract_info = AsyncMock(return_value={'entries': [
            {'id': 'gGdGFtwCNBE', 'title': 'Mr. Brightside', 'channel': 'The Killers',
             'duration': 223.0},
            {'id': None, 'title': 'Broken entry'},
        ]})

        candidates = await music.youtube_candidates("Mr. Brightside The Killers")

        self.assertEqual(candidates, [{
            'video_id': 'gGdGFtwCNBE', 'title': 'Mr. Brightside',
            'artist_text': 'The Killers Mr. Brightside', 'duration': 223.0,
        }])
        self.assertEqual(music.extract_info.await_args.args[1], "ytsearch10:Mr. Brightside The Killers")


if __name__ == "__main__":
    unittest.main()
