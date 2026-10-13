import asyncio
import logging
import os
import re
import time
import traceback
import urllib.parse
from pathlib import Path

import discord
import yt_dlp
from discord.ext import commands
from ytmusicapi import YTMusic

from magna import matching, spotify, youtube

logger = logging.getLogger(__name__)


class VoiceGatewayDiagnosticHandler(logging.Handler):
    """Captures the latest voice WebSocket close code from discord.py logs."""

    close_code_pattern = re.compile(r'WebSocket closed with (\d+)')

    def __init__(self, cog):
        super().__init__(level=logging.INFO)
        self.cog = cog

    def emit(self, record):
        message = record.getMessage()
        match = self.close_code_pattern.search(message)
        if match:
            self.cog.note_voice_close_code(int(match.group(1)))


MIN_DISCORD_DAVE_VERSION = (2, 7, 0)

URL_SCHEME_PATTERN = re.compile(r'^[a-z][a-z0-9+.-]*://', re.IGNORECASE)
UNSUPPORTED_URL_MESSAGE = (
    "❌ Only YouTube video links and Spotify track, album and playlist links are supported."
)
# Each queued Spotify track costs a YouTube lookup when its turn comes.
# Spotify's embed pages list at most 100 tracks anyway, without the total.
SPOTIFY_COLLECTION_LIMIT = 100
QUEUE_DISPLAY_LIMIT = 20
NOT_IN_PLAYER_CHANNEL_MESSAGE = "❌ You need to be in my voice channel to do that."
# Stream URLs carry their own expiry; stop reusing them a bit before it.
STREAM_EXPIRY_MARGIN = 300
# Downloaded fallback tracks kept on disk; older ones are deleted.
MAX_DOWNLOADED_TRACKS = 10
# Finished downloads; yt-dlp's partial and intermediate files are skipped.
DOWNLOADED_AUDIO_SUFFIXES = {'.mp3', '.m4a', '.webm', '.opus', '.ogg'}
# Seconds before leaving a voice channel where nothing is playing ('idle')
# or where nobody but bots is left listening ('alone').
INACTIVITY_DELAYS = {'idle': 300, 'alone': 120}
INACTIVITY_MESSAGES = {
    'idle': "👋 Left the voice channel since nothing was playing.",
    'alone': "👋 Left the voice channel since everyone left.",
}

def track_details(info):
    """Pull the title, artist and duration out of yt-dlp's video info."""
    info = info or {}
    artist = info.get('artist') or info.get('creator') or info.get('uploader') or info.get('channel')
    if artist and artist.endswith(' - Topic'):
        # YouTube Music's auto-generated artist channels.
        artist = artist[:-len(' - Topic')]
    return {
        'title': info.get('track') or info.get('title') or 'Unknown Title',
        'artist': artist,
        'duration': info.get('duration'),
    }


def format_duration(seconds):
    """Format seconds as m:ss or h:mm:ss; None when unknown."""
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return None
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_clients = {}
        self.current_songs = {}
        self.current_urls = {}
        self.loop_enabled = {}
        self.skip_requested = set()
        self.queue = {}
        self.playback_locks = {}
        self.inactivity_timers = {}  # guild_id -> (reason, Task)
        self.announce_channels = {}  # guild_id -> text channel of the last %play
        self.ytmusic = None  # created on first use
        self.stream_cache = {}  # Cache for successful stream URLs
        self.downloaded_tracks = {}  # video_id -> details of a downloaded track
        self.cache_timeout = 3600  # Cache timeout in seconds (1 hour)
        self.last_voice_close_code = None
        self.last_voice_close_time = 0.0

        # Create private directories
        self.private_dir = Path('.private')
        self.private_dir.mkdir(exist_ok=True)
        
        # Create cache directory inside private directory
        self.cache_dir = self.private_dir / 'temp_audio'
        self.cache_dir.mkdir(exist_ok=True)
        # Downloads from a previous run have no known title; start clean.
        for leftover in self.cache_dir.iterdir():
            if leftover.is_file():
                leftover.unlink(missing_ok=True)

        # A cookies file provided by the operator is only ever read. Cookies
        # from YOUTUBE_COOKIES are written to a separate, generated file.
        self.cookies_file = Path('youtube_cookies.txt')
        self.generated_cookies_file = self.private_dir / 'youtube_cookies.txt'
        
        # YT-DLP configuration with more robust settings
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': False,
            'geo_bypass': True,
            'geo_bypass_country': 'US',
            'socket_timeout': 10,
            'retries': 5,
            'extractor_retries': 5,
            'fragment_retries': 5,
            'extract_flat': 'in_playlist',
            'concurrent_fragment_downloads': 5,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android']
                }
            },
            'cookiefile': str(self.cookies_file) if self.cookies_file.exists() else None,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Referer': 'https://www.youtube.com/',
                'X-YouTube-Client-Name': '1',
                'X-YouTube-Client-Version': '2.20230120.00.00'
            }
        }
        
        # FFMPEG options with more robust settings
        self.FFMPEG_OPTIONS = {
            'before_options': '-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 -reconnect_on_network_error 1 -reconnect_delay_max 5 -loglevel warning',
            'options': '-vn -bufsize 1024k -af aresample=async=1:first_pts=0',
        }
        
        self.voice_diagnostic_handler = VoiceGatewayDiagnosticHandler(self)
        logging.getLogger('discord.voice_state').addHandler(self.voice_diagnostic_handler)

    async def cog_check(self, ctx):
        # Every command here works on a server's state or voice channel.
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return True

    def cog_unload(self):
        logging.getLogger('discord.voice_state').removeHandler(self.voice_diagnostic_handler)
        self.generated_cookies_file.unlink(missing_ok=True)
        for guild_id in list(self.inactivity_timers):
            self.cancel_inactivity_timer(guild_id)

    def note_voice_close_code(self, code):
        self.last_voice_close_code = code
        self.last_voice_close_time = time.monotonic()

    def voice_library_supports_current_discord_protocol(self):
        version = getattr(discord, 'version_info', None)
        if version is None:
            return False

        current_version = (version.major, version.minor, version.micro)
        return current_version >= MIN_DISCORD_DAVE_VERSION

    def get_recent_voice_close_code(self, max_age=30):
        if self.last_voice_close_code is None:
            return None

        if time.monotonic() - self.last_voice_close_time > max_age:
            return None

        return self.last_voice_close_code

    def build_voice_connection_error(self):
        if not self.voice_library_supports_current_discord_protocol():
            return (
                f"❌ A biblioteca de voz instalada (`discord.py {discord.__version__}`) "
                f"é incompatível com o protocolo atual do Discord. "
                f"Atualize para `discord.py[voice]>=2.7.1` e reinstale as dependências do bot."
            )

        code = self.get_recent_voice_close_code()

        if code == 4017:
            return (
                f"❌ O Discord recusou a conexão de voz com o código `4017`. "
                f"Desde 2026-03-01, canais de voz comuns exigem o protocolo DAVE/E2EE, "
                f"e a instalação atual com `discord.py {discord.__version__}` não suporta esse handshake."
            )

        if code == 4006:
            return (
                f"❌ O Discord encerrou o handshake de voz com o código `4006` "
                f"(sessão inválida). Com `discord.py {discord.__version__}`, isso normalmente indica "
                f"incompatibilidade com o protocolo de voz atual do Discord."
            )

        return "❌ Não foi possível entrar no canal de voz."

    async def disconnect_voice_client(self, guild_id, guild=None):
        self.cancel_inactivity_timer(guild_id)
        vc = self.voice_clients.get(guild_id)
        if vc is None and guild is not None:
            vc = guild.voice_client

        if vc is None:
            return

        try:
            if vc.is_connected():
                await vc.disconnect(force=True)
        except Exception:
            logger.exception("Failed to disconnect stale voice client for guild %s", guild_id)
        finally:
            self.voice_clients.pop(guild_id, None)
            self.current_songs.pop(guild_id, None)
            self.current_urls.pop(guild_id, None)
            self.loop_enabled.pop(guild_id, None)
            self.skip_requested.discard(guild_id)

    async def ensure_voice_client(self, ctx):
        if not self.voice_library_supports_current_discord_protocol():
            raise RuntimeError(self.build_voice_connection_error())

        guild_id = str(ctx.guild.id)
        channel = ctx.author.voice.channel
        vc = ctx.guild.voice_client or self.voice_clients.get(guild_id)

        if vc is not None and not vc.is_connected():
            await self.disconnect_voice_client(guild_id, ctx.guild)
            vc = None

        if vc is None:
            vc = await channel.connect(timeout=8.0)
        elif vc.channel != channel:
            await vc.move_to(channel)

        self.voice_clients[guild_id] = vc
        self.announce_channels[guild_id] = ctx.channel
        # Covers joining and then playing nothing, e.g. a cancelled search.
        self.refresh_inactivity_timer(ctx.guild)
        return vc

    @staticmethod
    def is_alone(vc):
        """Whether nobody but bots is left in the bot's voice channel."""
        members = getattr(vc.channel, 'members', None)
        if members is None:
            return False
        return not any(not member.bot for member in members)

    def inactivity_reason(self, guild):
        """Why the bot should leave voice ('idle' or 'alone'), or None to stay."""
        guild_id = str(guild.id)
        vc = guild.voice_client or self.voice_clients.get(guild_id)
        if vc is None or not vc.is_connected():
            return None
        if self.is_alone(vc):
            return 'alone'
        if (
            self.is_player_busy(vc)
            or self.queue.get(guild_id)
            or self.is_resolving_track(guild_id)
        ):
            return None
        return 'idle'

    def cancel_inactivity_timer(self, guild_id):
        timer = self.inactivity_timers.pop(guild_id, None)
        if timer is not None:
            timer[1].cancel()

    def refresh_inactivity_timer(self, guild):
        """Start, keep or cancel the countdown to leaving voice.

        Called whenever playback or the voice channel changes. A countdown
        that is still valid keeps running instead of starting over.
        """
        guild_id = str(guild.id)
        reason = self.inactivity_reason(guild)
        timer = self.inactivity_timers.get(guild_id)
        if timer is not None and timer[0] == reason:
            return

        self.cancel_inactivity_timer(guild_id)
        if reason is not None:
            task = asyncio.create_task(self.leave_when_inactive(guild, reason))
            self.inactivity_timers[guild_id] = (reason, task)

    async def leave_when_inactive(self, guild, reason):
        await asyncio.sleep(INACTIVITY_DELAYS[reason])

        guild_id = str(guild.id)
        timer = self.inactivity_timers.get(guild_id)
        if timer is not None and timer[1] is asyncio.current_task():
            # Leaving cancels the timer; it must not cancel this very task.
            del self.inactivity_timers[guild_id]

        # Whoever changes the state again (e.g. a track still being looked
        # up) refreshes the timer, so it's fine to just stop here.
        reason = self.inactivity_reason(guild)
        if reason is None:
            return

        channel = self.announce_channels.pop(guild_id, None)
        logger.info("Leaving voice in guild %s (%s)", guild_id, reason)
        await self.leave_voice(guild)
        if channel is not None:
            try:
                await channel.send(INACTIVITY_MESSAGES[reason])
            except discord.HTTPException:
                logger.warning("Could not announce leaving voice in guild %s", guild_id)

    async def leave_voice(self, guild):
        """Clear the guild's player and disconnect from voice."""
        guild_id = str(guild.id)
        # Clear first: disconnecting ends the track and fires play_next.
        self.queue[guild_id] = []
        self.loop_enabled[guild_id] = False
        self.current_urls.pop(guild_id, None)
        await self.disconnect_voice_client(guild_id, guild)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel:
            return  # mute, deafen, etc.
        vc = member.guild.voice_client
        if vc is None:
            return
        if member.id == self.bot.user.id or vc.channel in (before.channel, after.channel):
            self.refresh_inactivity_timer(member.guild)

    @staticmethod
    def is_player_busy(vc):
        """A paused track still owns the player, so treat it like a playing one."""
        return vc is not None and (vc.is_playing() or vc.is_paused())

    def playback_lock(self, guild_id):
        """Serialize "is anything playing? then start a track" per guild."""
        return self.playback_locks.setdefault(guild_id, asyncio.Lock())

    @staticmethod
    def can_control_player(ctx, vc):
        """Only listeners in the bot's voice channel (or moderators) may control it."""
        if vc is None or not vc.is_connected():
            return True
        if ctx.author.guild_permissions.move_members:
            return True
        author_channel = ctx.author.voice.channel if ctx.author.voice else None
        return author_channel == vc.channel

    async def play_or_enqueue(self, ctx, vc, url, title=None):
        """Start the track right away, or queue it when the player is busy.

        Returns the queue entry when the track was queued, otherwise None.
        """
        guild_id = str(ctx.guild.id)
        try:
            async with self.playback_lock(guild_id):
                if self.is_player_busy(vc):
                    entry = {'url': url, 'title': title or url}
                    self.queue.setdefault(guild_id, []).append(entry)
                    return entry
                await self.play_song(ctx, url)
                return None
        finally:
            self.refresh_inactivity_timer(ctx.guild)

    def is_resolving_track(self, guild_id):
        """A track is being looked up for this guild but hasn't started yet."""
        lock = self.playback_locks.get(guild_id)
        return lock is not None and lock.locked()

    async def extract_info(self, opts, url, *, download=False):
        """Run yt-dlp in a worker thread so slow extractions don't stall the bot."""
        def run():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=download)

        return await asyncio.to_thread(run)

    def after_playback(self, ctx, error):
        """Called from discord.py's audio thread when a track ends."""
        if error:
            logger.error(f"Playback error in guild {ctx.guild.id}: {error}")
        # Don't block the audio thread while the next track is resolved.
        asyncio.run_coroutine_threadsafe(self.play_next(ctx), self.bot.loop)

    async def play_next(self, ctx):
        try:
            await self.start_next_track(ctx)
        finally:
            # The queue may have run out, so the bot may now be idle.
            self.refresh_inactivity_timer(ctx.guild)

    async def start_next_track(self, ctx):
        guild_id = str(ctx.guild.id)
        async with self.playback_lock(guild_id):
            skipped = guild_id in self.skip_requested
            self.skip_requested.discard(guild_id)

            vc = self.voice_clients.get(guild_id)

            # A command may have started another track while we waited.
            if self.is_player_busy(vc):
                return

            # The last track is over; play_song sets this again for the next.
            self.current_songs.pop(guild_id, None)

            if vc is None or not vc.is_connected():
                return

            if not skipped and self.loop_enabled.get(guild_id) and self.current_urls.get(guild_id):
                if await self.play_song(ctx, self.current_urls[guild_id]):
                    return

            # A track that can't be played must not stall the rest of the queue.
            while self.queue.get(guild_id) and vc.is_connected():
                pending = self.queue[guild_id]
                entry = pending.pop(0)
                url = entry['url'] or await self.find_queued_track(ctx, entry)
                # %stop and %leave replace the queue; don't play into a cleared one.
                if self.queue.get(guild_id) is not pending:
                    return
                if url and await self.play_song(ctx, url):
                    return

    async def find_queued_track(self, ctx, entry):
        """Find the YouTube video for a queued Spotify track, e.g. from a playlist."""
        url = await self.find_spotify_match(entry['spotify'])
        if not url:
            await ctx.send(self.no_match_message(entry['spotify']))
        return url

    def is_cache_entry_fresh(self, cache_data, now):
        if now - cache_data['timestamp'] >= self.cache_timeout:
            return False
        expiry = youtube.stream_url_expiry(cache_data['url'])
        return expiry is None or now < expiry - STREAM_EXPIRY_MARGIN

    async def get_cached_stream(self, video_id):
        """Return the cached {'url', 'title'} for a video, unless it expired."""
        cache_data = self.stream_cache.get(video_id)
        if cache_data and self.is_cache_entry_fresh(cache_data, time.time()):
            return {
                'url': cache_data['url'],
                'title': cache_data.get('title'),
                'artist': cache_data.get('artist'),
                'duration': cache_data.get('duration'),
            }
        return None

    async def cache_stream(self, video_id, stream_url, title=None, artist=None, duration=None):
        """Cache stream URL and track details with timestamp"""
        if not self.is_valid_stream_url(stream_url):
            logger.warning(f"Refusing to cache invalid stream URL for video {video_id}")
            return

        now = time.time()
        expired = [
            cached_id for cached_id, cache_data in self.stream_cache.items()
            if not self.is_cache_entry_fresh(cache_data, now)
        ]
        for cached_id in expired:
            del self.stream_cache[cached_id]

        self.stream_cache[video_id] = {
            'url': stream_url,
            'title': title,
            'artist': artist,
            'duration': duration,
            'timestamp': now
        }

    def is_valid_stream_url(self, stream_url):
        """Return True when a stream URL is non-empty and has a usable shape."""
        if not isinstance(stream_url, str):
            return False

        stream_url = stream_url.strip()
        if not stream_url:
            return False

        parsed = urllib.parse.urlparse(stream_url)
        if parsed.scheme in ('http', 'https'):
            return bool(parsed.netloc)

        # Local playback is only allowed for files this cog downloaded itself.
        path = Path(stream_url).resolve()
        return path.is_file() and path.is_relative_to(self.cache_dir.resolve())

    def normalize_stream_data(self, stream_data, *, is_local=False):
        """Validate stream data and normalize common fields."""
        if not isinstance(stream_data, dict):
            return None

        stream_url = stream_data.get('url')
        if not self.is_valid_stream_url(stream_url):
            logger.warning(f"Ignoring invalid stream data: {stream_data}")
            return None

        normalized = stream_data.copy()
        normalized['url'] = stream_url.strip()
        normalized['title'] = normalized.get('title') or 'Unknown Title'
        if is_local:
            normalized['is_local'] = True

        return normalized

    def find_downloaded_audio(self, video_id):
        """Find a downloaded audio file for a video ID regardless of final extension."""
        candidates = sorted(
            (
                path for path in self.cache_dir.glob(f"{video_id}.*")
                if path.suffix in DOWNLOADED_AUDIO_SUFFIXES
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True
        )
        return candidates[0] if candidates else None

    def prune_downloaded_audio(self, keep=MAX_DOWNLOADED_TRACKS):
        """Delete all but the `keep` most recently downloaded tracks."""
        files = sorted(
            (path for path in self.cache_dir.iterdir() if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True
        )
        for path in files[keep:]:
            # ffmpeg keeps reading a file that is still playing after unlink.
            path.unlink(missing_ok=True)
            self.downloaded_tracks.pop(path.stem, None)

    async def get_downloaded_stream(self, video_id, normalized_url):
        """Play from a downloaded copy of the track, downloading it if needed."""
        downloaded_path = self.find_downloaded_audio(video_id)
        if downloaded_path is None:
            download_opts = self.ydl_opts.copy()
            download_opts['outtmpl'] = str(self.cache_dir / f"{video_id}.%(ext)s")
            info = await self.extract_info(download_opts, normalized_url, download=True)
            downloaded_path = self.find_downloaded_audio(video_id)
            if downloaded_path is None:
                return None
            self.downloaded_tracks[video_id] = track_details(info)
            self.prune_downloaded_audio()

        details = self.downloaded_tracks.get(video_id) or {}
        return self.normalize_stream_data({
            **details,
            'url': str(downloaded_path),
            'title': details.get('title') or "Downloaded Song",
        }, is_local=True)

    async def update_cookies(self):
        """Update cookies from environment variable"""
        try:
            new_cookies = os.getenv('YOUTUBE_COOKIES', '')
            if not new_cookies:
                logger.warning("YouTube asked for sign-in, but YOUTUBE_COOKIES is empty")
                return False

            # Never overwrite the operator's cookies file; write our own,
            # readable only by the bot.
            self.generated_cookies_file.touch(mode=0o600, exist_ok=True)
            self.generated_cookies_file.write_text(new_cookies)

            # Update the ydl_opts with new cookie file
            self.ydl_opts['cookiefile'] = str(self.generated_cookies_file)
            self.ydl_opts['cookiesfrombrowser'] = None

            logger.info("Loaded YouTube cookies from YOUTUBE_COOKIES")
            return True

        except Exception:
            logger.exception("Error updating YouTube cookies")
            return False

    @staticmethod
    def is_bot_check(error):
        return "Sign in to confirm you're not a bot" in str(error)

    async def extract_with_cookie_retry(self, opts_factory, url):
        """Run yt-dlp, retrying once with cookies when YouTube asks for sign-in.

        `opts_factory` builds the options, so the retry sees updated cookies.
        """
        try:
            return await self.extract_info(opts_factory(), url)
        except Exception as e:
            if not self.is_bot_check(e) or not await self.update_cookies():
                raise
            logger.info("Retrying %s with cookies after YouTube's bot check", url)
            return await self.extract_info(opts_factory(), url)

    async def play_song(self, ctx, url):
        """Play a YouTube video, falling back to downloading it.

        Returns True once playback has started.
        """
        try:
            video_id = youtube.video_id(url)
            if not video_id:
                logger.warning(f"Refusing unsupported URL: {url}")
                await ctx.send(UNSUPPORTED_URL_MESSAGE)
                return

            normalized_url = f"https://www.youtube.com/watch?v={video_id}"
            logger.info(f"Processing YouTube URL: {url}, Video ID: {video_id}")

            # Check cache first
            cached = await self.get_cached_stream(video_id)
            if cached and self.is_valid_stream_url(cached['url']):
                stream_data = {**cached, 'title': cached['title'] or 'Unknown Title'}
            else:
                if cached:
                    logger.warning(f"Dropping invalid cached stream URL for video {video_id}")
                    self.stream_cache.pop(video_id, None)

                stream_data = None
                try:
                    info = await self.extract_with_cookie_retry(lambda: self.ydl_opts, normalized_url)
                    if info and info.get('url'):
                        stream_data = self.normalize_stream_data({
                            **track_details(info),
                            'url': info['url'],
                        })
                except Exception as e:
                    logger.error(f"yt-dlp error: {str(e)}")

                # Downloading the track is slower, so it's the last resort.
                if not stream_data:
                    await ctx.send("⏳ Trying alternative source...")
                    try:
                        stream_data = await self.get_downloaded_stream(video_id, normalized_url)
                    except Exception as e:
                        logger.error(f"Download fallback failed: {str(e)}")

                stream_data = self.normalize_stream_data(stream_data)

                if not stream_data:
                    await ctx.send("❌ Unable to play this track. Please try another.")
                    return

                # Cache successful stream URL if it's not a local file
                if not stream_data.get('is_local', False):
                    await self.cache_stream(
                        video_id, stream_data['url'], stream_data['title'],
                        stream_data.get('artist'), stream_data.get('duration')
                    )
            
            # Create audio source and play
            try:
                stream_data = self.normalize_stream_data(
                    stream_data,
                    is_local=stream_data.get('is_local', False)
                )
                if not stream_data:
                    await ctx.send("❌ Unable to play this track.")
                    return

                # ffmpeg only opens the URL once playback starts, so an expired
                # stream can't be detected here; get_cached_stream drops
                # stream URLs before their `expire` time instead.
                source = discord.FFmpegOpusAudio(stream_data['url'], **self.FFMPEG_OPTIONS)
            except Exception as e:
                logger.error(f"Error creating audio source: {str(e)}")
                await ctx.send("❌ Unable to play this track.")
                return
            
            # The queue's "Now Playing" line repeats the same details.
            self.current_songs[str(ctx.guild.id)] = " • ".join(
                part for part in (
                    stream_data['title'],
                    stream_data.get('artist'),
                    format_duration(stream_data.get('duration')),
                ) if part
            )
            self.current_urls[str(ctx.guild.id)] = url
            
            vc = self.voice_clients[str(ctx.guild.id)]
            if not vc.is_connected():
                await ctx.send("❌ Voice connection lost.")
                return
            
            vc.play(source, after=lambda e: self.after_playback(ctx, e))
            
            embed = discord.Embed(
                title="🎵 Now Playing",
                description=f"**{stream_data['title']}**",
                color=discord.Color.green()
            )
            if stream_data.get('artist'):
                embed.add_field(name="Artist", value=stream_data['artist'], inline=True)
            duration = format_duration(stream_data.get('duration'))
            if duration:
                embed.add_field(name="Duration", value=duration, inline=True)
            await ctx.send(embed=embed)
            return True

        except Exception as e:
            logger.error(f"Play song error: {str(e)}\n{traceback.format_exc()}")
            await ctx.send("❌ An error occurred while trying to play the track.")
        return False

    async def search_youtube(self, query):
        """Search YouTube for up to 10 videos."""
        def search_opts():
            return {
                'format': 'bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'default_search': 'ytsearch10',  # Search for 10 videos
                'cookiefile': self.ydl_opts.get('cookiefile'),
                'cookiesfrombrowser': self.ydl_opts.get('cookiesfrombrowser'),
                'http_headers': self.ydl_opts.get('http_headers')
            }

        try:
            info = await self.extract_with_cookie_retry(search_opts, f"ytsearch10:{query}")
        except Exception as e:
            logger.error(f"yt-dlp search error: {str(e)}")
            return []

        return [
            {
                'id': entry.get('id'),
                'title': entry.get('title', 'Unknown Title'),
                'duration_string': entry.get('duration_string', 'Unknown'),
                'webpage_url': entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
            }
            for entry in (info or {}).get('entries') or []
        ]

    def spotify_user_agent(self):
        return self.ydl_opts['http_headers']['User-Agent']

    async def handle_spotify_url(self, url):
        """Resolve a Spotify track to its name, artists and duration."""
        return await spotify.resolve_track(url, user_agent=self.spotify_user_agent())

    async def handle_spotify_collection_url(self, url):
        """Resolve a Spotify album or playlist to its name and tracks."""
        return await spotify.resolve_collection(url, user_agent=self.spotify_user_agent())

    async def follow_spotify_short_link(self, url):
        return await spotify.follow_short_link(url, user_agent=self.spotify_user_agent())

    async def ytmusic_candidates(self, query, kind):
        """Search YouTube Music ("songs" or "videos") without signing in."""
        if self.ytmusic is None:
            self.ytmusic = await asyncio.to_thread(YTMusic)
        results = await asyncio.to_thread(self.ytmusic.search, query, filter=kind, limit=10)
        candidates = []
        for result in results[:10]:
            video_id = result.get('videoId') or ''
            if not youtube.VIDEO_ID_PATTERN.fullmatch(video_id):
                continue
            artists = " ".join(
                artist.get('name') or ''
                for artist in result.get('artists') or []
                if isinstance(artist, dict)
            )
            title = result.get('title') or ''
            candidates.append({
                'video_id': video_id,
                'title': title,
                # Songs credit their artists; videos may only name them in the title.
                'artist_text': artists if kind == 'songs' else f"{artists} {title}",
                'duration': result.get('duration_seconds'),
            })
        return candidates

    async def youtube_candidates(self, query):
        """Search regular YouTube with yt-dlp."""
        def search_opts():
            return {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'cookiefile': self.ydl_opts.get('cookiefile'),
                'http_headers': self.ydl_opts.get('http_headers'),
            }

        info = await self.extract_with_cookie_retry(search_opts, f"ytsearch10:{query}")
        candidates = []
        for entry in (info or {}).get('entries') or []:
            video_id = entry.get('id') or ''
            if not youtube.VIDEO_ID_PATTERN.fullmatch(video_id):
                continue
            title = entry.get('title') or ''
            channel = entry.get('channel') or entry.get('uploader') or ''
            candidates.append({
                'video_id': video_id,
                'title': title,
                'artist_text': f"{channel} {title}",
                'duration': entry.get('duration'),
            })
        return candidates

    async def find_spotify_match(self, track):
        """Return the YouTube URL of the same recording as a Spotify track, or None.

        Official song uploads on YouTube Music are tried first, then its videos,
        then regular YouTube. Results that don't match the track's title,
        artists and duration are never played.
        """
        query = " ".join([track['name'], *track['artists']])
        searches = (
            ("YouTube Music songs", lambda: self.ytmusic_candidates(query, 'songs')),
            ("YouTube Music videos", lambda: self.ytmusic_candidates(query, 'videos')),
            ("YouTube", lambda: self.youtube_candidates(query)),
        )
        for label, search in searches:
            try:
                candidates = await search()
            except Exception as e:
                logger.warning("%s search failed for %r: %s", label, query, e)
                continue

            match = matching.pick_match(track, candidates)
            if match:
                logger.info("Matched %r to %s %s", query, label, match['video_id'])
                return f"https://www.youtube.com/watch?v={match['video_id']}"

        logger.info("No reliable YouTube match for %r", query)
        return None

    @staticmethod
    def no_match_message(track):
        return (
            f"⏭️ I couldn't find a reliable match for **{spotify.track_title(track)}** "
            f"on YouTube, so I skipped it."
        )

    async def play_spotify(self, ctx, vc, reference):
        """Play a Spotify track, or queue every track of an album or playlist."""
        if spotify.is_short_link(reference):
            await ctx.send("🎵 Resolving Spotify link...")
            reference = await self.follow_spotify_short_link(reference) or reference

        if spotify.entity(reference, spotify.COLLECTION_KINDS):
            await ctx.send("🎵 Reading Spotify tracks...")
            collection = await self.handle_spotify_collection_url(reference)
            if not collection:
                return await ctx.send("❌ I couldn't read the tracks of this Spotify album or playlist.")
            return await self.enqueue_spotify_collection(ctx, vc, collection)

        await ctx.send("🎵 Resolving Spotify track...")
        track = await self.handle_spotify_url(reference)
        if not track:
            return await ctx.send(
                "❌ I couldn't read this Spotify track. "
                "Only track, album and playlist links are supported."
            )

        url = await self.find_spotify_match(track)
        if not url:
            return await ctx.send(self.no_match_message(track))

        title = spotify.track_title(track)
        if await self.play_or_enqueue(ctx, vc, url, title):
            await ctx.send(f"🎵 Added to queue: **{title}**")

    async def enqueue_spotify_collection(self, ctx, vc, collection):
        """Queue an album's or playlist's tracks and start playing if idle.

        Each track is looked up on YouTube only when its turn comes, so a long
        playlist doesn't fire a burst of searches.
        """
        guild_id = str(ctx.guild.id)
        tracks = collection['tracks'][:SPOTIFY_COLLECTION_LIMIT]
        entries = [
            {'url': None, 'spotify': track, 'title': spotify.track_title(track)}
            for track in tracks
        ]

        async with self.playback_lock(guild_id):
            self.queue.setdefault(guild_id, []).extend(entries)
            idle = not self.is_player_busy(vc)

        message = f"🎵 Added {len(entries)} tracks from **{collection['name']}** to the queue"
        # Spotify doesn't say how long the playlist really is, so a full
        # page may mean it was cut short.
        if len(collection['tracks']) >= SPOTIFY_COLLECTION_LIMIT:
            message += (
                f". Only the first {SPOTIFY_COLLECTION_LIMIT} tracks can be read, "
                f"so any tracks after those weren't added."
            )
        await ctx.send(message)

        if idle:
            await self.play_next(ctx)

    @commands.command()
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def play(self, ctx, *, query):
        """Play a song by URL (YouTube/Spotify) or search term"""
        try:
            if not ctx.author.voice:
                return await ctx.send("❌ You need to be in a voice channel!")

            guild_id = str(ctx.guild.id)

            # Don't let someone in another channel pull the bot away from
            # people who are still listening.
            current_vc = ctx.guild.voice_client or self.voice_clients.get(guild_id)
            in_use = (
                self.is_player_busy(current_vc)
                or self.queue.get(guild_id)
                or self.is_resolving_track(guild_id)
            )
            if in_use and not self.can_control_player(ctx, current_vc):
                return await ctx.send(NOT_IN_PLAYER_CHANNEL_MESSAGE)

            try:
                vc = await self.ensure_voice_client(ctx)
            except discord.errors.ConnectionClosed:
                await self.disconnect_voice_client(guild_id, ctx.guild)
                await ctx.send(self.build_voice_connection_error())
                return
            except asyncio.TimeoutError:
                await self.disconnect_voice_client(guild_id, ctx.guild)
                await ctx.send(self.build_voice_connection_error())
                return
            except RuntimeError as e:
                logger.error(f"Voice runtime error: {str(e)}")
                await self.disconnect_voice_client(guild_id, ctx.guild)
                await ctx.send(str(e))
                return
            except Exception as e:
                logger.error(f"Voice connection error: {str(e)}")
                await self.disconnect_voice_client(guild_id, ctx.guild)
                await ctx.send(self.build_voice_connection_error())
                return

            if guild_id not in self.queue:
                self.queue[guild_id] = []

            if spotify.is_spotify_reference(query):
                return await self.play_spotify(ctx, vc, query)

            if URL_SCHEME_PATTERN.match(query):
                url = youtube.normalize_url(query)
                if not url:
                    return await ctx.send(UNSUPPORTED_URL_MESSAGE)

                try:
                    entry = await self.play_or_enqueue(ctx, vc, url)
                except Exception as e:
                    logger.error(f"YouTube playback error: {str(e)}")
                    await ctx.send("❌ Unable to play this track.")
                    return

                if entry:
                    # The track is already queued; failing to look up its
                    # title only means the queue shows the URL instead.
                    try:
                        info = await self.extract_info(self.ydl_opts, url)
                        entry['title'] = (info or {}).get('title') or entry['title']
                    except Exception as e:
                        logger.warning(f"Could not fetch title for queued {url}: {str(e)}")
                    await ctx.send(f"🎵 Added to queue: **{entry['title']}**")
            else:
                await ctx.send("🔍 Searching...")
                results = await self.search_youtube(query)
                if not results:
                    return await ctx.send("❌ No results found.")

                embed = discord.Embed(
                    title="🔍 Search Results",
                    description="Type the number (1-10) or 'cancel':",
                    color=discord.Color.blue()
                )

                for i, entry in enumerate(results, 1):
                    title = entry['title']
                    duration = entry.get('duration_string', 'Unknown')
                    embed.add_field(
                        name=f"{i}. {title}",
                        value=f"Duration: {duration}",
                        inline=False
                    )

                await ctx.send(embed=embed)

                def check(m):
                    return (
                        m.author == ctx.author and
                        m.channel == ctx.channel and
                        (m.content.lower() == 'cancel' or m.content.isdigit() and 1 <= int(m.content) <= len(results))
                    )

                try:
                    msg = await self.bot.wait_for('message', check=check, timeout=30.0)
                    if msg.content.lower() == 'cancel':
                        return await ctx.send("❌ Search cancelled.")

                    selected = results[int(msg.content) - 1]
                    if await self.play_or_enqueue(ctx, vc, selected['webpage_url'], selected['title']):
                        await ctx.send(f"🎵 Added to queue: **{selected['title']}**")

                except asyncio.TimeoutError:
                    await ctx.send("❌ Search timed out.")
                except Exception as e:
                    logger.error(f"Search selection error: {str(e)}")
                    await ctx.send("❌ An error occurred while processing your selection.")

        except Exception as e:
            logger.error(f"Play command error: {str(e)}\n{traceback.format_exc()}")
            await ctx.send("❌ An error occurred while processing your request.")

    @commands.command()
    async def stop(self, ctx):
        """Stop playing and clear the queue"""
        guild_id = str(ctx.guild.id)
        vc = ctx.guild.voice_client or self.voice_clients.get(guild_id)
        if vc:
            if not self.can_control_player(ctx, vc):
                return await ctx.send(NOT_IN_PLAYER_CHANNEL_MESSAGE)
            self.voice_clients[guild_id] = vc
            self.queue[guild_id] = []
            self.loop_enabled[guild_id] = False
            self.current_urls.pop(guild_id, None)
            if self.is_player_busy(vc):
                self.skip_requested.add(guild_id)
                vc.stop()
            await ctx.send("⏹️ Stopped playing and cleared the queue")

    @commands.command()
    async def skip(self, ctx):
        """Skip the current song"""
        guild_id = str(ctx.guild.id)
        vc = ctx.guild.voice_client or self.voice_clients.get(guild_id)
        if vc:
            if not self.can_control_player(ctx, vc):
                return await ctx.send(NOT_IN_PLAYER_CHANNEL_MESSAGE)
            self.voice_clients[guild_id] = vc
            if self.is_player_busy(vc):
                self.skip_requested.add(guild_id)
                vc.stop()
                await ctx.send("⏭️ Skipped current song")
            else:
                await ctx.send("Nothing is playing!")

    @commands.command()
    async def queue(self, ctx):
        """Show the current queue"""
        guild_id = str(ctx.guild.id)
        if guild_id not in self.queue or not self.queue[guild_id]:
            return await ctx.send("Queue is empty!")
            
        embed = discord.Embed(title="🎵 Queue", color=discord.Color.blue())
        
        # Add current song
        if guild_id in self.current_songs:
            embed.add_field(name="Now Playing", value=self.current_songs[guild_id], inline=False)
        
        # Titles are stored when songs are queued, so listing needs no network.
        entries = self.queue[guild_id]
        for i, entry in enumerate(entries[:QUEUE_DISPLAY_LIMIT], 1):
            embed.add_field(name=f"{i}. ", value=entry['title'], inline=False)
        # Embeds hold at most 25 fields, and a playlist can queue far more.
        if len(entries) > QUEUE_DISPLAY_LIMIT:
            embed.set_footer(text=f"…and {len(entries) - QUEUE_DISPLAY_LIMIT} more")

        await ctx.send(embed=embed)

    @commands.command(name="loop")
    async def loop_track(self, ctx):
        """Toggle looping for the current song"""
        guild_id = str(ctx.guild.id)
        vc = ctx.guild.voice_client or self.voice_clients.get(guild_id)

        if not self.is_player_busy(vc) or guild_id not in self.current_urls:
            await ctx.send("Nothing is playing!")
            return

        if not self.can_control_player(ctx, vc):
            return await ctx.send(NOT_IN_PLAYER_CHANNEL_MESSAGE)

        self.loop_enabled[guild_id] = not self.loop_enabled.get(guild_id, False)
        status = "enabled" if self.loop_enabled[guild_id] else "disabled"
        await ctx.send(f"🔁 Loop {status}")

    @commands.command()
    async def leave(self, ctx):
        """Leave the voice channel"""
        guild_id = str(ctx.guild.id)
        vc = ctx.guild.voice_client or self.voice_clients.get(guild_id)
        if vc:
            if not self.can_control_player(ctx, vc):
                return await ctx.send(NOT_IN_PLAYER_CHANNEL_MESSAGE)
            await self.leave_voice(ctx.guild)
            await ctx.send("👋 Left the voice channel")

    @commands.command()
    async def pause(self, ctx):
        """Pause/Resume the current song"""
        guild_id = str(ctx.guild.id)
        vc = ctx.guild.voice_client or self.voice_clients.get(guild_id)
        if vc:
            if not self.can_control_player(ctx, vc):
                return await ctx.send(NOT_IN_PLAYER_CHANNEL_MESSAGE)
            self.voice_clients[guild_id] = vc
            if vc.is_playing():
                vc.pause()
                await ctx.send("⏸️ Paused")
            elif vc.is_paused():
                vc.resume()
                await ctx.send("▶️ Resumed")
            else:
                await ctx.send("Nothing is playing!")
        else:
            await ctx.send("I'm not in a voice channel!")

async def setup(bot):
    await bot.add_cog(Music(bot)) 
