import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.music import VOICE_ACTIVITY_FILE
from tests.music_helpers import make_ctx, make_music, make_voice_client


class VoiceActivityFileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.dict(os.environ, {'DATA_DIR': tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = Path(tmp.name) / VOICE_ACTIVITY_FILE
        self.music = make_music()

    def test_connected_voice_client_touches_the_file(self):
        self.music.bot.voice_clients = [make_voice_client()]

        self.music.mark_voice_activity()

        self.assertTrue(self.path.exists())

    def test_touching_again_refreshes_the_file(self):
        self.music.bot.voice_clients = [make_voice_client()]
        self.music.mark_voice_activity()
        os.utime(self.path, (0, 0))

        self.music.mark_voice_activity()

        self.assertGreater(self.path.stat().st_mtime, 0)

    def test_no_voice_client_removes_the_file(self):
        self.path.touch()

        self.music.mark_voice_activity()

        self.assertFalse(self.path.exists())

    def test_disconnected_voice_client_removes_the_file(self):
        self.path.touch()
        vc = make_voice_client()
        vc.is_connected.return_value = False
        self.music.bot.voice_clients = [vc]

        self.music.mark_voice_activity()

        self.assertFalse(self.path.exists())

    def test_write_errors_are_logged_not_raised(self):
        self.music.bot.voice_clients = [make_voice_client()]

        with patch.object(Path, 'touch', side_effect=PermissionError), \
                self.assertLogs('cogs.music', level='ERROR'):
            self.music.mark_voice_activity()

    async def test_joining_voice_marks_activity_right_away(self):
        vc = make_voice_client()
        channel = SimpleNamespace(connect=AsyncMock(return_value=vc), members=[])
        ctx = make_ctx()
        ctx.author.voice.channel = channel
        self.music.voice_library_supports_current_discord_protocol = lambda: True

        async def connect(timeout):
            self.music.bot.voice_clients = [vc]
            return vc

        channel.connect = connect
        await self.music.ensure_voice_client(ctx)
        self.music.cancel_inactivity_timer('123')

        self.assertTrue(self.path.exists())


if __name__ == '__main__':
    unittest.main()
