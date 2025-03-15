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

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_clients = {}
        self.current_songs = {}
        self.queue = {}
        self.search_results = {}
        self.stream_cache = {}  # Cache for successful stream URLs
        self.cache_timeout = 3600  # Cache timeout in seconds (1 hour)
        
        # Create cache directory
        self.cache_dir = Path('temp_audio')
        self.cache_dir.mkdir(exist_ok=True)
        
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
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml',
                'Referer': 'https://www.youtube.com/'
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
        youtube_regex = (
            r'(?:https?://)?(?:www\.|m\.)?'
            r'(?:youtube\.com/(?:watch\?v=|embed/|v/|shorts/|live/|playlist\?list=.*&v=|.*[?&]v=)|'
            r'youtu\.be/|music\.youtube\.com/watch\?v=)'
            r'([a-zA-Z0-9_-]{11})'
        )
        match = re.search(youtube_regex, url)
        if match:
            return match.group(1)
        return None

    async def is_youtube_url(self, url):
        """Check if a URL is a YouTube URL"""
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
            
    async def play_song(self, ctx, url):
        """Play a song using multiple fallback methods"""
        try:
            is_youtube = await self.is_youtube_url(url)
            video_id = None
            if is_youtube:
                video_id = await self.extract_youtube_id(url)
                if not video_id:
                    await ctx.send("❌ Could not extract YouTube video ID")
                    return

                cached_url = await self.get_cached_stream(video_id)
                if cached_url:
                    stream_data = {'url': cached_url, 'title': self.current_songs.get(str(ctx.guild.id)), 'is_local': False}
                else:
                    if is_youtube:
                        await ctx.send("🔍 Getting stream from YouTube...")
                        stream_data = None
                        try:
                            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                                # Extrair o URL do formato de áudio correto
                                best_audio = next(
                                    (fmt for fmt in info.get('formats', []) 
                                    if fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none'),
                                    None
                                )
                                if best_audio:
                                    stream_data = {
                                        'url': best_audio['url'],
                                        'title': info.get('title', 'Unknown Title'),
                                        'is_local': False
                                    }
                                else:
                                    raise Exception("No audio stream found in formats")
                        except Exception as ydl_error:
                            print(f"yt-dlp error: {str(ydl_error)}")
                    
                    # 2. If yt-dlp failed, try Piped API
                    if not stream_data:
                        await ctx.send("⚠️ Trying alternative source...")
                        stream_data = await self.get_piped_stream(video_id)
                    
                    # 3. If Piped failed, try Invidious
                    if not stream_data:
                        await ctx.send("⚠️ Trying another alternative source...")
                        stream_data = await self.get_invidious_stream(video_id)
                    
                    # 4. If both APIs failed, try direct method
                    if not stream_data:
                        await ctx.send("⚠️ Trying direct method...")
                        stream_data = await self.get_direct_stream(video_id)
                    
                    # 5. If all streaming methods failed, try downloading
                    if not stream_data:
                        await ctx.send("⚠️ Trying download method...")
                        output_path = self.cache_dir / f"{video_id}.mp3"
                        
                        if output_path.exists():
                            # Use existing downloaded file
                            stream_data = {
                                'url': str(output_path),
                                'title': f"Downloaded Song ({video_id})",
                                'is_local': True
                            }
                        else:
                            # Try to download using yt-dlp
                            try:
                                download_opts = self.ydl_opts.copy()
                                download_opts['outtmpl'] = str(self.cache_dir / f"{video_id}.%(ext)s")
                                with yt_dlp.YoutubeDL(download_opts) as ydl:
                                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
                                    stream_data = {
                                        'url': str(output_path),
                                        'title': info.get('title', f"Downloaded Song ({video_id})"),
                                        'is_local': True
                                    }
                            except Exception as e:
                                print(f"Download error: {str(e)}")
                    
                    if not stream_data:
                        await ctx.send("❌ Could not get audio stream. Please try another video or search term.")
                        return
                    
                    # Cache successful stream URL if it's not a local file
                    if not stream_data.get('is_local', False):
                        await self.cache_stream(video_id, stream_data['url'])
                
            else:
                # For non-YouTube URLs, use yt-dlp
                with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if 'url' not in info:
                        await ctx.send("❌ Could not extract audio URL")
                        return
                    
                    stream_data = {
                        'url': info['url'],
                        'title': info.get('title', 'Unknown Title'),
                        'is_local': False
                    }
            
            # Create audio source and play
            try:
                if stream_data.get('is_local', False):
                    source = discord.FFmpegOpusAudio(stream_data['url'], **self.FFMPEG_OPTIONS)
                else:
                    try:
                        source = await discord.FFmpegOpusAudio.from_probe(stream_data['url'], **self.FFMPEG_OPTIONS)
                    except Exception as e:
                        # If FFmpeg fails with 403 Forbidden, try downloading instead
                        if "403 Forbidden" in str(e):
                            await ctx.send("⚠️ Stream URL expired or forbidden. Downloading instead...")
                            
                            # Try to download using yt-dlp
                            if is_youtube:
                                try:
                                    output_path = self.cache_dir / f"{video_id}.mp3"
                                    download_opts = self.ydl_opts.copy()
                                    download_opts['outtmpl'] = str(self.cache_dir / f"{video_id}.%(ext)s")
                                    with yt_dlp.YoutubeDL(download_opts) as ydl:
                                        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
                                        source = discord.FFmpegOpusAudio(str(output_path), **self.FFMPEG_OPTIONS)
                                except Exception as dl_err:
                                    raise Exception(f"Failed to download after 403 error: {str(dl_err)}")
                            else:
                                raise e
                        else:
                            raise e
            except Exception as e:
                await ctx.send(f"❌ Error creating audio source: {str(e)}")
                return
            
            self.current_songs[str(ctx.guild.id)] = stream_data['title']
            
            vc = self.voice_clients[str(ctx.guild.id)]
            if not vc.is_connected():
                await ctx.send("❌ Voice client disconnected")
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
            error_msg = f"❌ Error playing song: {str(e)}\n```{traceback.format_exc()}```"
            await ctx.send(error_msg[:1900])

    async def search_youtube(self, query):
        """Search YouTube with multiple fallback methods"""
        # Try Invidious API first
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
        
        # If Invidious fails, try direct search
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
            # Check if user is in voice channel
            if not ctx.author.voice:
                return await ctx.send("You need to be in a voice channel!")
            
            # Connect to voice channel if not already connected
            channel = ctx.author.voice.channel
            guild_id = str(ctx.guild.id)
            
            try:
                if guild_id not in self.voice_clients:
                    vc = await channel.connect()
                    self.voice_clients[guild_id] = vc
                else:
                    vc = self.voice_clients[guild_id]
                    if vc.channel != channel:
                        await vc.move_to(channel)
            except Exception as e:
                await ctx.send(f"❌ Error connecting to voice channel: {str(e)}")
                return

            # Initialize queue if doesn't exist
            if guild_id not in self.queue:
                self.queue[guild_id] = []

            # Check if query is a Spotify URL
            spotify_info = await self.handle_spotify_url(query)
            if spotify_info:
                await ctx.send(f"🎵 Found Spotify track, searching on YouTube: {spotify_info}")
                query = spotify_info  # Use track info as search query

            # Check if query is a URL (YouTube)
            url_pattern = re.compile(
                r'^(?:http|ftp)s?://'  # http:// or https://
                r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  # domain...
                r'localhost|'  # localhost...
                r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
                r'(?::\d+)?'  # optional port
                r'(?:/?|[/?]\S+)$', re.IGNORECASE)

            if url_pattern.match(query) and 'spotify.com' not in query:
                # Direct URL play (YouTube)
                if vc.is_playing():
                    self.queue[guild_id].append(query)
                    with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                        info = ydl.extract_info(query, download=False)
                        await ctx.send(f"🎵 Added to queue: **{info['title']}**")
                else:
                    await self.play_song(ctx, query)
            else:
                # Search YouTube
                await ctx.send("🔍 Searching...")
                results = await self.search_youtube(query)
                if not results:
                    return await ctx.send("❌ No results found!")

                # Store search results for this user
                self.search_results[ctx.author.id] = results

                # Create embed with search results
                embed = discord.Embed(
                    title="🔍 Search Results",
                    description="Type the number of the song you want to play (1-10) or 'cancel' to abort:",
                    color=discord.Color.blue()
                )

                for i, entry in enumerate(results, 1):
                    title = entry['title']
                    duration = entry['duration_string']
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
                    await ctx.send(f"❌ Error during selection: {str(e)}")

        except Exception as e:
            error_msg = f"❌ Error: {str(e)}\n```{traceback.format_exc()}```"
            await ctx.send(error_msg[:1900])

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