import discord
from discord.ext import commands
import re
import random

class Roll(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.dice_pattern = re.compile(r'(\d+)d(\d+)([+-]\d+)?')

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{__name__} is online!")

    @commands.command()
    async def roll(self, ctx, *, dice_expression: str):
        """Roll dice using the format XdY+Z
        Example: %roll 2d20+5 (rolls 2 d20 dice and adds 5)
        You can combine multiple dice: %roll 5d6+2d4+2"""
        
        try:
            # Split the expression by + signs
            dice_parts = dice_expression.split('+')
            total = 0
            rolls = []
            
            for part in dice_parts:
                part = part.strip()
                # Check if it's a dice roll (XdY) or just a number
                if 'd' in part:
                    match = self.dice_pattern.match(part)
                    if match:
                        num_dice = int(match.group(1))
                        num_sides = int(match.group(2))
                        
                        # Limit reasonable numbers to prevent abuse
                        if num_dice > 100:
                            await ctx.send("❌ You can't roll more than 100 dice at once!")
                            return
                        if num_sides > 100:
                            await ctx.send("❌ Dice can't have more than 100 sides!")
                            return
                        
                        # Roll the dice
                        dice_rolls = [random.randint(1, num_sides) for _ in range(num_dice)]
                        rolls.append((dice_rolls, num_sides))
                        total += sum(dice_rolls)
                else:
                    # It's a modifier number
                    total += int(part)
            
            # Create a nice embed to show the results
            embed = discord.Embed(
                title="🎲 Dice Roll Results",
                color=discord.Color.blue()
            )
            
            # Add fields for each type of dice rolled
            for dice_rolls, sides in rolls:
                roll_str = f"[{', '.join(map(str, dice_rolls))}]"
                embed.add_field(
                    name=f"d{sides} rolls",
                    value=roll_str,
                    inline=False
                )
            
            # Add the total
            embed.add_field(
                name="Total",
                value=str(total),
                inline=False
            )
            
            await ctx.send(embed=embed)
            
        except ValueError:
            await ctx.send("❌ Invalid dice format! Use format like: 2d20+5 or 3d6+2d4+2")
        except Exception as e:
            await ctx.send(f"❌ Error rolling dice: {str(e)}")

async def setup(bot):
    await bot.add_cog(Roll(bot)) 