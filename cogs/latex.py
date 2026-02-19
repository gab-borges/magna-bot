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
        
        # Patterns for display math delimiters
        self.display_bracket_pattern = re.compile(r'\\\[(.+?)\\\]', re.DOTALL)
        self.display_dollar_pattern = re.compile(r'\$\$(.+?)\$\$', re.DOTALL)
        
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
            'text.latex.preamble': r'\usepackage{amsmath}\usepackage{amssymb}\usepackage{stmaryrd}\usepackage{eulervm}',
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

    def preprocess_delimiters(self, text):
        """Convert alternative math delimiters to $...$ format"""
        def to_display(m):
            return '$\\displaystyle ' + m.group(1) + '$'
        
        # Convert \[...\] to $\displaystyle ...$
        text = self.display_bracket_pattern.sub(to_display, text)
        # Convert $$...$$ to $\displaystyle ...$
        text = self.display_dollar_pattern.sub(to_display, text)
        return text

    def _width_to_inches(self, width):
        """Convert a LaTeX width string (e.g. '16cm') to inches for matplotlib figsize"""
        conversions = {'cm': 0.3937, 'in': 1.0, 'mm': 0.03937, 'pt': 1/72}
        for unit, factor in conversions.items():
            if width.endswith(unit):
                try:
                    return float(width[:-len(unit)]) * factor
                except ValueError:
                    break
        return 12  # fallback

    def render_mixed_text(self, text, width='32cm'):
        """Render text with both normal text and LaTeX expressions"""
        try:
            # Normalize all math delimiters to $...$
            text = self.preprocess_delimiters(text)

            # Clear any existing plots
            plt.clf()
            
            # Create figure with size matching the parbox width
            fig_width = self._width_to_inches(width) + 1  # small margin
            fig = plt.figure(figsize=(fig_width, 10))
            fig.patch.set_facecolor('none')
            fig.patch.set_alpha(0)
            
            # Split text into parts and process
            parts = self.latex_pattern.split(text)
            
            # Prepare the complete LaTeX string
            latex_string = ""
            for i, part in enumerate(parts):
                if i % 2 == 0:  # Normal text
                    if part.strip():
                        # Replace newlines with LaTeX line breaks for text
                        latex_string += self.prepare_text(part).replace('\n', r' \\ ')
                else:  # LaTeX expression (newlines are just whitespace in math)
                    latex_string += f"${self.fix_latex_symbols(part.replace(chr(10), ' '))}$"
            
            # Wrap in \parbox for automatic line breaking
            latex_string = r'\parbox{' + width + '}{' + latex_string + '}'
            
            # Add text with LaTeX
            plt.text(0.05, 0.95, latex_string,
                    horizontalalignment='left',
                    verticalalignment='top',
                    transform=fig.transFigure,
                    fontsize=28,
                    color='white')
            
            # Remove axes
            plt.axis('off')
            
            # Save to bytes buffer with higher DPI
            buf = io.BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight', 
                       facecolor='none', dpi=400, pad_inches=0.2, transparent=True)
            buf.seek(0)
            
            # Clear the figure
            plt.close(fig)
            
            return buf
        except Exception as e:
            msg = f"LaTeX rendering error: {str(e)}"
            if len(msg) > 300:
                msg = msg[:300] + "..."
            raise ValueError(f"{msg}\nTry checking your LaTeX syntax and symbols.")

    @commands.command(name="tex")
    async def tex(self, ctx, *, text):
        r"""Convert text with LaTeX expressions to a single image
        Usage: %tex [-w WIDTH] Your text here $latex_expression$ more text here
        Example: %tex The area of a circle is $A = \pi r^2$ in square units
        Example: %tex -w 20cm Long text that should wrap at 20cm"""
        
        try:
            # Parse optional -w flag for parbox width
            width = '32cm'
            width_match = re.match(r'-w\s+(\S+)\s+', text)
            if width_match:
                width = width_match.group(1)
                # Default to cm if no unit specified
                if width.replace('.', '', 1).isdigit():
                    width += 'cm'
                text = text[width_match.end():]
            
            # Render the mixed text to image
            image_buf = self.render_mixed_text(text, width=width)
            
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