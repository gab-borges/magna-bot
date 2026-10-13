import discord
from discord.ext import commands
import logging
import os
import random
import asyncpraw as praw
from asyncprawcore.exceptions import AsyncPrawcoreException

logger = logging.getLogger(__name__)

SUBREDDITS = ["shitposting", "therewasanattempt", "yesyesyesyesno", "WhyWomenLiveLonger"]
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif")
POSTS_PER_SUBREDDIT = 25
# Discord rejects embeds whose title or description is longer than this.
EMBED_TITLE_LIMIT = 256
EMBED_DESCRIPTION_LIMIT = 4096


def truncate(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + "…"


# > Commands that use the Reddit API <

class Reddit(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reddit = praw.Reddit(
            client_id=os.getenv("REDDIT_CLIENT_ID"),
            client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
            user_agent=os.getenv("REDDIT_USER_AGENT")
        )

    async def find_memes(self, subreddit_name, allow_nsfw):
        """Image posts currently hot on a subreddit."""
        subreddit = await self.reddit.subreddit(subreddit_name)
        memes = []
        async for post in subreddit.hot(limit=POSTS_PER_SUBREDDIT):
            if post.stickied or (post.over_18 and not allow_nsfw):
                continue
            if post.url.lower().endswith(IMAGE_EXTENSIONS):
                memes.append(post)
        return memes

    # Commands
    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def meme(self, ctx: commands.Context):
        is_nsfw = getattr(ctx.channel, "is_nsfw", None)
        allow_nsfw = bool(is_nsfw and is_nsfw())

        # One subreddit is usually enough; only try the next if it has no images.
        post, subreddit_name = None, None
        async with ctx.typing():
            for subreddit_name in random.sample(SUBREDDITS, len(SUBREDDITS)):
                try:
                    memes = await self.find_memes(subreddit_name, allow_nsfw)
                except AsyncPrawcoreException:
                    logger.exception("Could not fetch r/%s", subreddit_name)
                    continue
                if memes:
                    post = random.choice(memes)
                    break

        if post is None:
            await ctx.send("❌ Couldn't find a meme right now. Try again later.")
            return

        meme_embed = discord.Embed(
            title=truncate(post.title, EMBED_TITLE_LIMIT),
            description=truncate(post.selftext, EMBED_DESCRIPTION_LIMIT) or None,
            color=discord.Color.green()
        )
        meme_embed.set_author(name=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
        meme_embed.set_image(url=post.url)
        # Deleted accounts show up as a post without an author.
        author_name = post.author.name if post.author else "[deleted]"
        meme_embed.set_footer(text=f"Post by u/{author_name} from r/{subreddit_name}")

        await ctx.send(embed=meme_embed)

    async def cog_unload(self):
        await self.reddit.close()

async def setup(bot):
    await bot.add_cog(Reddit(bot))
