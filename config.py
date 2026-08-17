import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    TOKEN = os.getenv("BOT_TOKEN")
    SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
    DEFAULT_PREFIX = ">"
    OWNER_IDS = [1354490199140470784] 

    # ── Public Lavalink Nodes ──
    LAVALINK_NODES = [
        {
            "host": "lava2.kasawa.pro",
            "port": 2334,
            "password": "youshallnotpass",
            "region": "us",
            "name": "kasawa",
            "ssl": False,
        },
        {
            "host": "lavalink.jirayu.net",
            "port": 13592,
            "password": "youshallnotpass",
            "region": "us",
            "name": "jirayu",
            "ssl": False,
        },
    ]

    # ── Accent Color: None (Components V2) ──
    ACCENT_COLOR = None

    # ── Bot Info ──
    BOT_NAME = "Echo"
    SUPPORT_SERVER = "https://discord.gg/6TRfEMJq9z"
    WEBSITE = None

    # ── Dashboard ──
    DASHBOARD_ENABLED = os.getenv("DASHBOARD_ENABLED", "true").lower() == "true"
    DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:3000")

    # ── Logging Webhooks ──
    JOIN_LOG_WEBHOOK_URL = os.getenv("JOIN_LOG_WEBHOOK_URL")
    LEAVE_LOG_WEBHOOK_URL = os.getenv("LEAVE_LOG_WEBHOOK_URL")
    MUSIC_LOG_WEBHOOK_URL = os.getenv("MUSIC_LOG_WEBHOOK_URL")
    COMMAND_LOG_WEBHOOK_URL = os.getenv("COMMAND_LOG_WEBHOOK_URL")
    ERROR_LOG_WEBHOOK_URL = os.getenv("ERROR_LOG_WEBHOOK_URL")
    DASHBOARD_LOG_WEBHOOK_URL = os.getenv("DASHBOARD_LOG_WEBHOOK_URL")

    # ── Admin Panel Security ──
    ADMIN_PASSCODE = os.getenv("ADMIN_PASSCODE", "123456")