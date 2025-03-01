import discord
from discord.ext import commands
import yt_dlp
import asyncio

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_clients = {}
        self.current_songs = {}
        self.queue = {}
        
        # YT-DLP configuration
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
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
                url2 = info['url']
                title = info['title']
                
                source = await discord.FFmpegOpusAudio.from_probe(url2, **self.FFMPEG_OPTIONS)
                self.current_songs[str(ctx.guild.id)] = title
                
                vc = self.voice_clients[str(ctx.guild.id)]
                vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
                    self.play_next(ctx), self.bot.loop))
                
                embed = discord.Embed(
                    title="🎵 Now Playing",
                    description=f"**{title}**",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
                
        except Exception as e:
            await ctx.send(f"❌ Error playing song: {str(e)}")

    @commands.command()
    async def play(self, ctx, url):
        """Play a YouTube URL"""
        try:
            # Connect to voice channel if not already connected
            if not ctx.author.voice:
                return await ctx.send("You need to be in a voice channel!")
                
            channel = ctx.author.voice.channel
            guild_id = str(ctx.guild.id)
            
            if guild_id not in self.voice_clients:
                vc = await channel.connect()
                self.voice_clients[guild_id] = vc
            else:
                vc = self.voice_clients[guild_id]
                if vc.channel != channel:
                    await vc.move_to(channel)
            
            # Initialize queue if doesn't exist
            if guild_id not in self.queue:
                self.queue[guild_id] = []
            
            # Add to queue if something is already playing
            if vc.is_playing():
                self.queue[guild_id].append(url)
                with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    await ctx.send(f"🎵 Added to queue: **{info['title']}**")
            else:
                await self.play_song(ctx, url)
                
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")

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
                info = ydl.extract_info(url, download=False)
                embed.add_field(name=f"{i}. ", value=info['title'], inline=False)
                
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

async def setup(bot):
    await bot.add_cog(Music(bot)) 