import discord
from discord.ext import commands
import matplotlib.pyplot as plt
import matplotlib as mpl
import io
import os

class LaTeX(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Configure matplotlib to use AMS Euler font
        mpl.rcParams['mathtext.fontset'] = 'stix'  # STIX fonts include Euler
        mpl.rcParams['mathtext.rm'] = 'Euler'
        mpl.rcParams['mathtext.it'] = 'Euler'
        mpl.rcParams['mathtext.bf'] = 'Euler'
        
    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{__name__} is online!")

    @commands.command()
    async def tex(self, ctx, *, latex_text: str):
        """Convert LaTeX text to an image"""
        try:
            async with ctx.typing():
                # Create figure with white background
                plt.figure(figsize=(10, 3), facecolor='white')
                plt.axis('off')  # Hide axes
                
                # Add text with matplotlib's math rendering
                plt.text(0.5, 0.5, f"${latex_text}$", 
                         size=36,  # Larger font size
                         ha='center', 
                         va='center',
                         transform=plt.gca().transAxes)
                
                # Tight layout to remove extra whitespace
                plt.tight_layout()
                
                # Save to a BytesIO object with higher DPI
                buf = io.BytesIO()
                plt.savefig(buf, format='png', dpi=400, bbox_inches='tight', pad_inches=0.2)
                buf.seek(0)
                plt.close()
                
                # Send the image
                await ctx.send(file=discord.File(buf, filename="latex.png"))
                    
        except Exception as e:
            await ctx.send(f"❌ Error rendering LaTeX: {str(e)}")
            # Print full error for debugging
            import traceback
            traceback.print_exc()

async def setup(bot):
    await bot.add_cog(LaTeX(bot))