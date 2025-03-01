import discord
from discord.ext import commands
import subprocess
import os
import tempfile

class LaTeX(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{__name__} is online!")

    @commands.command()
    async def tex(self, ctx, *, latex_text: str):
        """Convert LaTeX text to an image"""
        try:
            async with ctx.typing():
                # Create a temporary directory
                with tempfile.TemporaryDirectory() as tmpdir:
                    # Create LaTeX document
                    tex_content = (
                        "\\documentclass[12pt]{article}\n"
                        "\\usepackage{amsmath,amssymb}\n"
                        "\\usepackage[paperwidth=25cm,paperheight=10cm,margin=4cm]{geometry}\n"
                        "\\pagestyle{empty}\n"
                        "\\begin{document}\n"
                        "\\begin{center}\n"
                        f"${latex_text}$\n"
                        "\\end{center}\n"
                        "\\end{document}\n"
                    )
                    
                    # Write LaTeX content to file
                    tex_file = os.path.join(tmpdir, "equation.tex")
                    with open(tex_file, "w") as f:
                        f.write(tex_content)
                    
                    # Compile LaTeX to DVI
                    subprocess.run(
                        ["latex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_file],
                        capture_output=True
                    )
                    
                    # Convert DVI to PNG with high resolution
                    dvi_file = os.path.join(tmpdir, "equation.dvi")
                    png_file = os.path.join(tmpdir, "equation.png")
                    subprocess.run([
                        "dvipng",
                        "-D", "300",  # Resolution
                        "-bg", "white",  # Background color
                        "-T", "tight",  # Tight bounding box
                        "-o", png_file,
                        dvi_file
                    ])
                    
                    # Send the image
                    await ctx.send(file=discord.File(png_file))
                    
        except Exception as e:
            await ctx.send(f"❌ Error rendering LaTeX: {str(e)}")
            import traceback
            traceback.print_exc()

async def setup(bot):
    await bot.add_cog(LaTeX(bot))