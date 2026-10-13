from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from cogs.music import Music


def make_music(**attrs):
    """Build a Music cog without running __init__ side effects."""
    music = Music.__new__(Music)
    music.bot = SimpleNamespace(loop=None, wait_for=AsyncMock())
    music.cookies_file = None
    music.queue = {}
    music.voice_clients = {}
    music.current_songs = {}
    music.current_urls = {}
    music.loop_enabled = {}
    music.skip_requested = set()
    music.search_results = {}
    music.stream_cache = {}
    music.downloaded_titles = {}
    music.cache_timeout = 3600
    music.cache_dir = Path('.private') / 'temp_audio'
    music.playback_locks = {}
    music.ytmusic = None
    music.ydl_opts = {'http_headers': {'User-Agent': 'test'}}
    music.FFMPEG_OPTIONS = {}
    for name, value in attrs.items():
        setattr(music, name, value)
    return music


def make_voice_client(*, playing=False, paused=False, channel=None):
    voice_client = Mock()
    voice_client.is_playing.return_value = playing
    voice_client.is_paused.return_value = paused
    voice_client.is_connected.return_value = True
    voice_client.channel = channel
    return voice_client


def make_member(*, channel=None, move_members=False):
    return SimpleNamespace(
        id=1,
        voice=SimpleNamespace(channel=channel) if channel is not None else None,
        guild_permissions=SimpleNamespace(move_members=move_members),
    )


def make_ctx(*, author=None, voice_client=None):
    return SimpleNamespace(
        author=author or make_member(channel=object()),
        guild=SimpleNamespace(id=123, voice_client=voice_client),
        channel=object(),
        send=AsyncMock(),
    )


def sent_text(ctx):
    """Join every plain-text message sent through a mocked context."""
    return "\n".join(
        str(call.args[0]) for call in ctx.send.await_args_list if call.args
    )
