import discord
from discord.ext import commands
import yt_dlp
import asyncio
import re
import traceback
import requests
from bs4 import BeautifulSoup

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_clients = {}
        self.current_songs = {}
        self.queue = {}
        self.search_results = {}
        
        # YT-DLP configuration
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'noplaylist': True,
            'quiet': True
        }
        
        # Search configuration
        self.search_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'default_search': 'ytsearch10',
            'extract_flat': True
        }
        
        # FFMPEG options
        self.FFMPEG_OPTIONS = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn',
        }

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{__name__} is online!")

    async def play_next(self, ctx):
        if str(ctx.guild.id) in self.queue and self.queue[str(ctx.guild.id)]:
            # Get next song from queue
            url = self.queue[str(ctx.guild.id)].pop(0)
            await self.play_song(ctx, url)

    async def play_song(self, ctx, url):
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if 'url' not in info:
                    await ctx.send("❌ Could not extract audio URL from video")
                    return
                    
                url2 = info['url']
                title = info.get('title', 'Unknown Title')
                
                try:
                    source = await discord.FFmpegOpusAudio.from_probe(url2, **self.FFMPEG_OPTIONS)
                except Exception as e:
                    await ctx.send(f"❌ Error creating audio source: {str(e)}")
                    return
                    
                self.current_songs[str(ctx.guild.id)] = title
                
                vc = self.voice_clients[str(ctx.guild.id)]
                if not vc.is_connected():
                    await ctx.send("❌ Voice client disconnected")
                    return
                    
                vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
                    self.play_next(ctx), self.bot.loop).result())
                
                embed = discord.Embed(
                    title="🎵 Now Playing",
                    description=f"**{title}**",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
                
        except Exception as e:
            error_msg = f"❌ Error playing song: {str(e)}\n```{traceback.format_exc()}```"
            await ctx.send(error_msg[:1900])  # Discord message length limit

    async def search_youtube(self, query):
        try:
            with yt_dlp.YoutubeDL(self.search_opts) as ydl:
                info = ydl.extract_info(f"ytsearch10:{query}", download=False)
                if 'entries' in info:
                    return info['entries']
                return []
        except Exception as e:
            print(f"Search error: {str(e)}")
            return []

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
                    title = entry.get('title', 'Unknown Title')
                    duration = entry.get('duration_string', 'Unknown Duration')
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
                    url = f"https://www.youtube.com/watch?v={selected['id']}"

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