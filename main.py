import os
import staypresent

# Configure the health-check web server (Render needs an open port).
staypresent.web.json({"status": "running", "service": "eurusd-alert-bot"})

# Self-ping to prevent the free tier from sleeping.
# IMPORTANT: Replace this URL with your actual Render URL after first deploy.
staypresent.cron(
    "https://disord-bot-alerts.onrender.com",
    interval=240,  # every 4 minutes
)

# Run the bot process with crash recovery.
staypresent.run(
    "bot.py",
    port=int(os.getenv("PORT", 8080)),
    restart_on_crash=True,
)
