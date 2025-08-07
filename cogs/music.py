import discord
from discord.ext import commands
import yt_dlp
import asyncio
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

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_clients = {}
        self.current_songs = {}
        self.queue = {}
        self.search_results = {}
        self.stream_cache = {}  # Cache for successful stream URLs
        self.cache_timeout = 3600  # Cache timeout in seconds (1 hour)
        
        # Create private directories
        self.private_dir = Path('.private')
        self.private_dir.mkdir(exist_ok=True)
        
        # Create cache directory inside private directory
        self.cache_dir = self.private_dir / 'temp_audio'
        self.cache_dir.mkdir(exist_ok=True)
        
        self.cookies_file = Path('youtube_cookies.txt')
        
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
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'geo_bypass': True,
            'geo_bypass_country': 'US',
            'socket_timeout': 10,
            'retries': 5,
            'extractor_retries': 5,
            'fragment_retries': 5,
            'extract_flat': 'in_playlist',
            'concurrent_fragment_downloads': 5,
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
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -analyzeduration 0 -loglevel warning',
            'options': '-vn -bufsize 64k',
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

    def __del__(self):
        # Clean up temporary cookie file if it exists
        if self.cookies_file and self.cookies_file.exists():
            self.cookies_file.unlink()

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{__name__} is online!")

    async def play_next(self, ctx):
        if str(ctx.guild.id) in self.queue and self.queue[str(ctx.guild.id)]:
            # Get next song from queue
            url = self.queue[str(ctx.guild.id)].pop(0)
            await self.play_song(ctx, url)

    async def extract_youtube_id(self, url):
        """Extract video ID from a YouTube URL"""
        # Direct match for watch?v= parameter (most common)
        watch_v_match = re.search(r'(?:youtube\.com\/watch\?v=|youtube\.com\/watch\?.+&v=)([a-zA-Z0-9_-]{11})', url)
        if watch_v_match:
            return watch_v_match.group(1)
            
        # Extended regex to handle more YouTube URL formats
        youtube_regex = r'(?:youtube\.com\/(?:[^\/\n\s]+\/\S+\/|(?:v|e(?:mbed)?)\/|\S*?[?&]v=)|(?:youtu\.be|music\.youtube\.com)\/([a-zA-Z0-9_-]{11}))'
        match = re.search(youtube_regex, url)
        
        if match:
            return match.group(1)
        
        # Additional checks for other URL formats
        if 'youtube.com' in url or 'youtu.be' in url or 'music.youtube.com' in url:
            # Check for v= parameter
            v_param = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
            if v_param:
                return v_param.group(1)
                
            # Check for youtu.be/ format
            short_url = re.search(r'youtu\.be\/([a-zA-Z0-9_-]{11})', url)
            if short_url:
                return short_url.group(1)
                
            # Check for music.youtube.com
            music_url = re.search(r'music\.youtube\.com\/watch\?v=([a-zA-Z0-9_-]{11})', url)
            if music_url:
                return music_url.group(1)
                
            # Check for embed format
            embed_url = re.search(r'youtube\.com\/embed\/([a-zA-Z0-9_-]{11})', url)
            if embed_url:
                return embed_url.group(1)
        
        # Fallback: try to find any 11-character ID in the URL that looks like a YouTube ID
        potential_ids = re.findall(r'([a-zA-Z0-9_-]{11})', url)
        for potential_id in potential_ids:
            if re.match(r'^[a-zA-Z0-9_-]{11}$', potential_id):
                return potential_id
                
        return None

    async def is_youtube_url(self, url):
        """Check if a URL is a YouTube URL"""
        # First check with basic domain matching
        youtube_domains = [
            'youtube.com', 
            'youtu.be', 
            'www.youtube.com', 
            'm.youtube.com',
            'music.youtube.com',
            'youtube-nocookie.com'
        ]
        
        for domain in youtube_domains:
            if domain in url:
                return True
        
        # Second, try pattern matching for YouTube URLs
        youtube_patterns = [
            r'(?:https?://)?(?:www\.)?youtube\.com/watch\?(?:.*&)?v=[\w-]+(?:&.*)?',
            r'(?:https?://)?(?:www\.)?youtu\.be/[\w-]+(?:\?.*)?',
            r'(?:https?://)?(?:www\.)?youtube\.com/embed/[\w-]+(?:\?.*)?',
            r'(?:https?://)?(?:www\.)?youtube\.com/v/[\w-]+(?:\?.*)?',
            r'(?:https?://)?(?:www\.)?youtube\.com/user/[\w-]+(?:/.*)?',
            r'(?:https?://)?(?:www\.)?youtube\.com/channel/[\w-]+(?:/.*)?',
            r'(?:https?://)?(?:www\.)?music\.youtube\.com/watch\?(?:.*&)?v=[\w-]+(?:&.*)?'
        ]
        
        for pattern in youtube_patterns:
            if re.match(pattern, url, re.IGNORECASE):
                return True
                
        return False

    async def get_cached_stream(self, video_id):
        """Get stream URL from cache if available and not expired"""
        if video_id in self.stream_cache:
            cache_data = self.stream_cache[video_id]
            if time.time() - cache_data['timestamp'] < self.cache_timeout:
                return cache_data['url']
        return None

    async def cache_stream(self, video_id, stream_url):
        """Cache stream URL with timestamp"""
        self.stream_cache[video_id] = {
            'url': stream_url,
            'timestamp': time.time()
        }

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
            async with aiohttp.ClientSession() as session:
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
                                if fmt.get('mimeType', '').startswith('audio/'):
                                    return {
                                        'url': fmt.get('url', ''),
                                        'title': data.get('videoDetails', {}).get('title', 'Unknown Title')
                                    }
            
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
                response = requests.get(api_url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    title = data.get('title', 'Unknown Title')
                    
                    # First try to get audio streams
                    if 'audioStreams' in data and data['audioStreams']:
                        for stream in data['audioStreams']:
                            return {
                                'url': stream['url'],
                                'title': title
                            }
                    
                    # If no audio streams, try video streams
                    if 'videoStreams' in data and data['videoStreams']:
                        return {
                            'url': data['videoStreams'][0]['url'],
                            'title': title
                        }
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
                response = requests.get(api_url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    title = data.get('title', 'Unknown Title')
                    
                    # Get audio formats
                    formats = data.get('adaptiveFormats', [])
                    for fmt in formats:
                        if fmt.get('type', '').startswith('audio/'):
                            return {
                                'url': fmt['url'],
                                'title': title
                            }
                    
                    # Fallback to any format with a URL
                    if formats and 'url' in formats[0]:
                        return {
                            'url': formats[0]['url'],
                            'title': title
                        }
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
                    response = requests.get(src, headers=headers, timeout=10)
                    if response.status_code == 200:
                        data = response.json()
                        if isinstance(data, dict):
                            # Handle Piped API format
                            if 'audioStreams' in data and data['audioStreams']:
                                return {
                                    'url': data['audioStreams'][0]['url'],
                                    'title': data.get('title', 'YouTube Music Track')
                                }
                        elif isinstance(data, str) and data.startswith('http'):
                            # Handle direct URL response
                            return {
                                'url': data,
                                'title': 'YouTube Music Track'
                            }
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
            
            session = aiohttp.ClientSession()
            async with session.get(search_url, headers=headers) as response:
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
                    
                    await session.close()
                    return formatted_results
            
            await session.close()
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
                # Update the cookies file with new content
                if self.cookies_file:
                    self.cookies_file.write_text(new_cookies)
                else:
                    self.cookies_file = self.private_dir / 'temp_cookies.txt'
                    self.cookies_file.write_text(new_cookies)
                
                # Update the ydl_opts with new cookie file
                self.ydl_opts['cookiefile'] = str(self.cookies_file)
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
        """Play a song using multiple fallback methods"""
        try:
            # First determine if this is a YouTube URL
            is_youtube = await self.is_youtube_url(url)
            
            if is_youtube:
                # Extract the YouTube video ID
                video_id = await self.extract_youtube_id(url)
                if not video_id:
                    await ctx.send("❌ Invalid YouTube URL format.")
                    logger.error(f"Could not extract video ID from URL: {url}")
                    return
                
                logger.info(f"Processing YouTube URL: {url}, Video ID: {video_id}")
                
                # Check cache first
                cached_url = await self.get_cached_stream(video_id)
                if cached_url:
                    stream_data = {'url': cached_url, 'title': self.current_songs.get(str(ctx.guild.id), 'Cached Song')}
                else:
                    await ctx.send("🔍 Getting stream...")
                    
                    # Try each method in sequence
                    stream_data = None
                    normalized_url = f"https://www.youtube.com/watch?v={video_id}"
                    
                    try:
                        with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                            info = ydl.extract_info(normalized_url, download=False)
                            if 'url' in info:
                                stream_data = {
                                    'url': info['url'],
                                    'title': info.get('title', 'Unknown Title')
                                }
                    except Exception as e:
                        logger.error(f"yt-dlp error: {str(e)}")
                        if "Sign in to confirm you're not a bot" in str(e):
                            await ctx.send("⚠️ Refreshing connection...")
                            if await self.update_cookies():
                                try:
                                    with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                                        info = ydl.extract_info(normalized_url, download=False)
                                        if 'url' in info:
                                            stream_data = {
                                                'url': info['url'],
                                                'title': info.get('title', 'Unknown Title')
                                            }
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
                                    output_path = self.cache_dir / f"{video_id}.mp3"
                                    if not output_path.exists():
                                        download_opts = self.ydl_opts.copy()
                                        download_opts['outtmpl'] = str(self.cache_dir / f"{video_id}.%(ext)s")
                                        with yt_dlp.YoutubeDL(download_opts) as ydl:
                                            info = ydl.extract_info(normalized_url, download=True)
                                            stream_data = {
                                                'url': str(output_path),
                                                'title': info.get('title', f"Downloaded Song"),
                                                'is_local': True
                                            }
                                
                                if stream_data:
                                    break
                                
                                await ctx.send("⏳ Trying alternative source...")
                                
                            except Exception as e:
                                logger.error(f"Alternative method {method} failed: {str(e)}")
                                continue
                    
                    if not stream_data:
                        await ctx.send("❌ Unable to play this track. Please try another.")
                        return
                    
                    # Cache successful stream URL if it's not a local file
                    if not stream_data.get('is_local', False):
                        await self.cache_stream(video_id, stream_data['url'])
                
            else:
                # For non-YouTube URLs
                try:
                    with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                        if 'url' not in info:
                            await ctx.send("❌ Unable to process this URL.")
                            return
                        
                        stream_data = {
                            'url': info['url'],
                            'title': info.get('title', 'Unknown Title'),
                            'is_local': False
                        }
                except Exception as e:
                    logger.error(f"Non-YouTube URL processing error: {str(e)}")
                    await ctx.send("❌ Unable to process this URL.")
                    return
            
            # Create audio source and play
            try:
                if stream_data.get('is_local', False):
                    source = discord.FFmpegOpusAudio(stream_data['url'], **self.FFMPEG_OPTIONS)
                else:
                    try:
                        source = await discord.FFmpegOpusAudio.from_probe(stream_data['url'], **self.FFMPEG_OPTIONS)
                    except Exception as e:
                        if "403 Forbidden" in str(e):
                            await ctx.send("⚠️ Stream expired, retrying...")
                            if is_youtube:
                                try:
                                    output_path = self.cache_dir / f"{video_id}.mp3"
                                    download_opts = self.ydl_opts.copy()
                                    download_opts['outtmpl'] = str(self.cache_dir / f"{video_id}.%(ext)s")
                                    with yt_dlp.YoutubeDL(download_opts) as ydl:
                                        info = ydl.extract_info(normalized_url, download=True)
                                        source = discord.FFmpegOpusAudio(str(output_path), **self.FFMPEG_OPTIONS)
                                except Exception as dl_err:
                                    logger.error(f"Download after 403 error failed: {str(dl_err)}")
                                    raise Exception("Unable to play this track")
                            else:
                                raise Exception("Unable to play this track")
                        else:
                            raise Exception("Unable to play this track")
            except Exception as e:
                logger.error(f"Error creating audio source: {str(e)}")
                await ctx.send("❌ Unable to play this track.")
                return
            
            self.current_songs[str(ctx.guild.id)] = stream_data['title']
            
            vc = self.voice_clients[str(ctx.guild.id)]
            if not vc.is_connected():
                await ctx.send("❌ Voice connection lost.")
                return
            
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
                self.play_next(ctx), self.bot.loop).result())
            
            embed = discord.Embed(
                title="🎵 Now Playing",
                description=f"**{stream_data['title']}**",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Play song error: {str(e)}\n{traceback.format_exc()}")
            await ctx.send("❌ An error occurred while trying to play the track.")

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
            
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                info = ydl.extract_info(f"ytsearch10:{query}", download=False)
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
            
            response = requests.get(api_url, timeout=10)
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
            
            response = requests.get(api_url, timeout=10)
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

    async def handle_spotify_url(self, url):
        """Extract track info from Spotify URL"""
        # Spotify URL patterns
        track_pattern = r'https?://[^/]*spotify\.com/track/([a-zA-Z0-9]+)'
        
        track_match = re.match(track_pattern, url)
        if track_match:
            try:
                # Try to get the song name from the page title
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = requests.get(url, headers=headers)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    title = soup.title.string
                    if title and 'Spotify' in title:
                        # Remove "- song and lyrics | Spotify" or similar
                        track_info = re.sub(r'\s*[-|]\s*(?:song and lyrics\s*)?(?:\|\s*)?Spotify\s*$', '', title).strip()
                        if track_info:
                            return track_info

            except Exception as e:
                print(f"Error extracting Spotify title: {e}")

            # Fallback: Try to get track name from the URL slug
            parts = url.split('?')[0].split('/')  # Remove query parameters
            if len(parts) > 4:
                # The last part of the URL might contain the song name
                track_slug = parts[-1]
                # If there's a query string, remove it
                if '?' in track_slug:
                    track_slug = track_slug.split('?')[0]
                # Convert slug to readable text
                track_info = track_slug.replace('-', ' ').replace('_', ' ')
                # If it's just the ID, try the previous part which might be the song name
                if re.match(r'^[A-Za-z0-9]{22}$', track_info):
                    if len(parts) > 5:
                        track_info = parts[-2].replace('-', ' ').replace('_', ' ')
                return track_info
            
            # If we can't get the name from the URL, at least add "spotify song" to help search
            return "spotify song"
        return None

    @commands.command()
    async def play(self, ctx, *, query):
        """Play a song by URL (YouTube/Spotify) or search term"""
        try:
            if not ctx.author.voice:
                return await ctx.send("❌ You need to be in a voice channel!")
            
            channel = ctx.author.voice.channel
            guild_id = str(ctx.guild.id)
            
            try:
                if guild_id not in self.voice_clients:
                    # Adicione um timeout para evitar que o bot fique preso
                    vc = await channel.connect(timeout=5.0)
                    self.voice_clients[guild_id] = vc
                else:
                    vc = self.voice_clients[guild_id]
                    if vc.channel != channel:
                        await vc.move_to(channel)
            # Capture a exceção específica de conexão
            except discord.errors.ConnectionClosed:
                await ctx.send("❌ A conexão de voz com o Discord falhou. Verifique as permissões e tente novamente.")
                return
            except asyncio.TimeoutError:
                await ctx.send("❌ A conexão de voz demorou muito para responder. Tente novamente.")
                return
            except Exception as e:
                logger.error(f"Voice connection error: {str(e)}")
                await ctx.send("❌ Não foi possível entrar no canal de voz.")
                return

            if guild_id not in self.queue:
                self.queue[guild_id] = []

            spotify_info = await self.handle_spotify_url(query)
            if spotify_info:
                await ctx.send(f"🎵 Found Spotify track, searching...")
                query = spotify_info

            url_pattern = re.compile(
                r'^(?:http|ftp)s?://'
                r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'
                r'localhost|'
                r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
                r'(?::\d+)?'
                r'(?:/?|[/?]\S+)$', re.IGNORECASE)

            if url_pattern.match(query):
                is_youtube = await self.is_youtube_url(query)
                
                if is_youtube and 'spotify.com' not in query:
                    video_id = await self.extract_youtube_id(query)
                    if not video_id:
                        logger.error(f"Could not extract video ID from URL: {query}")
                    
                    try:
                        if vc.is_playing():
                            self.queue[guild_id].append(query)
                            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                                info = ydl.extract_info(query, download=False)
                                await ctx.send(f"🎵 Added to queue: **{info['title']}**")
                        else:
                            await self.play_song(ctx, query)
                    except Exception as e:
                        logger.error(f"YouTube playback error: {str(e)}")
                        await ctx.send("❌ Unable to play this track.")
                elif 'spotify.com' in query:
                    await ctx.send("⚠️ Spotify URL detected, searching...")
                    await self.play_song(ctx, query)
                else:
                    try:
                        if vc.is_playing():
                            self.queue[guild_id].append(query)
                            await ctx.send("🎵 Added to queue")
                        else:
                            await self.play_song(ctx, query)
                    except Exception as e:
                        logger.error(f"Non-YouTube URL error: {str(e)}")
                        await ctx.send("❌ Unable to play this track.")
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
                    url = selected['webpage_url']

                    if vc.is_playing():
                        self.queue[guild_id].append(url)
                        await ctx.send(f"🎵 Added to queue: **{selected['title']}**")
                    else:
                        await self.play_song(ctx, url)

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

        voice_channel = ctx.author.voice.channel

        # 2. Tenta se conectar ao canal
        try:
            vc = await voice_channel.connect(timeout=8.0)
        except asyncio.TimeoutError:
            await ctx.send("❌ A conexão demorou demais! Verifique as permissões do bot.")
            return
        except Exception as e:
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
        if guild_id in self.voice_clients:
            vc = self.voice_clients[guild_id]
            if vc.is_playing():
                vc.stop()
            self.queue[guild_id] = []
            await ctx.send("⏹️ Stopped playing and cleared the queue")

    @commands.command()
    async def skip(self, ctx):
        """Skip the current song"""
        guild_id = str(ctx.guild.id)
        if guild_id in self.voice_clients:
            vc = self.voice_clients[guild_id]
            if vc.is_playing():
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
        
        # Add queued songs
        with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
            for i, url in enumerate(self.queue[guild_id], 1):
                try:
                    info = ydl.extract_info(url, download=False)
                    embed.add_field(name=f"{i}. ", value=info['title'], inline=False)
                except:
                    embed.add_field(name=f"{i}. ", value="Unable to fetch title", inline=False)
                
        await ctx.send(embed=embed)

    @commands.command()
    async def leave(self, ctx):
        """Leave the voice channel"""
        guild_id = str(ctx.guild.id)
        if guild_id in self.voice_clients:
            await self.voice_clients[guild_id].disconnect()
            del self.voice_clients[guild_id]
            self.queue[guild_id] = []
            await ctx.send("👋 Left the voice channel")

    @commands.command()
    async def pause(self, ctx):
        """Pause/Resume the current song"""
        guild_id = str(ctx.guild.id)
        if guild_id in self.voice_clients:
            vc = self.voice_clients[guild_id]
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