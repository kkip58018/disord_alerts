import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
import turso_serverless
from biquote import Biquote

# --- Configuration ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TURSO_DB_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

# --- Discord Bot Setup ---
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# --- Turso Database Helpers (Synchronous, wrapped in threads) ---
def _connect():
    """Create a new synchronous Turso connection."""
    return turso_serverless.connect(
        TURSO_DB_URL,
        auth_token=TURSO_AUTH_TOKEN,
    )

def _init_db_sync():
    """Synchronous DB initialization."""
    conn = _connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                pair TEXT NOT NULL,
                target_price REAL NOT NULL,
                direction TEXT NOT NULL,
                triggered INTEGER DEFAULT 0
            )
        """)
        conn.commit()
    finally:
        conn.close()

async def init_db():
    await asyncio.to_thread(_init_db_sync)

def _insert_alert_sync(user_id: str, pair: str, price: float, direction: str):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO alerts (user_id, pair, target_price, direction) VALUES (?, ?, ?, ?)",
            (user_id, pair, price, direction),
        )
        conn.commit()
    finally:
        conn.close()

async def insert_alert(user_id: str, pair: str, price: float, direction: str):
    await asyncio.to_thread(_insert_alert_sync, user_id, pair, price, direction)

def _fetch_active_alerts_sync():
    conn = _connect()
    try:
        cursor = conn.execute(
            "SELECT id, user_id, pair, target_price, direction FROM alerts WHERE triggered = 0"
        )
        return cursor.fetchall()
    finally:
        conn.close()

async def fetch_active_alerts():
    return await asyncio.to_thread(_fetch_active_alerts_sync)

def _mark_triggered_sync(alert_id: int):
    conn = _connect()
    try:
        conn.execute("UPDATE alerts SET triggered = 1 WHERE id = ?", (alert_id,))
        conn.commit()
    finally:
        conn.close()

async def mark_triggered(alert_id: int):
    await asyncio.to_thread(_mark_triggered_sync, alert_id)

# --- Slash Command: /alert ---
@tree.command(name="alert", description="Set a price alert for a forex pair")
@app_commands.describe(
    pair="Currency pair (e.g., EURUSD)",
    price="The target price",
    direction="Alert when price goes 'above' or 'below' the target",
)
@app_commands.choices(direction=[
    app_commands.Choice(name="Above", value="above"),
    app_commands.Choice(name="Below", value="below"),
])
async def alert(interaction: discord.Interaction, pair: str, price: float, direction: str):
    await interaction.response.defer(ephemeral=True)

    pair = pair.upper()
    await insert_alert(str(interaction.user.id), pair, price, direction)

    await interaction.followup.send(
        f"✅ Alert set for **{pair}** {direction} **{price:.4f}**.",
        ephemeral=True,
    )

# --- Background Task: Check Prices ---
@tasks.loop(minutes=5)
async def check_prices():
    bq = Biquote()
    try:
        active_alerts = await fetch_active_alerts()

        for alert_id, user_id, pair, target_price, direction in active_alerts:
            try:
                tick = bq.tick(pair)
                current_price = tick["mid"]

                should_trigger = (
                    (direction == "above" and current_price >= target_price)
                    or (direction == "below" and current_price <= target_price)
                )

                if should_trigger:
                    await mark_triggered(alert_id)

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
    await tree.sync()
    if not check_prices.is_running():
        check_prices.start()

# --- Local entry point (Render will use main.py + StayPresent) ---
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)