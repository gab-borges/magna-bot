import discord
from discord.ext import commands
from random import choice
import os
import asyncpraw as praw

# > Commands that use the Reddit API <

class Reddit(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reddit = praw.Reddit(
            client_id=os.getenv("REDDIT_CLIENT_ID"),
            client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
            user_agent=os.getenv("REDDIT_USER_AGENT")
        )

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{__name__} is ready!")

    # Commands
    @commands.command()
    async def meme(self, ctx: commands.Context):
        # List of subreddits
        subreddits = ["shitposting", "therewasanattempt", "yesyesyesyesno", "WhyWomenLiveLonger"]

        posts_list = []

        for subreddit_name in subreddits:
            subreddit = await self.reddit.subreddit(subreddit_name)

            async for post in subreddit.hot(limit=25):
                if any(post.url.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif"]):
                    author_name = post.author.name
                    posts_list.append((post.url, post.title, post.selftext, author_name, subreddit_name))

        if posts_list:
            random_post = choice(posts_list)

            meme_embed = discord.Embed(
                title=random_post[1],
                description=random_post[2] if random_post[2] else "",
                color=discord.Color.green()
            )
            meme_embed.set_author(name=f"Requested by {ctx.author.name}", icon_url=ctx.author.avatar)
            meme_embed.set_image(url=random_post[0])
            meme_embed.set_footer(text=f"Post by u/{random_post[3]} from r/{random_post[4]}", icon_url=None)

            await ctx.send(embed=meme_embed)
        
        else:
            await ctx.send("Error")

    def cog_unload(self):
        self.bot.loop.create_task(self.reddit.close())

async def setup(bot):
    await bot.add_cog(Reddit(bot))
