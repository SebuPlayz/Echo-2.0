import aiohttp
import asyncio
import time
import datetime
import emojis
from config import Config

# Rate-limited Webhook Queue
_webhook_queue: asyncio.Queue = None
_webhook_worker_task = None


async def _webhook_worker():
    global _webhook_queue
    while True:
        url, payload = await _webhook_queue.get()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 429:
                        try:
                            retry_data = await resp.json()
                            retry_after = retry_data.get("retry_after", 1.2)
                        except Exception:
                            retry_after = 1.5
                        await asyncio.sleep(retry_after)
                        async with session.post(url, json=payload) as retry_resp:
                            pass
                    elif resp.status not in (200, 204):
                        print(f"[Webhook Log] HTTP error: {resp.status}")
        except Exception as e:
            print(f"[Webhook Log Error] {e}")
        finally:
            _webhook_queue.task_done()
            await asyncio.sleep(0.5)


def _ensure_worker_started():
    global _webhook_queue, _webhook_worker_task
    if _webhook_queue is None:
        _webhook_queue = asyncio.Queue()
    if _webhook_worker_task is None or _webhook_worker_task.done():
        try:
            loop = asyncio.get_running_loop()
            _webhook_worker_task = loop.create_task(_webhook_worker())
        except RuntimeError:
            pass


def format_time(ms: int) -> str:
    seconds = ms // 1000
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def progress_bar(position: int, duration: int, length: int = 18) -> str:
    if duration <= 0:
        return "-" * length
    filled = int((position / duration) * length)
    filled = max(0, min(length, filled))
    return "-" * filled + "•" + "-" * (length - filled - 1)


def get_source_emoji(uri: str) -> str:
    if not uri:
        return emojis.MUSIC
    u = uri.lower()
    if "spotify" in u:
        return emojis.SPOTIFY
    elif "apple" in u:
        return emojis.APPLE_MUSIC
    elif "soundcloud" in u:
        return emojis.SOUNDCLOUD
    elif "youtube" in u or "youtu.be" in u:
        return emojis.YOUTUBE
    return emojis.MUSIC


def truncate(text: str, max_len: int = 40) -> str:
    if len(text) > max_len:
        return text[:max_len - 3] + "..."
    return text


def build_v2_container_payload(
    title: str,
    fields: list[dict] = None,
    description: str = None,
    accent_color: int = 3066993,
    thumbnail_url: str = None,
    footer_text: str = None,
    bot_avatar: str = None,
    username: str = None
) -> dict:
    """
    Constructs a native Discord Components V2 payload (type 17 Container).
    """
    container_components = [
        # Header (TextDisplay type 9)
        {
            "type": 9,
            "content": f"### {title}"
        },
        # Top Separator (type 14)
        {
            "type": 14,
            "divider": True,
            "spacing": 1
        }
    ]

    # Body Section (type 11) with TextDisplay (type 9)
    body_parts = []
    if description:
        body_parts.append(description)

    if fields:
        for f in fields:
            name = f.get("name", "")
            val = f.get("value", "")
            if f.get("inline"):
                body_parts.append(f"**{name}:** {val}")
            else:
                body_parts.append(f"**{name}**\n{val}")

    body_text = "\n\n".join(body_parts) if body_parts else "*No additional details provided.*"

    section_comp = {
        "type": 11,
        "components": [
            {
                "type": 9,
                "content": body_text
            }
        ]
    }

    if thumbnail_url:
        section_comp["accessory"] = {
            "type": 10,
            "media": {
                "url": thumbnail_url
            }
        }

    container_components.append(section_comp)

    # Bottom Separator (type 14)
    container_components.append({
        "type": 14,
        "divider": True,
        "spacing": 1
    })

    # Footer (TextDisplay type 9) with Discord Timestamp
    ts = int(time.time())
    footer_label = footer_text or f"{getattr(Config, 'BOT_NAME', 'Echo')} System Logs"
    container_components.append({
        "type": 9,
        "content": f"-# **{footer_label}** • <t:{ts}:f> (<t:{ts}:R>)"
    })

    bot_name = getattr(Config, "BOT_NAME", "Echo") + " Logs"
    
    # Filter out empty thumbnail
    embed_obj = {
        "title": title,
        "description": description or "*No details*",
        "color": accent_color,
        "fields": fields or [],
        "footer": {"text": footer_text or f"{getattr(Config, 'BOT_NAME', 'Echo')} System Logs"}
    }
    if thumbnail_url:
        embed_obj["thumbnail"] = {"url": thumbnail_url}

    return {
        "username": username or bot_name,
        "avatar_url": bot_avatar or getattr(Config, "BOT_AVATAR", None),
        "embeds": [embed_obj]
    }


async def send_log_webhook(webhook_url: str, bot, embed_data: dict):
    if not webhook_url or not isinstance(webhook_url, str) or not webhook_url.startswith("http"):
        return

    _ensure_worker_started()

    bot_avatar = None
    if bot and bot.user:
        try:
            bot_avatar = str(bot.user.display_avatar.url)
        except Exception:
            pass

    if isinstance(embed_data, dict) and "embeds" in embed_data:
        payload = embed_data
    else:
        title = embed_data.get("title", f"{emojis.INFO} System Event")
        description = embed_data.get("description", "")
        fields = embed_data.get("fields", [])
        color = embed_data.get("color", 3066993)

        thumbnail_url = None
        if "thumbnail" in embed_data and isinstance(embed_data["thumbnail"], dict):
            thumbnail_url = embed_data["thumbnail"].get("url")

        footer_text = None
        if "footer" in embed_data and isinstance(embed_data["footer"], dict):
            footer_text = embed_data["footer"].get("text")

        payload = build_v2_container_payload(
            title=title,
            fields=fields,
            description=description,
            accent_color=color,
            thumbnail_url=thumbnail_url,
            footer_text=footer_text,
            bot_avatar=bot_avatar
        )

    if _webhook_queue is not None:
        await _webhook_queue.put((webhook_url, payload))