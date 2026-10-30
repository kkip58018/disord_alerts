import os
import staypresent

# Configure the web server (for Render's health checks)
staypresent.web.json({"status": "running", "service": "eurusd-alert-bot"})

# Self-ping to prevent the free tier from sleeping every 4 minutes[reference:10]
# IMPORTANT: Replace with your actual Render URL after the first deploy.
staypresent.cron(
    "https://disord-bot-alerts.onrender.com",
    interval=240,
)

# Run the bot process (bot.py) with crash recovery[reference:11]
staypresent.run(
    "bot.py",
    port=int(os.getenv("PORT", 8080)),
    restart_on_crash=True,
)
