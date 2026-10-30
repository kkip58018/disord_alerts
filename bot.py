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


# ============================================================
# Turso Database Helpers (Synchronous, wrapped in threads)
# ============================================================
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


def _list_user_alerts_sync(user_id: str):
    conn = _connect()
    try:
        cursor = conn.execute(
            "SELECT id, pair, target_price, direction FROM alerts WHERE user_id = ? AND triggered = 0 ORDER BY id",
            (user_id,),
        )
        return cursor.fetchall()
    finally:
        conn.close()


async def list_user_alerts(user_id: str):
    return await asyncio.to_thread(_list_user_alerts_sync, user_id)


def _delete_alert_sync(alert_id: int, user_id: str):
    conn = _connect()
    try:
        cursor = conn.execute(
            "DELETE FROM alerts WHERE id = ? AND user_id = ?",
            (alert_id, user_id),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


async def delete_alert(alert_id: int, user_id: str):
    return await asyncio.to_thread(_delete_alert_sync, alert_id, user_id)


# ============================================================
# Slash Commands
# ============================================================
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
    # Acknowledge the interaction IMMEDIATELY (must succeed within 3 seconds).
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.errors.NotFound:
        print(f"Interaction expired for user {interaction.user.id} (pair={pair}).")
        return

    pair = pair.upper()

    try:
        await insert_alert(str(interaction.user.id), pair, price, direction)
    except Exception as e:
        print(f"DB error inserting alert: {e}")
        await interaction.followup.send(
            "❌ Failed to save your alert. Please try again later.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"✅ Alert set for **{pair}** {direction} **{price:.4f}**.",
        ephemeral=True,
    )


@tree.command(name="alerts", description="List your active price alerts")
async def alerts(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    rows = await list_user_alerts(str(interaction.user.id))
    if not rows:
        await interaction.followup.send(
            "You have no active alerts. Use `/alert` to create one.",
            ephemeral=True,
        )
        return

    lines = ["**Your active alerts:**"]
    for alert_id, pair, target, direction in rows:
        lines.append(f"`#{alert_id}` **{pair}** {direction} **{target:.4f}**")

    await interaction.followup.send("\n".join(lines), ephemeral=True)


@tree.command(name="delalert", description="Delete one of your alerts by ID")
@app_commands.describe(alert_id="The ID shown by /alerts")
async def delalert(interaction: discord.Interaction, alert_id: int):
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    deleted = await delete_alert(alert_id, str(interaction.user.id))
    if deleted:
        await interaction.followup.send(
            f"🗑️ Alert `#{alert_id}` deleted.", ephemeral=True
        )
    else:
        await interaction.followup.send(
            f"❌ No alert with ID `#{alert_id}` was found for you.",
            ephemeral=True,
        )


@tree.command(name="price", description="Get the current price of a forex pair")
@app_commands.describe(pair="Currency pair (e.g., EURUSD)")
async def price(interaction: discord.Interaction, pair: str):
    try:
        await interaction.response.defer(ephemeral=False)
    except discord.errors.NotFound:
        return

    pair = pair.upper()
    bq = Biquote()
    try:
        tick = await asyncio.to_thread(bq.tick, pair)
        current_price = tick["mid"]
        await interaction.followup.send(
            f"💹 **{pair}** current price: **{current_price:.5f}**"
        )
    except Exception as e:
        print(f"Error fetching price for {pair}: {e}")
        await interaction.followup.send(
            f"❌ Could not fetch price for **{pair}**. Is it a valid pair?",
            ephemeral=True,
        )


# ============================================================
# Background Task: Check Prices
# ============================================================
@tasks.loop(minutes=5)
async def check_prices():
    bq = Biquote()
    try:
        active_alerts = await fetch_active_alerts()

        for alert_id, user_id, pair, target_price, direction in active_alerts:
            try:
                # Offload the blocking tick() call to a thread.
                tick = await asyncio.to_thread(bq.tick, pair)
                current_price = tick["mid"]

                should_trigger = (
                    (direction == "above" and current_price >= target_price)
                    or (direction == "below" and current_price <= target_price)
                )

                if should_trigger:
                    await mark_triggered(alert_id)

                    try:
                        user = await bot.fetch_user(int(user_id))
                        if user:
                            await user.send(
                                f"🔔 **Alert Triggered!**\n"
                                f"**{pair}** is now at **{current_price:.5f}** "
                                f"({direction} your target of {target_price:.5f})."
                            )
                    except discord.errors.Forbidden:
                        print(f"Cannot DM user {user_id} (DMs disabled).")
                    except Exception as e:
                        print(f"Error DMing user {user_id}: {e}")
            except Exception as e:
                print(f"Error checking {pair} for alert {alert_id}: {e}")
    except Exception as e:
        print(f"Error in check_prices task: {e}")


@check_prices.before_loop
async def before_check_prices():
    await bot.wait_until_ready()


# ============================================================
# Bot Events
# ============================================================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await init_db()
    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    if not check_prices.is_running():
        check_prices.start()


@bot.event
async def on_error(event, *args, **kwargs):
    print(f"Unhandled error in event {event}")


# --- Local entry point (Render uses main.py instead) ---
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
