import discord
from discord.ext import commands
import matplotlib.pyplot as plt
import io
import re

class Latex(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Regular expression to match LaTeX expressions between $$
        self.latex_pattern = re.compile(r'\$([^$]+)\$')
        
        # Common LaTeX symbol corrections
        self.symbol_corrections = {
            r'\infin': r'\infty',
            r'\infinity': r'\infty',
            r'\alfa': r'\alpha',
            r'\Beta': r'\beta',
            r'\Sigma': r'\sum',
        }
        
        # Configure matplotlib to use AMS Euler font
        plt.rcParams.update({
            'text.usetex': True,
            'text.latex.preamble': r'\usepackage{amsmath}\usepackage{amssymb}\usepackage{eulervm}',
            'font.family': 'euler'
        })

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{__name__} is online!")

    def fix_latex_symbols(self, latex_expr):
        """Fix common LaTeX symbol mistakes"""
        for wrong, correct in self.symbol_corrections.items():
            latex_expr = latex_expr.replace(wrong, correct)
        return latex_expr

    def prepare_text(self, text):
        """Prepare text for LaTeX rendering by escaping special characters"""
        # Replace LaTeX special characters with their escaped versions
        special_chars = ['\\', '{', '}', '_', '^', '#', '&', '$', '%']
        for char in special_chars:
            text = text.replace(char, f'\\{char}')
        return text

    def render_mixed_text(self, text):
        """Render text with both normal text and LaTeX expressions"""
        try:
            # Clear any existing plots
            plt.clf()
            
            # Create figure with white background
            fig = plt.figure(figsize=(12, 1.5))  # Made figure larger for complex equations
            fig.patch.set_facecolor('white')
            
            # Split text into parts and process
            parts = self.latex_pattern.split(text)
            
            # Prepare the complete LaTeX string
            latex_string = ""
            for i, part in enumerate(parts):
                if i % 2 == 0:  # Normal text
                    if part.strip():
                        latex_string += self.prepare_text(part)
                else:  # LaTeX expression
                    latex_string += f"${self.fix_latex_symbols(part)}$"
            
            # Add text with LaTeX
            plt.text(0.5, 0.5, latex_string,
                    horizontalalignment='center',
                    verticalalignment='center',
                    transform=fig.transFigure,
                    fontsize=16)
            
            # Remove axes
            plt.axis('off')
            
            # Save to bytes buffer with higher DPI
            buf = io.BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight', 
                       facecolor='white', dpi=400, pad_inches=0.2)
            buf.seek(0)
            
            # Clear the figure
            plt.close(fig)
            
            return buf
        except Exception as e:
            raise ValueError(f"LaTeX rendering error: {str(e)}\nTry checking your LaTeX syntax and symbols.")

    @commands.command(name="tex")
    async def tex(self, ctx, *, text):
        r"""Convert text with LaTeX expressions to a single image
        Usage: %tex Your text here $latex_expression$ more text here
        Example: %tex The area of a circle is $A = \pi r^2$ in square units"""
        
        try:
            # Render the mixed text to image
            image_buf = self.render_mixed_text(text)
            
            # Create discord file from buffer
            file = discord.File(fp=image_buf, filename='latex.png')
            
            # Send the image
            await ctx.send(file=file)
            
        except ValueError as e:
            await ctx.send(f"❌ {str(e)}")
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")

async def setup(bot):
    await bot.add_cog(Latex(bot)) 