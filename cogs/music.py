import discord
from discord.ext import commands
import yt_dlp
import asyncio
import html
import re
import traceback
import requests
from bs4 import BeautifulSoup
import json
import urllib.parse
import random
import os
import io
import aiohttp
import time
from pathlib import Path
import subprocess
import platform
import sys
from dotenv import load_dotenv
import logging


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

# Create private directory for sensitive files
private_dir = Path('.private')
private_dir.mkdir(exist_ok=True)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(private_dir / 'bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('music_cog')

# Load environment variables
load_dotenv()

MIN_DISCORD_DAVE_VERSION = (2, 7, 0)

YOUTUBE_HOSTS = {
    'youtube.com',
    'www.youtube.com',
    'm.youtube.com',
    'music.youtube.com',
    'youtube-nocookie.com',
    'www.youtube-nocookie.com',
}
YOUTUBE_VIDEO_ID_PATTERN = re.compile(r'[A-Za-z0-9_-]{11}')
URL_SCHEME_PATTERN = re.compile(r'^[a-z][a-z0-9+.-]*://', re.IGNORECASE)
UNSUPPORTED_URL_MESSAGE = "❌ Only YouTube video and Spotify track links are supported."
SPOTIFY_HOSTS = {'spotify.com', 'spotify.link'}
SPOTIFY_MAX_REDIRECTS = 5
SPOTIFY_TRACK_URL_PREFIX = "https://open.spotify.com/track/"
NOT_IN_PLAYER_CHANNEL_MESSAGE = "❌ You need to be in my voice channel to do that."
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)
# Stream URLs carry their own expiry; stop reusing them a bit before it.
STREAM_EXPIRY_MARGIN = 300
# Downloaded fallback tracks kept on disk; older ones are deleted.
MAX_DOWNLOADED_TRACKS = 10
# Finished downloads; yt-dlp's partial and intermediate files are skipped.
DOWNLOADED_AUDIO_SUFFIXES = {'.mp3', '.m4a', '.webm', '.opus', '.ogg'}

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_clients = {}
        self.current_songs = {}
        self.current_urls = {}
        self.loop_enabled = {}
        self.skip_requested = set()
        self.queue = {}
        self.search_results = {}
        self.playback_locks = {}
        self.stream_cache = {}  # Cache for successful stream URLs
        self.downloaded_titles = {}  # video_id -> title of a downloaded track
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
        
        # Initialize instance lists
        self.piped_instances = [
            "https://pipedapi.kavin.rocks",
            "https://pipedapi.tokhmi.xyz",
            "https://piped-api.lunar.icu",
            "https://pipedapi.syncpundit.io",
            "https://api-piped.mha.fi",
            "https://pipedapi.osphost.fi",
            "https://pipedapi.in.projectsegfau.lt"
        ]
        
        self.invidious_instances = [
            "https://invidious.protokolla.fi",
            "https://invidious.esmailelbob.xyz",
            "https://yt.artemislena.eu",
            "https://invidious.nerdvpn.de",
            "https://invidious.dhusch.de",
            "https://vid.puffyan.us",
            "https://invidious.private.coffee"
        ]

        self.voice_diagnostic_handler = VoiceGatewayDiagnosticHandler(self)
        logging.getLogger('discord.voice_state').addHandler(self.voice_diagnostic_handler)

    def cog_unload(self):
        logging.getLogger('discord.voice_state').removeHandler(self.voice_diagnostic_handler)
        self.generated_cookies_file.unlink(missing_ok=True)

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
        return vc

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{__name__} is online!")

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
        async with self.playback_lock(guild_id):
            if self.is_player_busy(vc):
                entry = {'url': url, 'title': title or url}
                self.queue.setdefault(guild_id, []).append(entry)
                return entry
            await self.play_song(ctx, url)
            return None

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
        guild_id = str(ctx.guild.id)
        async with self.playback_lock(guild_id):
            skipped = guild_id in self.skip_requested
            self.skip_requested.discard(guild_id)

            vc = self.voice_clients.get(guild_id)
            if vc is None or not vc.is_connected():
                return

            # A command may have started another track while we waited.
            if self.is_player_busy(vc):
                return

            if not skipped and self.loop_enabled.get(guild_id) and self.current_urls.get(guild_id):
                if await self.play_song(ctx, self.current_urls[guild_id]):
                    return

            # A track that can't be played must not stall the rest of the queue.
            while self.queue.get(guild_id):
                entry = self.queue[guild_id].pop(0)
                if await self.play_song(ctx, entry['url']):
                    return

    @staticmethod
    def youtube_video_id(value):
        """Return the video ID of a YouTube video URL, or None for anything else."""
        parsed = urllib.parse.urlparse(value.strip())
        if parsed.scheme not in ('http', 'https'):
            return None

        hostname = (parsed.hostname or '').lower()
        parts = [part for part in parsed.path.split('/') if part]
        if hostname == 'youtu.be':
            video_id = parts[0] if parts else None
        elif hostname in YOUTUBE_HOSTS:
            if parts == ['watch']:
                video_id = urllib.parse.parse_qs(parsed.query).get('v', [None])[0]
            elif len(parts) >= 2 and parts[0] in ('embed', 'shorts', 'live', 'v'):
                video_id = parts[1]
            else:
                video_id = None
        else:
            return None

        if not video_id or not YOUTUBE_VIDEO_ID_PATTERN.fullmatch(video_id):
            return None
        return video_id

    @classmethod
    def normalize_youtube_url(cls, value):
        """Return the canonical watch URL for a YouTube video link, or None."""
        video_id = cls.youtube_video_id(value)
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else None

    @staticmethod
    def stream_url_expiry(stream_url):
        """Return the Unix time a stream URL stops working, when it says so.

        YouTube's media URLs carry an `expire` parameter; requesting them after
        that time fails with 403 Forbidden.
        """
        query = urllib.parse.parse_qs(urllib.parse.urlparse(stream_url).query)
        try:
            return int(query['expire'][0])
        except (KeyError, IndexError, ValueError):
            return None

    def is_cache_entry_fresh(self, cache_data, now):
        if now - cache_data['timestamp'] >= self.cache_timeout:
            return False
        expiry = self.stream_url_expiry(cache_data['url'])
        return expiry is None or now < expiry - STREAM_EXPIRY_MARGIN

    async def get_cached_stream(self, video_id):
        """Return the cached {'url', 'title'} for a video, unless it expired."""
        cache_data = self.stream_cache.get(video_id)
        if cache_data and self.is_cache_entry_fresh(cache_data, time.time()):
            return {'url': cache_data['url'], 'title': cache_data.get('title')}
        return None

    async def cache_stream(self, video_id, stream_url, title=None):
        """Cache stream URL and title with timestamp"""
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
            self.downloaded_titles.pop(path.stem, None)

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
            self.downloaded_titles[video_id] = (info or {}).get('title')
            self.prune_downloaded_audio()

        return self.normalize_stream_data({
            'url': str(downloaded_path),
            'title': self.downloaded_titles.get(video_id) or "Downloaded Song",
        }, is_local=True)

    async def get_direct_stream(self, video_id):
        """Get stream URL directly from YouTube frontend"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Connection': 'keep-alive',
                'DNT': '1',
                'Referer': 'https://www.youtube.com/'
            }
            
            # Try to get stream URL from YouTube directly
            url = f"https://www.youtube.com/watch?v={video_id}"
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        html = await response.text()
                        
                        # Extract player response
                        player_response = re.search(r'ytInitialPlayerResponse\s*=\s*({.+?});', html)
                        if player_response:
                            data = json.loads(player_response.group(1))
                            
                            # Try to find audio stream URL
                            formats = data.get('streamingData', {}).get('adaptiveFormats', [])
                            for fmt in formats:
                                stream_url = fmt.get('url')
                                if fmt.get('mimeType', '').startswith('audio/') and self.is_valid_stream_url(stream_url):
                                    return self.normalize_stream_data({
                                        'url': fmt.get('url', ''),
                                        'title': data.get('videoDetails', {}).get('title', 'Unknown Title')
                                    })
            
            return None
        except Exception as e:
            print(f"Direct stream error: {str(e)}")
            return None

    async def get_piped_stream(self, video_id):
        """Get stream URL from Piped API"""
        # Try each Piped instance
        random.shuffle(self.piped_instances)  # Randomize to distribute load
        
        for instance in self.piped_instances:
            try:
                api_url = f"{instance}/streams/{video_id}"
                response = await asyncio.to_thread(requests.get, api_url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    title = data.get('title', 'Unknown Title')
                    
                    # First try to get audio streams
                    if 'audioStreams' in data and data['audioStreams']:
                        for stream in data['audioStreams']:
                            stream_data = self.normalize_stream_data({
                                'url': stream.get('url'),
                                'title': title
                            })
                            if stream_data:
                                return stream_data
                    
                    # If no audio streams, try video streams
                    if 'videoStreams' in data and data['videoStreams']:
                        stream_data = self.normalize_stream_data({
                            'url': data['videoStreams'][0].get('url'),
                            'title': title
                        })
                        if stream_data:
                            return stream_data
            except Exception as e:
                print(f"Error with Piped instance {instance}: {str(e)}")
                continue
                
        return None

    async def get_invidious_stream(self, video_id):
        """Get stream URL from Invidious API"""
        random.shuffle(self.invidious_instances)  # Randomize to distribute load
        
        for instance in self.invidious_instances:
            try:
                api_url = f"{instance}/api/v1/videos/{video_id}"
                response = await asyncio.to_thread(requests.get, api_url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    title = data.get('title', 'Unknown Title')
                    
                    # Get audio formats
                    formats = data.get('adaptiveFormats', [])
                    for fmt in formats:
                        if fmt.get('type', '').startswith('audio/'):
                            stream_data = self.normalize_stream_data({
                                'url': fmt.get('url'),
                                'title': title
                            })
                            if stream_data:
                                return stream_data
                    
                    # Fallback to any format with a URL
                    if formats and 'url' in formats[0]:
                        stream_data = self.normalize_stream_data({
                            'url': formats[0]['url'],
                            'title': title
                        })
                        if stream_data:
                            return stream_data
            except Exception as e:
                print(f"Error with Invidious instance {instance}: {str(e)}")
                continue
                
        return None

    async def get_ytmusic_stream(self, video_id):
        """Get stream URL from YouTube Music API (last resort)"""
        try:
            api_url = f"https://music.youtube.com/watch?v={video_id}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://music.youtube.com/'
            }
            
            # Try to get direct stream URL from public sources
            stream_sources = [
                f"https://pipedapi.kavin.rocks/streams/{video_id}",
                f"https://pipedapi-libre.kavin.rocks/streams/{video_id}",
                f"https://vid.puffyan.us/latest_version?id={video_id}&itag=140",
                f"https://yt.artemislena.eu/latest_version?id={video_id}&itag=140"
            ]
            
            for src in stream_sources:
                try:
                    response = await asyncio.to_thread(requests.get, src, headers=headers, timeout=10)
                    if response.status_code == 200:
                        content_type = response.headers.get('Content-Type', '')
                        if 'application/json' in content_type:
                            data = response.json()
                        else:
                            data = response.text.strip()

                        if isinstance(data, dict):
                            # Handle Piped API format
                            if 'audioStreams' in data and data['audioStreams']:
                                stream_data = self.normalize_stream_data({
                                    'url': data['audioStreams'][0].get('url'),
                                    'title': data.get('title', 'YouTube Music Track')
                                })
                                if stream_data:
                                    return stream_data
                        elif isinstance(data, str) and data.startswith('http'):
                            # Handle direct URL response
                            stream_data = self.normalize_stream_data({
                                'url': data,
                                'title': 'YouTube Music Track'
                            })
                            if stream_data:
                                return stream_data
                except Exception as e:
                    print(f"Error with YT Music source {src}: {str(e)}")
                    continue
                
            return None
        except Exception as e:
            print(f"Error with YT Music API: {str(e)}")
            return None

    async def direct_search(self, query):
        """Search directly from YouTube's frontend and get results"""
        try:
            search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9'
            }
            
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session, \
                    session.get(search_url, headers=headers) as response:
                if response.status == 200:
                    html = await response.text()
                    
                    # Extract video IDs from the page
                    video_ids = re.findall(r'watch\?v=([a-zA-Z0-9_-]{11})', html)
                    unique_ids = []
                    for vid_id in video_ids:
                        if vid_id not in unique_ids:
                            unique_ids.append(vid_id)
                    
                    # Limit to 10 results
                    limited_ids = unique_ids[:10]
                    
                    # Format results
                    formatted_results = []
                    for vid_id in limited_ids:
                        # Extract title from page if possible
                        title_match = re.search(r'title="([^"]+)".*?watch\?v=' + vid_id, html)
                        title = title_match.group(1) if title_match else f"YouTube Video ({vid_id})"
                        
                        formatted_results.append({
                            'id': vid_id,
                            'title': title,
                            'duration_string': 'Unknown',
                            'webpage_url': f"https://www.youtube.com/watch?v={vid_id}"
                        })
                    
                    return formatted_results
            
            return []
        except Exception as e:
            print(f"Direct search error: {str(e)}")
            return []

    async def update_cookies(self):
        """Update cookies from environment variable"""
        try:
            print("Attempting to update YouTube cookies...")
            new_cookies = os.getenv('YOUTUBE_COOKIES', '')
            
            if new_cookies:
                # Never overwrite the operator's cookies file; write our own,
                # readable only by the bot.
                self.generated_cookies_file.touch(mode=0o600, exist_ok=True)
                self.generated_cookies_file.write_text(new_cookies)

                # Update the ydl_opts with new cookie file
                self.ydl_opts['cookiefile'] = str(self.generated_cookies_file)
                self.ydl_opts['cookiesfrombrowser'] = None
                
                print("Successfully updated cookies from environment variable")
                return True
            else:
                print("No cookies found in environment variable")
                return False
                
        except Exception as e:
            print(f"Error updating cookies: {e}")
            return False

    async def play_song(self, ctx, url):
        """Play a song using multiple fallback methods.

        Returns True once playback has started.
        """
        try:
            video_id = self.youtube_video_id(url)
            if not video_id:
                logger.warning(f"Refusing unsupported URL: {url}")
                await ctx.send(UNSUPPORTED_URL_MESSAGE)
                return

            normalized_url = f"https://www.youtube.com/watch?v={video_id}"
            logger.info(f"Processing YouTube URL: {url}, Video ID: {video_id}")

            # Check cache first
            cached = await self.get_cached_stream(video_id)
            if cached and self.is_valid_stream_url(cached['url']):
                stream_data = {'url': cached['url'], 'title': cached['title'] or 'Unknown Title'}
            else:
                if cached:
                    logger.warning(f"Dropping invalid cached stream URL for video {video_id}")
                    self.stream_cache.pop(video_id, None)

                await ctx.send("🔍 Getting stream...")

                # Try each method in sequence
                stream_data = None

                try:
                    info = await self.extract_info(self.ydl_opts, normalized_url)
                    if info and info.get('url'):
                        stream_data = self.normalize_stream_data({
                            'url': info['url'],
                            'title': info.get('title', 'Unknown Title')
                        })
                except Exception as e:
                    logger.error(f"yt-dlp error: {str(e)}")
                    if "Sign in to confirm you're not a bot" in str(e):
                        await ctx.send("⚠️ Refreshing connection...")
                        if await self.update_cookies():
                            try:
                                info = await self.extract_info(self.ydl_opts, normalized_url)
                                if info and info.get('url'):
                                    stream_data = self.normalize_stream_data({
                                        'url': info['url'],
                                        'title': info.get('title', 'Unknown Title')
                                    })
                            except Exception as retry_error:
                                logger.error(f"Retry failed: {str(retry_error)}")

                # Try alternative methods if main method fails
                if not stream_data:
                    for method in range(1, 6):  # Try up to 5 alternative methods
                        try:
                            if method == 1:
                                stream_data = await self.get_piped_stream(video_id)
                            elif method == 2:
                                stream_data = await self.get_invidious_stream(video_id)
                            elif method == 3:
                                stream_data = await self.get_direct_stream(video_id)
                            elif method == 4:
                                stream_data = await self.get_ytmusic_stream(video_id)
                            elif method == 5:
                                # Try downloading as last resort
                                stream_data = await self.get_downloaded_stream(video_id, normalized_url)

                            if stream_data:
                                break

                            await ctx.send("⏳ Trying alternative source...")

                        except Exception as e:
                            logger.error(f"Alternative method {method} failed: {str(e)}")
                            continue

                stream_data = self.normalize_stream_data(stream_data)

                if not stream_data:
                    await ctx.send("❌ Unable to play this track. Please try another.")
                    return

                # Cache successful stream URL if it's not a local file
                if not stream_data.get('is_local', False):
                    await self.cache_stream(video_id, stream_data['url'], stream_data['title'])
            
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
            
            self.current_songs[str(ctx.guild.id)] = stream_data['title']
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
            await ctx.send(embed=embed)
            return True

        except Exception as e:
            logger.error(f"Play song error: {str(e)}\n{traceback.format_exc()}")
            await ctx.send("❌ An error occurred while trying to play the track.")
        return False

    async def search_youtube(self, query):
        """Search YouTube with multiple fallback methods"""
        # First try with yt-dlp
        try:
            # Use a custom format with yt-dlp to perform the search
            search_opts = {
                'format': 'bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'default_search': 'ytsearch10',  # Search for 10 videos
                'cookiefile': self.ydl_opts.get('cookiefile'),
                'cookiesfrombrowser': self.ydl_opts.get('cookiesfrombrowser'),
                'http_headers': self.ydl_opts.get('http_headers')
            }
            
            info = await self.extract_info(search_opts, f"ytsearch10:{query}")
            if 'entries' in info and info['entries']:
                # Format results
                formatted_results = []
                for entry in info['entries']:
                    formatted_results.append({
                        'id': entry.get('id'),
                        'title': entry.get('title', 'Unknown Title'),
                        'duration_string': entry.get('duration_string', 'Unknown'),
                        'webpage_url': entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
                    })
                return formatted_results
                    
        except Exception as e:
            print(f"yt-dlp search error: {str(e)}")
            # If error is about bot detection, try to update cookies
            if "Sign in to confirm you're not a bot" in str(e):
                print("Trying to update cookies due to bot detection...")
                await self.update_cookies()
        
        # Try Invidious API next
        try:
            # Randomly choose an Invidious instance
            instance = random.choice(self.invidious_instances)
            
            # Search using Invidious API
            encoded_query = urllib.parse.quote(query)
            api_url = f"{instance}/api/v1/search?q={encoded_query}&type=video"
            
            response = await asyncio.to_thread(requests.get, api_url, timeout=10)
            if response.status_code == 200:
                results = response.json()
                
                # Format results similar to previous format
                formatted_results = []
                for video in results[:10]:  # Limit to 10 results
                    formatted_results.append({
                        'id': video.get('videoId'),
                        'title': video.get('title', 'Unknown Title'),
                        'duration_string': self._format_duration(video.get('lengthSeconds', 0)),
                        'channel': video.get('author'),
                        'webpage_url': f"https://www.youtube.com/watch?v={video.get('videoId')}"
                    })
                return formatted_results
                
        except Exception as e:
            print(f"Invidious API search error: {str(e)}")
        
        # Try Piped API if Invidious fails
        try:
            # Randomly choose a Piped instance
            instance = random.choice(self.piped_instances)
            
            # Search using Piped API
            encoded_query = urllib.parse.quote(query)
            api_url = f"{instance}/search?q={encoded_query}&filter=videos"
            
            response = await asyncio.to_thread(requests.get, api_url, timeout=10)
            if response.status_code == 200:
                results = response.json()
                
                # Format results
                formatted_results = []
                for item in results[:10]:
                    formatted_results.append({
                        'id': item.get('id'),
                        'title': item.get('title', 'Unknown Title'),
                        'duration_string': item.get('duration', 'Unknown'),
                        'webpage_url': f"https://www.youtube.com/watch?v={item.get('id')}"
                    })
                return formatted_results
        except Exception as e:
            print(f"Piped API search error: {str(e)}")
        
        # If all else fails, try direct search
        try:
            results = await self.direct_search(query)
            if results:
                return results
        except Exception as e:
            print(f"Direct search error: {str(e)}")
        
        # If all searches fail, return empty results
        return []

    def _format_duration(self, seconds):
        """Format duration from seconds to MM:SS"""
        minutes, seconds = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"

    @staticmethod
    def is_spotify_reference(value):
        """Return whether a value is a Spotify URL or URI."""
        value = value.strip()
        if value.lower().startswith('spotify:'):
            return True

        parsed = urllib.parse.urlparse(value)
        hostname = (parsed.hostname or '').lower()
        return (
            hostname == 'spotify.com'
            or hostname.endswith('.spotify.com')
            or hostname == 'spotify.link'
            or hostname.endswith('.spotify.link')
        )

    @staticmethod
    def is_fetchable_spotify_url(url):
        """Return whether the bot may request this URL while resolving a track."""
        parsed = urllib.parse.urlparse(url)
        hostname = (parsed.hostname or '').lower()
        try:
            port = parsed.port
        except ValueError:
            return False
        is_spotify_host = (
            hostname in SPOTIFY_HOSTS
            or hostname.endswith(('.spotify.com', '.spotify.link'))
        )
        return (
            parsed.scheme == 'https'
            and port in (None, 443)
            and not parsed.username
            and is_spotify_host
        )

    async def fetch_spotify_page(self, session, url):
        """GET a Spotify page, following redirects only within Spotify over HTTPS.

        Returns (final_url, html), or None when the page can't be fetched.
        """
        for _ in range(SPOTIFY_MAX_REDIRECTS + 1):
            if not self.is_fetchable_spotify_url(url):
                logger.warning("Refusing to follow Spotify redirect to %s", url)
                return None

            async with session.get(url, allow_redirects=False) as response:
                location = response.headers.get('Location')
                if response.status in (301, 302, 303, 307, 308) and location:
                    url = urllib.parse.urljoin(url, location)
                    continue
                if response.status != 200:
                    return None
                return url, await response.text()

        logger.warning("Too many redirects while resolving Spotify link")
        return None

    @staticmethod
    def normalize_spotify_track_url(value):
        """Normalize supported Spotify track references to a web URL."""
        value = value.strip()
        uri_match = re.fullmatch(r'spotify:track:([A-Za-z0-9]{22})', value, re.IGNORECASE)
        if uri_match:
            return f"https://open.spotify.com/track/{uri_match.group(1)}"

        parsed = urllib.parse.urlparse(value)
        hostname = (parsed.hostname or '').lower()
        if hostname == 'spotify.link' or hostname.endswith('.spotify.link'):
            # Short links are resolved by following their redirects, so only
            # accept ones that can be fetched safely.
            return value if Music.is_fetchable_spotify_url(value) else None

        if not (
            hostname == 'spotify.com'
            or hostname.endswith('.spotify.com')
        ):
            return None

        parts = [part for part in parsed.path.split('/') if part]
        try:
            track_index = parts.index('track')
            track_id = parts[track_index + 1]
        except (ValueError, IndexError):
            return None

        if not re.fullmatch(r'[A-Za-z0-9]{22}', track_id):
            return None

        return f"https://open.spotify.com/track/{track_id}"

    @staticmethod
    def spotify_query_from_embed(page_html, track_id):
        """Build a "track artists" query from the track's embed page.

        The embed page carries the track's metadata as Next.js data, including
        every artist, which oEmbed and the regular page title don't reliably do.
        """
        script = BeautifulSoup(page_html, 'html.parser').find('script', id='__NEXT_DATA__')
        if script is None or not script.string:
            return None

        try:
            entity = json.loads(script.string)['props']['pageProps']['state']['data']['entity']
        except (ValueError, KeyError, TypeError):
            return None

        if not isinstance(entity, dict):
            return None
        if entity.get('type') != 'track' or entity.get('uri') != f"spotify:track:{track_id}":
            return None

        name = (entity.get('name') or '').strip()
        artists = [
            artist.get('name', '').strip()
            for artist in entity.get('artists') or []
            if isinstance(artist, dict)
        ]
        artists = [artist for artist in artists if artist]
        if not name or not artists:
            return None
        return " ".join([name, *artists])

    async def handle_spotify_url(self, url):
        """Resolve a Spotify track to a title/artist query for YouTube."""
        track_url = self.normalize_spotify_track_url(url)
        if not track_url:
            return None

        timeout = aiohttp.ClientTimeout(total=8, connect=5, sock_read=5)
        headers = {
            'User-Agent': self.ydl_opts['http_headers']['User-Agent'],
            'Accept-Language': 'en-US,en;q=0.9',
        }

        try:
            # Keep every metadata attempt under one deadline so a Spotify
            # outage cannot hold the command open indefinitely.
            async with asyncio.timeout(12):
                async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                    if not track_url.startswith(SPOTIFY_TRACK_URL_PREFIX):
                        # A short link: follow it to find out which track it is.
                        page = await self.fetch_spotify_page(session, track_url)
                        resolved_url = page and self.normalize_spotify_track_url(page[0])
                        if resolved_url:
                            track_url = resolved_url

                    if track_url.startswith(SPOTIFY_TRACK_URL_PREFIX):
                        track_id = track_url.removeprefix(SPOTIFY_TRACK_URL_PREFIX)
                        embed_url = f"https://open.spotify.com/embed/track/{track_id}"
                        page = await self.fetch_spotify_page(session, embed_url)
                        search_query = page and self.spotify_query_from_embed(page[1], track_id)
                        if search_query:
                            return search_query

                    # oEmbed is an official, credential-free metadata endpoint.
                    # It only has the track name, so it is the last resort.
                    oembed_url = 'https://open.spotify.com/oembed'
                    async with session.get(
                        oembed_url, params={'url': track_url}, allow_redirects=False
                    ) as response:
                        if response.status != 200:
                            return None

                        metadata = await response.json(content_type=None)
                        iframe_url = metadata.get('iframe_url', '')
                        if '/track/' not in iframe_url:
                            return None

                        title = html.unescape(metadata.get('title', '')).strip()
                        return title or None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            logger.warning("Could not resolve Spotify track %s: %s", url, e)
            return None

    @commands.command()
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

            if self.is_spotify_reference(query):
                await ctx.send("🎵 Resolving Spotify track...")
                spotify_info = await self.handle_spotify_url(query)
                if not spotify_info:
                    return await ctx.send(
                        "❌ I couldn't read this Spotify track. "
                        "For now, only individual track links are supported."
                    )

                results = await self.search_youtube(spotify_info)
                if not results:
                    return await ctx.send("❌ I couldn't find this Spotify track on YouTube.")

                selected = results[0]
                if await self.play_or_enqueue(ctx, vc, selected['webpage_url'], selected['title']):
                    await ctx.send(f"🎵 Added to queue: **{selected['title']}**")
                return

            if URL_SCHEME_PATTERN.match(query):
                url = self.normalize_youtube_url(query)
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

                self.search_results[ctx.author.id] = results

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
                        del self.search_results[ctx.author.id]
                        return await ctx.send("❌ Search cancelled.")

                    selected = results[int(msg.content) - 1]
                    if await self.play_or_enqueue(ctx, vc, selected['webpage_url'], selected['title']):
                        await ctx.send(f"🎵 Added to queue: **{selected['title']}**")

                except asyncio.TimeoutError:
                    del self.search_results[ctx.author.id]
                    await ctx.send("❌ Search timed out.")
                except Exception as e:
                    logger.error(f"Search selection error: {str(e)}")
                    await ctx.send("❌ An error occurred while processing your selection.")

        except Exception as e:
            logger.error(f"Play command error: {str(e)}\n{traceback.format_exc()}")
            await ctx.send("❌ An error occurred while processing your request.")

    @commands.command()
    async def testplay(self, ctx):
        """Um comando simples para testar a conexão de voz com um arquivo local."""
        # 1. Verifica se o autor está em um canal de voz
        if not ctx.author.voice:
            return await ctx.send("Você precisa estar em um canal de voz para este teste.")

        guild_id = str(ctx.guild.id)

        # 2. Tenta se conectar ao canal
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
        except Exception as e:
            await self.disconnect_voice_client(guild_id, ctx.guild)
            await ctx.send(f"❌ Falha ao conectar: {e}")
            return

        # 3. Tenta carregar o áudio local
        try:
            # Garanta que o arquivo 'audio.mp3' está na pasta raiz do bot
            source = discord.FFmpegOpusAudio('audio.mp3')
        except Exception as e:
            await ctx.send(f"❌ Falha ao carregar o arquivo 'audio.mp3': {e}")
            await vc.disconnect()
            return

        # 4. Toca o áudio e desconecta
        try:
            await ctx.send("▶️ Tocando áudio de teste...")
            vc.play(source)

            while vc.is_playing():
                await asyncio.sleep(1)

            await vc.disconnect()
            await ctx.send("✅ Teste finalizado.")

        except Exception as e:
            await ctx.send(f"❌ Erro durante a reprodução: {e}")
            if vc.is_connected():
                await vc.disconnect()

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
        for i, entry in enumerate(self.queue[guild_id], 1):
            embed.add_field(name=f"{i}. ", value=entry['title'], inline=False)

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
            # Clear first: disconnecting ends the track and fires play_next.
            self.queue[guild_id] = []
            self.loop_enabled[guild_id] = False
            self.current_urls.pop(guild_id, None)
            await self.disconnect_voice_client(guild_id, ctx.guild)
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
