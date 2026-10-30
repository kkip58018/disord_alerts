import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
import turso.aio  # Async Turso driver[reference:7]
from biquote import Biquote

# --- Configuration ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TURSO_DB_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

# --- Discord Bot Setup ---
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# --- Turso Database Helper ---
async def get_db():
    return await turso.aio.connect(TURSO_DB_URL, auth_token=TURSO_AUTH_TOKEN)

async def init_db():
    async with await get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                pair TEXT NOT NULL,
                target_price REAL NOT NULL,
                direction TEXT NOT NULL,
                triggered INTEGER DEFAULT 0
            )
        """)
        await db.commit()

# --- Slash Command: /alert ---
@tree.command(name="alert", description="Set a price alert for a forex pair")
@app_commands.describe(
    pair="Currency pair (e.g., EURUSD)",
    price="The target price",
    direction="Alert when price goes 'above' or 'below' the target"
)
@app_commands.choices(direction=[
    app_commands.Choice(name="Above", value="above"),
    app_commands.Choice(name="Below", value="below"),
])
async def alert(interaction: discord.Interaction, pair: str, price: float, direction: str):
    await interaction.response.defer(ephemeral=True) # Acknowledge the command

    pair = pair.upper()
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO alerts (user_id, pair, target_price, direction) VALUES (?, ?, ?, ?)",
            (str(interaction.user.id), pair, price, direction)
        )
        await db.commit()

    await interaction.followup.send(
        f"✅ Alert set for **{pair}** {direction} **{price:.4f}**.",
        ephemeral=True
    )

# --- Background Task: Check Prices ---
@tasks.loop(minutes=5) # Adjust frequency as needed
async def check_prices():
    bq = Biquote()
    try:
        async with await get_db() as db:
            # Fetch all active (non-triggered) alerts
            cursor = await db.execute("SELECT id, user_id, pair, target_price, direction FROM alerts WHERE triggered = 0")
            active_alerts = await cursor.fetchall()

            for alert_id, user_id, pair, target_price, direction in active_alerts:
                try:
                    tick = bq.tick(pair) # Fetch real-time price[reference:8]
                    current_price = tick["mid"]

                    should_trigger = (
                        (direction == "above" and current_price >= target_price) or
                        (direction == "below" and current_price <= target_price)
                    )

                    if should_trigger:
                        # Mark as triggered in DB
                        await db.execute("UPDATE alerts SET triggered = 1 WHERE id = ?", (alert_id,))
                        await db.commit()

                        # Send DM to the user
                        user = await bot.fetch_user(int(user_id))
                        if user:
                            await user.send(
                                f"🔔 **Alert Triggered!**\n"
                                f"**{pair}** is now at **{current_price:.4f}** "
                                f"({direction} your target of {target_price:.4f})."
                            )
                except Exception as e:
                    print(f"Error checking {pair} for alert {alert_id}: {e}")
    except Exception as e:
        print(f"Error in check_prices task: {e}")

# --- Bot Events ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await init_db()
    await tree.sync() # Sync slash commands with Discord[reference:9]
    if not check_prices.is_running():
        check_prices.start()

# --- Entry Point (for StayPresent) ---
# The actual bot.run() is placed in a separate file to be wrapped by StayPresent.
# However, for simplicity, we can let StayPresent run this script directly if we
# structure the entry point correctly in main.py.
if __name__ == "__main__":
    # When running locally for testing, you can use bot.run(DISCORD_TOKEN)
    # On Render, StayPresent will handle starting the process.
    pass