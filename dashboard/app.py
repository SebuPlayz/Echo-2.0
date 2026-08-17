"""
Dashboard backend. Runs in the same process as the bot, sharing
bot.db and bot.lavalink directly, so play/pause/skip/queue actions
from the website take effect immediately in the actual voice call.
"""

import os
import secrets
import asyncio
import time
import psutil
from pathlib import Path
import re
import base64
import aiohttp

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dashboard import oauth
from config import Config
import emojis

async def resolve_spotify_url(url: str, client_id: str = None, client_secret: str = None) -> list[dict] | None:
    """
    Given a Spotify playlist/album/track URL, resolves it by scraping the Embed page.
    Bypasses the client premium requirement by parsing Next.js __NEXT_DATA__ props.
    """
    match = re.search(r'spotify\.com/(playlist|album|track)/([a-zA-Z0-9]+)', url)
    if not match:
        return None

    spotify_type, spotify_id = match.groups()
    embed_url = f"https://open.spotify.com/embed/{spotify_type}/{spotify_id}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        try:
            async with session.get(embed_url, headers=headers, timeout=10.0) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
                
                json_scripts = re.findall(r'<script[^>]*type="application/json"[^>]*>([\s\S]*?)</script>', html)
                for js in json_scripts:
                    try:
                        data = json.loads(js.strip())
                        if "props" in data:
                            page_props = data.get("props", {}).get("pageProps", {})
                            if page_props.get("status") == 404:
                                continue
                            state = page_props.get("state", {})
                            data_val = state.get("data", {})
                            entity = data_val.get("entity", {})
                            
                            tracks = []
                            # Single track
                            if entity.get("type") == "track":
                                track_id = entity.get("id") or spotify_id
                                tracks.append({
                                    "title": entity.get("title", entity.get("name")),
                                    "author": entity.get("subtitle", ""),
                                    "uri": f"https://open.spotify.com/track/{track_id}",
                                    "identifier": track_id
                                })
                                return tracks
                            
                            # Playlist / Album
                            track_list = entity.get("trackList", [])
                            if track_list:
                                for t in track_list:
                                    track_uri = t.get("uri", "")
                                    track_id = track_uri.split(":")[-1] if ":" in track_uri else t.get("uid", "")
                                    tracks.append({
                                        "title": t.get("title", ""),
                                        "author": t.get("subtitle", "").replace("\xa0", " "),
                                        "uri": f"https://open.spotify.com/track/{track_id}",
                                        "identifier": track_id
                                    })
                                return tracks
                    except Exception as e:
                        print(f"[SpotifyResolver] Inner parsing exception: {e}")
                        continue
        except Exception as e:
            print(f"[SpotifyResolver] Request/parsing outer exception: {e}")
            import traceback
            traceback.print_exc()
            
    return None

BASE_DIR = Path(__file__).parent
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


async def _resolve_track(sem, player, track_title: str, track_uri: str, stagger: float = 0.0):
    if stagger > 0:
        await asyncio.sleep(stagger)
    async with sem:
        search_query = track_uri if track_uri else f"ytsearch:{track_title}"
        for attempt in range(2):
            try:
                results = await player.node.get_tracks(search_query)
                if (not results or not results.tracks) and ("spotify.com/" in search_query or "spotify:" in search_query):
                    fallback_query = f"ytsearch:{track_title}"
                    results = await player.node.get_tracks(fallback_query)
                if results and results.tracks:
                    return results.tracks[0]
            except Exception as e:
                print(f"[DashboardResolve] Attempt {attempt+1} failed for '{track_title}': {e}")
            if attempt == 0:
                await asyncio.sleep(0.25)
        return None


def create_dashboard(bot) -> FastAPI:
    app = FastAPI(title="Echo Dashboard", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/") or request.url.path.startswith("/ws/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

        title = "Access Restricted" if exc.status_code in (401, 403) else ("Page Not Found" if exc.status_code == 404 else "Dashboard Error")
        icon_svg = '<svg viewBox="0 0 24 24" width="44" height="44" fill="none" stroke="#ef4444" stroke-width="2.2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>' if exc.status_code in (401, 403) else '<svg viewBox="0 0 24 24" width="44" height="44" fill="none" stroke="#f59e0b" stroke-width="2.2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{exc.status_code} - {title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #09090b;
      --card-bg: #121215;
      --line: rgba(255, 255, 255, 0.08);
      --text-hi: #ffffff;
      --text-mid: rgba(255, 255, 255, 0.65);
      --accent: #6366f1;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Plus Jakarta Sans', sans-serif;
      background: var(--bg);
      color: var(--text-hi);
      height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      padding: 20px;
    }}
    .petal-field {{
      position: absolute;
      inset: 0;
      pointer-events: none;
      background: radial-gradient(circle at 50% 40%, rgba(99, 102, 241, 0.12) 0%, transparent 65%);
    }}
    .error-card {{
      position: relative;
      z-index: 10;
      background: var(--card-bg);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 42px 36px;
      max-width: 440px;
      width: 100%;
      text-align: center;
      box-shadow: 0 25px 50px -12px rgba(0,0,0,0.7);
      backdrop-filter: blur(16px);
    }}
    .icon-box {{
      width: 76px;
      height: 76px;
      border-radius: 20px;
      background: rgba(239, 68, 68, 0.08);
      border: 1px solid rgba(239, 68, 68, 0.2);
      display: flex;
      align-items: center;
      justify-content: center;
      margin: 0 auto 22px;
    }}
    .status-code {{
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: rgba(255, 255, 255, 0.4);
      margin-bottom: 8px;
    }}
    h1 {{
      font-size: 22px;
      font-weight: 800;
      color: var(--text-hi);
      margin-bottom: 10px;
    }}
    p {{
      font-size: 14px;
      color: var(--text-mid);
      line-height: 1.6;
      margin-bottom: 28px;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      background: var(--accent);
      color: #fff;
      font-weight: 700;
      font-size: 14px;
      padding: 12px 26px;
      border-radius: 12px;
      text-decoration: none;
      transition: all 0.2s ease;
    }}
    .btn:hover {{
      transform: translateY(-2px);
      box-shadow: 0 10px 25px -5px rgba(99, 102, 241, 0.4);
    }}
  </style>
</head>
<body>
  <div class="petal-field"></div>
  <div class="error-card">
    <div class="icon-box">
      {icon_svg}
    </div>
    <div class="status-code">ERROR {exc.status_code}</div>
    <h1>{title}</h1>
    <p>{exc.detail}</p>
    <a href="/dashboard" class="btn">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
      Return to Dashboard
    </a>
  </div>
</body>
</html>"""
        return HTMLResponse(content=html_content, status_code=exc.status_code)

    # Track connected dashboard clients per guild so we can push live
    # player updates (song changed, paused, queue updated, etc.)
    app.state.ws_clients: dict[int, set[WebSocket]] = {}
    app.state.oauth_states: set[str] = set()
    # Cache of {discord_user_id: {"guild_ids": set(...), "fetched_at": float}}
    # populated at login so /api/guilds doesn't re-hit Discord's API
    # on every page load.
    app.state.guild_cache: dict[str, dict] = {}

    from utils.helpers import send_log_webhook
    import datetime

    async def log_dashboard_action(user_id: int, username: str, title: str, description: str, fields: list = None, color: int = 16096779):
        try:
            # Save audit action to SQLite database for web panel Audit Action History
            if hasattr(bot, "db") and bot.db:
                try:
                    await bot.db.log_audit_action(
                        user_id=user_id,
                        username=username or "Owner",
                        title=title,
                        description=description,
                        color=color
                    )
                except Exception as db_err:
                    print(f"[Dashboard Audit Log DB Error] {db_err}")

            avatar_url = oauth.avatar_url(str(user_id), None)
            all_fields = fields or []
            if username or user_id:
                all_fields.insert(0, {"name": "👤 Admin / User", "value": f"**{username}** (`{user_id}`)"})

            embed = {
                "title": f"🌐 {title}",
                "description": description,
                "fields": all_fields,
                "color": color,
                "thumbnail": {"url": avatar_url},
                "footer": {"text": f"Echo Dashboard Monitor • ID: {user_id}"}
            }
            bot.loop.create_task(send_log_webhook(Config.DASHBOARD_LOG_WEBHOOK_URL, bot, embed))
        except Exception as e:
            print(f"[Dashboard Log] Error sending action log: {e}")

    # ── Helpers ──────────────────────────────────────────────────

    def get_session(request: Request) -> dict | None:
        token = request.cookies.get("Echo_session")
        if not token:
            return None
        return oauth.read_session_token(token)

    def require_session(request: Request) -> dict | None:
        session = get_session(request)
        return session

    import hmac, hashlib
    app.state.failed_pin_attempts = {}

    def get_signed_pin_hash(pin: str) -> str:
        secret = str(getattr(Config, "DASHBOARD_SESSION_SECRET", "secret_key_12345")).encode()
        return hmac.new(secret, pin.encode(), hashlib.sha256).hexdigest()

    def is_admin_pin_verified(request: Request) -> bool:
        cookie_hash = request.cookies.get("Echo_admin_pin")
        expected_pin = str(getattr(Config, "ADMIN_PASSCODE", "123456")).strip()
        if not expected_pin:
            return True
        if not cookie_hash:
            return False
        expected_hash = get_signed_pin_hash(expected_pin)
        return hmac.compare_digest(cookie_hash, expected_hash)

    async def is_maintenance_active() -> bool:
        if hasattr(bot, "maintenance_mode") and bot.maintenance_mode:
            return True
        if hasattr(bot, "db") and bot.db:
            try:
                val = await bot.db.get_bot_setting("maintenance_mode", "0")
                return val == "1"
            except Exception:
                pass
        return False

    async def check_maintenance_redirect(request: Request):
        if await is_maintenance_active():
            session = get_session(request)
            if session and _is_owner(session):
                return None
            return templates.TemplateResponse(
                request=request,
                name="maintenance.html",
                context={
                    "bot_name": getattr(bot, "user", None) and bot.user.name or "Echo",
                    "bot_avatar": _bot_avatar_url(),
                    "support_server": getattr(Config, "SUPPORT_SERVER", "")
                },
                status_code=530
            )
        return None

    async def member_permission_level(guild, user_id: int) -> str | None:
        """Returns 'owner' | 'manager' | 'member' | None (not in guild)."""
        if user_id in bot.owner_ids or (hasattr(guild, "owner_id") and guild.owner_id == user_id):
            return "owner"

        member = guild.get_member(user_id) if hasattr(guild, "get_member") else None
        if member is None and hasattr(guild, "fetch_member"):
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                pass

        if member:
            if getattr(getattr(member, "guild_permissions", None), "manage_guild", False):
                return "manager"
            return "member"

        return None

    def _track_thumbnail(track) -> str | None:
        """Resolve a thumbnail the same way the Discord embed does,
        so both stay in sync. Falls back locally if the cog isn't loaded."""
        if not track:
            return None
        music_cog = bot.get_cog("Music")
        if music_cog and hasattr(music_cog, "_get_thumbnail"):
            try:
                thumb = music_cog._get_thumbnail(track)
                if thumb:
                    return thumb
            except Exception:
                pass
        artwork = getattr(track, "artwork_url", None)
        if artwork:
            return artwork
        try:
            source = (getattr(track, "source_name", "") or "").lower()
            uri = (track.uri or "").lower()
            if "youtube" in source or "youtube" in uri or "youtu.be" in uri:
                return f"https://img.youtube.com/vi/{track.identifier}/hqdefault.jpg"
        except Exception:
            pass
        return None

    def player_to_dict(guild_id: int) -> dict:
        """Serialize the current lavalink player state for JSON/WebSocket."""
        lavalink = getattr(bot, "lavalink", None)
        if not lavalink:
            return {"connected": False}
        player = lavalink.player_manager.get(guild_id)
        if not player or not player.is_connected:
            return {"connected": False}

        music_cog = bot.get_cog("Music")
        current = player.current
        queue = []
        for i, track in enumerate(player.queue[:25]):
            queue.append({
                "index": i,
                "title": track.title,
                "author": track.author,
                "duration": track.duration,
                "uri": track.uri,
                "requester": track.requester,
                "thumbnail": _track_thumbnail(track),
            })

        return {
            "connected": True,
            "paused": player.paused,
            "volume": player.volume,
            "loop": bool(getattr(player, "loop", False)),
            "active_filter": player.fetch("active_filter"),
            "autoplay": bool(music_cog.autoplay_states.get(guild_id, False)) if music_cog else False,
            "position": player.position if current else 0,
            "current": {
                "title": current.title,
                "author": current.author,
                "duration": current.duration,
                "uri": current.uri,
                "requester": current.requester,
                "identifier": current.identifier,
                "thumbnail": _track_thumbnail(current),
            } if current else None,
            "queue": queue,
            "queue_length": len(player.queue),
        }

    async def broadcast_player_update(guild_id: int):
        clients = app.state.ws_clients.get(guild_id)
        if not clients:
            return
        payload = {"type": "player_update", "data": player_to_dict(guild_id)}
        dead = []
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)

    # Expose the broadcaster so cogs/music.py can call it after
    # play/pause/skip/etc so the dashboard updates in real time
    # without the browser needing to poll.
    bot.dashboard_broadcast = broadcast_player_update

    # ── Auth routes ──────────────────────────────────────────────

    @app.get("/login")
    @app.get("/auth/login")
    async def auth_login():
        state = secrets.token_urlsafe(24)
        app.state.oauth_states.add(state)
        return RedirectResponse(oauth.build_authorize_url(state))

    @app.get("/auth/callback")
    @app.get("//auth/callback")
    async def auth_callback(request: Request, code: str = None, state: str = None, error: str = None):
        if error or not code:
            return RedirectResponse("/?error=login_failed")

        if state not in app.state.oauth_states:
            return RedirectResponse("/?error=invalid_state")
        app.state.oauth_states.discard(state)

        token_data = await oauth.exchange_code(code)
        if not token_data:
            return RedirectResponse("/?error=token_exchange_failed")

        user = await oauth.fetch_user(token_data["access_token"])
        if not user:
            return RedirectResponse("/?error=user_fetch_failed")

        session_token = oauth.create_session_token(user)

        # Log Dashboard Login
        await log_dashboard_action(
            user_id=int(user["id"]),
            username=user["username"],
            title="🌐 Dashboard Login",
            description=(
                f"👤 **User:** **{user['username']}**\n"
                f"🆔 **Discord ID:** `{user['id']}`\n\n"
                f"User successfully authorized and logged in to the web panel."
            ),
            color=0x2ecc71
        )

        # Cache the user's guild list briefly so /api/guilds doesn't
        # need to re-hit Discord's API on every page load.
        guilds = await oauth.fetch_user_guilds(token_data["access_token"])
        app.state.guild_cache[user["id"]] = {
            "guilds": guilds,
            "fetched_at": asyncio.get_event_loop().time(),
        }

        resp = RedirectResponse("/dashboard")
        resp.set_cookie(
            "Echo_session", session_token,
            max_age=oauth.SESSION_MAX_AGE, httponly=True, samesite="lax"
        )
        return resp

    @app.get("/logout")
    @app.get("/auth/logout")
    async def auth_logout():
        resp = RedirectResponse("/")
        resp.delete_cookie("Echo_session")
        return resp

    # ── Page routes ──────────────────────────────────────────────

    def _bot_avatar_url() -> str:
        """Return the bot's Discord CDN avatar URL, or fallback to static."""
        user = getattr(bot, "user", None)
        if user and user.avatar:
            return str(user.avatar.url)
        if user and user.id:
            return f"https://cdn.discordapp.com/embed/avatars/0.png"
        return "/static/Echo-avatar.png"

    def _is_owner(session: dict) -> bool:
        if not session or "id" not in session:
            return False
        try:
            return int(session["id"]) in Config.OWNER_IDS
        except Exception:
            return False

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        session = get_session(request)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "session": session,
                "bot_name": getattr(bot, "user", None) and bot.user.name or "Echo",
                "bot_id": getattr(bot, "user", None) and bot.user.id or None,
                "bot_avatar": _bot_avatar_url(),
                "guild_count": len(bot.guilds),
                "support_server": Config.SUPPORT_SERVER,
                "is_owner": _is_owner(session) if session else False,
            }
        )

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_home(request: Request):
        m_resp = await check_maintenance_redirect(request)
        if m_resp: return m_resp
        session = require_session(request)
        if not session:
            return RedirectResponse("/?login_required=1")
        return templates.TemplateResponse(
            request=request,
            name="guilds.html",
            context={
                "session": session,
                "avatar": oauth.avatar_url(session["id"], session.get("avatar")),
                "bot_avatar": _bot_avatar_url(),
                "bot_name": getattr(bot, "user", None) and bot.user.name or "Echo",
                "is_owner": _is_owner(session),
            }
        )

    @app.get("/dashboard/{guild_id}", response_class=HTMLResponse)
    async def dashboard_guild(request: Request, guild_id: int):
        m_resp = await check_maintenance_redirect(request)
        if m_resp: return m_resp
        session = require_session(request)
        if not session:
            return RedirectResponse("/?login_required=1")
        guild = bot.get_guild(guild_id)
        if not guild:
            try:
                guild = await bot.fetch_guild(guild_id)
            except Exception:
                raise HTTPException(status_code=404, detail="Bot is not in that server")

        level = await member_permission_level(guild, int(session["id"]))
        if level is None:
            raise HTTPException(status_code=403, detail="You are not a member of that server")

        settings = await bot.db.get_guild_settings(guild_id)

        roles_list = []
        vc_channels = []
        tc_channels = []

        try:
            raw_roles = await bot.http.get_roles(guild_id)
            for r in raw_roles:
                r_id = str(r.get("id"))
                if r_id != str(guild_id) and not r.get("managed", False):
                    roles_list.append({"id": r_id, "name": r.get("name")})
        except Exception as e:
            print(f"[Dashboard Guild] Error fetching HTTP roles: {e}")

        try:
            raw_channels = await bot.http.get_all_guild_channels(guild_id)
            for c in raw_channels:
                c_id = str(c.get("id"))
                c_name = str(c.get("name"))
                c_type = int(c.get("type", 0))
                if c_type in (2, 13):
                    vc_channels.append({"id": c_id, "name": c_name})
                elif c_type in (0, 5, 15):
                    tc_channels.append({"id": c_id, "name": c_name})
        except Exception as e:
            print(f"[Dashboard Guild] Error fetching HTTP channels: {e}")

        if not roles_list and hasattr(guild, "roles") and guild.roles:
            for r in guild.roles:
                if not r.is_default() and not getattr(r, "managed", False):
                    roles_list.append({"id": str(r.id), "name": r.name})

        if not vc_channels and hasattr(guild, "voice_channels") and guild.voice_channels:
            vc_channels = [{"id": str(c.id), "name": c.name} for c in guild.voice_channels]

        if not tc_channels and hasattr(guild, "text_channels") and guild.text_channels:
            tc_channels = [{"id": str(c.id), "name": c.name} for c in guild.text_channels]

        return templates.TemplateResponse(
            request=request,
            name="player.html",
            context={
                "session": session,
                "avatar": oauth.avatar_url(session["id"], session.get("avatar")),
                "bot_avatar": _bot_avatar_url(),
                "bot_name": getattr(bot, "user", None) and bot.user.name or "Echo",
                "guild": {
                    "id": str(guild.id),
                    "name": guild.name,
                    "icon": guild.icon.url if guild.icon else None,
                    "member_count": getattr(guild, "member_count", None) or getattr(guild, "approximate_member_count", None) or len(getattr(guild, "members", [])),
                },
                "permission_level": level,
                "is_owner": _is_owner(session),
                "guild_settings": settings,
                "roles": roles_list,
                "vc_channels": vc_channels,
                "tc_channels": tc_channels,
            }
        )

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_dashboard_page(request: Request):
        session = require_session(request)
        if not session or not _is_owner(session):
            raise HTTPException(status_code=403, detail="Owner access required")

        if not is_admin_pin_verified(request):
            return templates.TemplateResponse(
                request=request,
                name="admin_lock.html",
                context={
                    "session": session,
                    "avatar": oauth.avatar_url(session["id"], session.get("avatar")),
                    "bot_avatar": _bot_avatar_url(),
                    "bot_name": getattr(bot, "user", None) and bot.user.name or "Echo",
                }
            )

        return templates.TemplateResponse(
            request=request,
            name="admin.html",
            context={
                "session": session,
                "avatar": oauth.avatar_url(session["id"], session.get("avatar")),
                "bot_avatar": _bot_avatar_url(),
                "bot_name": getattr(bot, "user", None) and bot.user.name or "Echo",
                "is_owner": True,
            }
        )

    @app.post("/api/admin/verify-pin")
    async def api_admin_verify_pin(request: Request):
        session = require_session(request)
        if not session or not _is_owner(session):
            raise HTTPException(status_code=403, detail="Owner access required")

        client_ip = request.client.host if request.client else "Unknown IP"
        now = time.time()

        # Check IP Anti-Bruteforce Rate Limit (5 failed attempts within 15 mins)
        attempts = [t for t in app.state.failed_pin_attempts.get(client_ip, []) if now - t < 900]
        app.state.failed_pin_attempts[client_ip] = attempts

        if len(attempts) >= 5:
            asyncio.create_task(log_dashboard_action(
                user_id=session["id"],
                username=session.get("username"),
                title="🚨 IP Blocked: Failed Admin PIN Bruteforce",
                description=f"Client IP `{client_ip}` was temporarily banned for 15 minutes due to 5 consecutive invalid PIN attempts.",
                color=0xEF4444
            ))
            return JSONResponse({
                "success": False,
                "message": "🚫 Too many invalid attempts! IP temporarily locked out for 15 minutes."
            }, status_code=429)

        try:
            body = await request.json()
        except Exception:
            body = {}
        pin = str(body.get("pin", "")).strip()
        expected_pin = str(getattr(Config, "ADMIN_PASSCODE", "123456")).strip()

        if pin == expected_pin:
            # Clear failed attempts on successful login
            app.state.failed_pin_attempts.pop(client_ip, None)
            signed_hash = get_signed_pin_hash(pin)
            response = JSONResponse({"success": True, "message": "Admin Security PIN verified successfully."})
            response.set_cookie(key="Echo_admin_pin", value=signed_hash, max_age=86400, httponly=True, samesite="strict")
            
            asyncio.create_task(log_dashboard_action(
                user_id=session["id"],
                username=session.get("username"),
                title="🔓 Admin Panel Unlocked",
                description=f"Admin panel successfully unlocked by **{session.get('username')}** from IP `{client_ip}`.",
                color=0x10B981
            ))
            return response
        else:
            app.state.failed_pin_attempts.setdefault(client_ip, []).append(now)
            remaining = 5 - len(app.state.failed_pin_attempts[client_ip])

            asyncio.create_task(log_dashboard_action(
                user_id=session["id"],
                username=session.get("username"),
                title="⚠️ Invalid Admin PIN Attempt",
                description=f"Failed PIN attempt from IP `{client_ip}` for user **{session.get('username')}** ({remaining} attempts left).",
                color=0xF59E0B
            ))
            return JSONResponse({
                "success": False,
                "message": f"Invalid Admin PIN! ({remaining} attempts remaining before IP lockout)"
            }, status_code=400)

    @app.get("/status", response_class=HTMLResponse)
    async def status_page(request: Request):
        session = get_session(request)
        return templates.TemplateResponse(
            request=request,
            name="status.html",
            context={
                "session": session,
                "bot_name": getattr(bot, "user", None) and bot.user.name or "Echo",
                "bot_avatar": _bot_avatar_url(),
                "is_owner": _is_owner(session) if session else False,
            }
        )

    @app.get("/api/status")
    async def api_status():
        import time, platform

        # ── Bot info ────────────────────────────────────────
        bot_user = getattr(bot, "user", None)
        bot_latency_ms = round(bot.latency * 1000, 1) if bot.latency and bot.latency != float("inf") else None

        # Uptime
        start_time = getattr(bot, "start_time", None)
        uptime_sec = int(time.time() - start_time) if start_time else None
        def fmt_uptime(s):
            if s is None: return "Unknown"
            d, rem = divmod(s, 86400)
            h, rem = divmod(rem, 3600)
            m, _ = divmod(rem, 60)
            parts = []
            if d: parts.append(f"{d}d")
            if h: parts.append(f"{h}h")
            parts.append(f"{m}m")
            return " ".join(parts)

        # ── Lavalink nodes ──────────────────────────────────
        lavalink = getattr(bot, "lavalink", None)
        nodes_data = []
        if lavalink:
            for node in lavalink.node_manager.nodes:
                node_stats = getattr(node, "stats", None)
                nodes_data.append({
                    "name": node.name,
                    "host": f"{node._transport._host}:{node._transport._port}" if hasattr(node, "_transport") else "Unknown",
                    "connected": node.available,
                    "ssl": getattr(node._transport, "_ssl", False) if hasattr(node, "_transport") else False,
                    "players": getattr(node_stats, "playing_players", 0) if node_stats else 0,
                    "cpu": round(getattr(node_stats, "cpu", {}).get("system_load", 0) * 100, 1) if node_stats and hasattr(node_stats, "cpu") and node_stats.cpu else None,
                    "memory_used": getattr(node_stats, "memory", {}).get("used", None) if node_stats and hasattr(node_stats, "memory") and node_stats.memory else None,
                })

        nodes_up = sum(1 for n in nodes_data if n["connected"])
        nodes_total = len(nodes_data)

        return {
            "bot": {
                "online": bot_user is not None,
                "name": bot_user.name if bot_user else "Echo",
                "id": str(bot_user.id) if bot_user else None,
                "avatar": _bot_avatar_url(),
                "guilds": len(bot.guilds),
                "users": sum(g.member_count or 0 for g in bot.guilds),
                "latency_ms": bot_latency_ms,
                "uptime": fmt_uptime(uptime_sec),
                "uptime_sec": uptime_sec,
                "python": platform.python_version(),
            },
            "lavalink": {
                "nodes": nodes_data,
                "up": nodes_up,
                "total": nodes_total,
                "status": "operational" if nodes_up == nodes_total and nodes_total > 0 else (
                    "degraded" if nodes_up > 0 else "down"
                ),
            },
            "dashboard": {
                "status": "operational",
                "port": DASHBOARD_PORT,
            },
            "timestamp": int(time.time()),
        }

    # ── JSON API ─────────────────────────────────────────────────

    @app.get("/api/me")
    async def api_me(request: Request):
        session = require_session(request)
        return {
            "id": session["id"],
            "username": session["username"],
            "avatar": oauth.avatar_url(session["id"], session.get("avatar")),
            "is_owner": _is_owner(session),
        }

    # ── Admin Panel API ─────────────────────────────────────────

    @app.get("/api/admin/stats")
    async def api_admin_stats(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")
        if not is_admin_pin_verified(request):
            raise HTTPException(status_code=401, detail="Admin Security PIN required")

        lavalink = getattr(bot, "lavalink", None)
        active_players = 0
        paused_players = 0
        total_voice_connected = 0

        for g in bot.guilds:
            if g.voice_client and g.voice_client.channel:
                total_voice_connected += 1
            if lavalink:
                player = lavalink.player_manager.get(g.id)
                if player:
                    if player.current:
                        if player.paused:
                            paused_players += 1
                        else:
                            active_players += 1
                    elif getattr(player, "is_playing", False):
                        active_players += 1

        import time, psutil, math

        def _clean_num(v, default=0.0):
            if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                return default
            return v

        process = psutil.Process()
        mem_info = process.memory_info()
        memory_mb = _clean_num(round(mem_info.rss / 1024 / 1024, 1))
        cpu_pct = _clean_num(round(psutil.cpu_percent(), 1))
        raw_lat = bot.latency * 1000 if (hasattr(bot, "latency") and bot.latency is not None) else 0.0
        lat_ms = _clean_num(round(raw_lat, 1))

        total_plays = 0
        try:
            total_plays = await bot.db.get_total_song_plays()
        except Exception as e:
            print(f"[AdminTelemetry] Error getting total plays: {e}")

        total_playlists = 0
        try:
            total_playlists = await bot.db.get_total_playlists_count()
        except Exception as e:
            print(f"[AdminTelemetry] Error getting total playlists: {e}")

        total_guild_cnt = len(bot.guilds)
        total_user_cnt = sum(g.member_count or 0 for g in bot.guilds)

        # Record telemetry history point
        if not hasattr(app.state, "telemetry_history"):
            app.state.telemetry_history = []
        
        now_str = time.strftime("%H:%M:%S")
        app.state.telemetry_history.append({
            "time": now_str,
            "cpu": cpu_pct,
            "memory": memory_mb,
            "latency": lat_ms,
            "active_streams": active_players,
            "voice_connections": total_voice_connected,
            "guilds": total_guild_cnt,
            "users": total_user_cnt
        })
        if len(app.state.telemetry_history) > 30:
            app.state.telemetry_history.pop(0)

        return {
            "ok": True,
            "total_guilds": total_guild_cnt,
            "total_users": total_user_cnt,
            "voice_connections": total_voice_connected,
            "active_players": active_players,
            "paused_players": paused_players,
            "memory_mb": memory_mb,
            "cpu_percent": cpu_pct,
            "total_plays": total_plays,
            "total_playlists": total_playlists,
            "uptime_sec": int(time.time() - bot.start_time) if hasattr(bot, "start_time") and bot.start_time else 0,
            "latency_ms": lat_ms,
            "telemetry_history": app.state.telemetry_history
        }

    @app.get("/api/admin/streams")
    async def api_admin_streams(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        lavalink = getattr(bot, "lavalink", None)
        streams = []
        for g in bot.guilds:
            player = lavalink.player_manager.get(g.id) if lavalink else None
            if player and player.current:
                vc = g.voice_client
                vc_name = vc.channel.name if (vc and vc.channel) else "Voice Channel"
                streams.append({
                    "guild_id": str(g.id),
                    "guild_name": g.name,
                    "guild_icon": g.icon.url if g.icon else None,
                    "channel_name": vc_name,
                    "title": player.current.title,
                    "author": player.current.author,
                    "uri": player.current.uri,
                    "artwork_url": getattr(player.current, "artwork_url", None),
                    "duration": getattr(player.current, "duration", 0),
                    "position": getattr(player, "position", 0),
                    "is_paused": getattr(player, "paused", False),
                    "volume": getattr(player, "volume", 100),
                    "queue_length": len(player.queue) if hasattr(player, "queue") else 0
                })

        return {"ok": True, "streams": streams, "count": len(streams)}

    @app.get("/api/admin/guilds")
    async def api_admin_guilds(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        lavalink = getattr(bot, "lavalink", None)

        guild_list = []
        for g in bot.guilds:
            vc = g.voice_client
            vc_channel = vc.channel if vc else None
            player = lavalink.player_manager.get(g.id) if lavalink else None
            
            is_247 = await bot.db.get_247(g.id)
            is_playing = bool(player and player.current and not player.paused)
            if not is_playing and player and getattr(player, "is_playing", False):
                is_playing = True

            is_paused = bool(player and player.current and player.paused)

            current_track_dict = None
            if player and player.current:
                current_track_dict = {
                    "title": player.current.title,
                    "author": player.current.author,
                    "uri": player.current.uri,
                    "duration": getattr(player.current, "duration", 0),
                    "artwork_url": getattr(player.current, "artwork_url", None)
                }

            guild_list.append({
                "id": str(g.id),
                "name": g.name,
                "icon": g.icon.url if g.icon else None,
                "member_count": g.member_count or 0,
                "owner_id": str(g.owner_id) if g.owner_id else None,
                "owner_name": str(g.owner) if g.owner else "Unknown",
                "voice_connected": vc_channel is not None or bool(player and getattr(player, "is_connected", False)),
                "voice_channel_name": vc_channel.name if vc_channel else None,
                "voice_channel_id": str(vc_channel.id) if vc_channel else None,
                "is_playing": is_playing,
                "is_paused": is_paused,
                "current_track": player.current.title if (player and player.current) else None,
                "track_info": current_track_dict,
                "is_247": is_247,
                "joined_at": g.me.joined_at.strftime("%Y-%m-%d") if g.me and g.me.joined_at else "Unknown"
            })

        guild_list.sort(key=lambda x: (not x["is_playing"], not x["voice_connected"], -x["member_count"]))
        return {"ok": True, "guilds": guild_list}

    @app.post("/api/admin/guilds/{guild_id}/toggle-247")
    async def api_admin_toggle_247(request: Request, guild_id: int):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        guild = bot.get_guild(guild_id)
        if not guild:
            raise HTTPException(status_code=404, detail="Guild not found")

        current_247 = await bot.db.get_247(guild_id)
        new_state = not current_247
        
        vc_channel_id = None
        if new_state:
            if guild.voice_client and guild.voice_client.channel:
                vc_channel_id = guild.voice_client.channel.id
            elif guild.me and guild.me.voice and guild.me.voice.channel:
                vc_channel_id = guild.me.voice.channel.id

        await bot.db.set_247(guild_id, new_state, vc_channel_id)
        
        await log_dashboard_action(
            user_id=int(session["id"]),
            username=session.get("username", "Owner"),
            title="⚡ 24/7 Mode Toggled (Admin Panel)",
            description=f"Bot owner {'enabled' if new_state else 'disabled'} 24/7 mode for **{guild.name}** (`{guild_id}`).",
            color=0x9b59b6 if new_state else 0x95a5a6
        )
        return {"ok": True, "is_247": new_state, "message": f"24/7 Mode {'enabled' if new_state else 'disabled'} for '{guild.name}'"}

    @app.post("/api/admin/guilds/{guild_id}/leave")
    async def api_admin_leave_guild(request: Request, guild_id: int):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        guild = bot.get_guild(guild_id)
        if not guild:
            raise HTTPException(status_code=404, detail="Guild not found")

        guild_name = guild.name
        try:
            if guild.voice_client:
                try:
                    await guild.voice_client.disconnect(force=True)
                except Exception:
                    pass

            await guild.leave()
            
            await log_dashboard_action(
                user_id=int(session["id"]),
                username=session.get("username", "Owner"),
                title="🚪 Bot Left Guild (Admin Panel)",
                description=(
                    f"**Server Name:** **{guild_name}**\n"
                    f"**Server ID:** `{guild_id}`\n\n"
                    f"Bot owner left the server via Admin Dashboard."
                ),
                color=0xe74c3c
            )
            return {"ok": True, "message": f"Successfully left server '{guild_name}'"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to leave server: {e}")

    # ── Admin NoPrefix Management API ───────────────────────────

    @app.get("/api/admin/noprefix")
    async def api_admin_get_noprefix(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        try:
            rows = await bot.db.list_noprefix()
            user_list = []
            now = int(time.time())

            for row in rows:
                try:
                    user_id, added_by, added_at, expires_at = row
                    user_id_str = str(user_id)
                    uid_int = int(user_id_str)
                    
                    is_expired = False
                    time_left_str = "Lifetime"
                    if expires_at and str(expires_at).isdigit() and int(expires_at) > 0:
                        exp_int = int(expires_at)
                        if exp_int < now:
                            is_expired = True
                            time_left_str = "Expired"
                        else:
                            diff = exp_int - now
                            days = diff // 86400
                            hours = (diff % 86400) // 3600
                            mins = (diff % 3600) // 60
                            if days > 0:
                                time_left_str = f"{days}d {hours}h left"
                            elif hours > 0:
                                time_left_str = f"{hours}h {mins}m left"
                            else:
                                time_left_str = f"{mins}m left"

                    # Synchronous cache lookup first
                    user = bot.get_user(uid_int)
                    if not user and hasattr(bot, "fetch_user") and getattr(bot, "is_ready", lambda: False)():
                        try:
                            user = await asyncio.wait_for(bot.fetch_user(uid_int), timeout=1.0)
                        except Exception:
                            user = None

                    user_name = str(user) if user else f"User {user_id}"
                    avatar_url = None
                    if user and hasattr(user, "display_avatar") and user.display_avatar:
                        avatar_url = str(user.display_avatar.url)

                    # Added By User lookup
                    added_by_name = f"ID: {added_by}" if added_by else "System"
                    if added_by:
                        try:
                            ab_int = int(added_by)
                            added_by_user = bot.get_user(ab_int)
                            if not added_by_user and hasattr(bot, "fetch_user") and getattr(bot, "is_ready", lambda: False)():
                                try:
                                    added_by_user = await asyncio.wait_for(bot.fetch_user(ab_int), timeout=1.0)
                                except Exception:
                                    added_by_user = None
                            if added_by_user:
                                added_by_name = str(added_by_user)
                        except Exception:
                            pass

                    added_at_str = "Unknown"
                    if added_at:
                        try:
                            added_at_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(added_at)))
                        except Exception:
                            pass

                    user_list.append({
                        "user_id": user_id_str,
                        "username": user_name,
                        "avatar": avatar_url,
                        "added_by": str(added_by) if added_by else "",
                        "added_by_name": added_by_name,
                        "added_at": added_at_str,
                        "expires_at": expires_at,
                        "is_expired": is_expired,
                        "time_left_str": time_left_str
                    })
                except Exception as row_err:
                    print(f"[NoPrefix API] Error parsing row {row}: {row_err}")

            return {"ok": True, "noprefix_users": user_list, "count": len(user_list)}
        except Exception as err:
            import traceback
            print(f"[NoPrefix API Fatal Error] {err}")
            traceback.print_exc()
            return {"ok": False, "error": str(err), "noprefix_users": [], "count": 0}

    @app.post("/api/admin/noprefix")
    async def api_admin_add_noprefix(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        body = await request.json()
        target_user_id_str = str(body.get("user_id", "")).strip()
        if not target_user_id_str.isdigit():
            raise HTTPException(status_code=400, detail="Invalid User ID. Must be numeric Discord User ID.")

        target_user_id = int(target_user_id_str)
        duration_input = str(body.get("duration", "lifetime")).strip().lower()

        now = int(time.time())
        expires_at = None

        if duration_input in ("lifetime", "never", "none", "0"):
            expires_at = None
        elif duration_input == "1h":
            expires_at = now + 3600
        elif duration_input == "1d":
            expires_at = now + 86400
        elif duration_input == "7d":
            expires_at = now + (86400 * 7)
        elif duration_input == "30d":
            expires_at = now + (86400 * 30)
        elif duration_input.isdigit():
            val = int(duration_input)
            if val > 1000000000:
                expires_at = val
            else:
                expires_at = now + val

        await bot.db.add_noprefix(target_user_id, int(session["id"]), expires_at)

        target_user = bot.get_user(target_user_id)
        target_name = str(target_user) if target_user else f"User {target_user_id}"

        # Dispatch DM notification to user using Component V2
        try:
            from cogs.noprefix import send_noprefix_grant_dm
            await send_noprefix_grant_dm(bot, target_user_id, int(session["id"]), expires_at)
        except Exception as e:
            print(f"[Admin Panel NoPrefix DM] Could not send grant DM: {e}")

        await log_dashboard_action(
            user_id=int(session["id"]),
            username=session.get("username", "Owner"),
            title="👑 NoPrefix Granted (Admin Panel)",
            description=f"Owner granted NoPrefix access to **{target_name}** (`{target_user_id}`).",
            color=0xf1c40f
        )

        return {"ok": True, "message": f"Successfully granted NoPrefix to {target_name}"}

    @app.delete("/api/admin/noprefix/{user_id}")
    async def api_admin_remove_noprefix(request: Request, user_id: int):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        existed = await bot.db.remove_noprefix(user_id)
        if not existed:
            raise HTTPException(status_code=404, detail="User does not have NoPrefix access")

        target_user = bot.get_user(user_id)
        target_name = str(target_user) if target_user else f"User {user_id}"

        # Dispatch DM notification to user using Component V2
        try:
            from cogs.noprefix import send_noprefix_revoke_dm
            await send_noprefix_revoke_dm(bot, user_id, int(session["id"]), is_expired=False)
        except Exception as e:
            print(f"[Admin Panel NoPrefix DM] Could not send revoke DM: {e}")

        await log_dashboard_action(
            user_id=int(session["id"]),
            username=session.get("username", "Owner"),
            title="🗑️ NoPrefix Removed (Admin Panel)",
            description=f"Owner revoked NoPrefix access from **{target_name}** (`{user_id}`).",
            color=0xe74c3c
        )

        return {"ok": True, "message": f"Removed NoPrefix access from {target_name}"}

    # ── Admin Global Broadcast API ───────────────────────────────

    @app.post("/api/admin/broadcast")
    async def api_admin_broadcast(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        body = await request.json()
        title = str(body.get("title", "📢 Official Announcement")).strip()
        message_content = str(body.get("message", "")).strip()
        color_hex = str(body.get("color", "#8b5cf6")).strip()
        target_mode = str(body.get("target_mode", "system")).strip().lower()

        if not message_content:
            raise HTTPException(status_code=400, detail="Announcement message content cannot be empty.")

        try:
            color_int = int(color_hex.lstrip("#"), 16)
        except Exception:
            color_int = 0x8b5cf6

        import discord
        from discord import ui
        import emojis

        bot_name = bot.user.name if bot.user else "Echo"

        # Build Component V2 layout container
        view = ui.LayoutView()
        container = ui.Container(accent_colour=color_int)

        announcement_text = (
            f"### {emojis.CROWN} **{title}**\n"
            f"{emojis.INFO} **{bot_name} Global Announcement**\n\n"
            f"{message_content}\n\n"
            f"{emojis.DOT} *Broadcasted by Bot Owner • {time.strftime('%Y-%m-%d %H:%M')}*"
        )
        container.add_item(ui.TextDisplay(announcement_text))
        view.add_item(container)

        success_count = 0
        failed_count = 0

        for guild in bot.guilds:
            target_channel = None

            if target_mode == "system" and guild.system_channel:
                target_channel = guild.system_channel

            if not target_channel:
                for ch in guild.text_channels:
                    perms = ch.permissions_for(guild.me)
                    if perms.send_messages and perms.embed_links:
                        target_channel = ch
                        break

            if target_channel:
                try:
                    await target_channel.send(view=view)
                    success_count += 1
                except Exception as e:
                    print(f"[Broadcast Error] Failed to send to {guild.name} ({guild.id}): {e}")
                    failed_count += 1
            else:
                failed_count += 1

        await log_dashboard_action(
            user_id=int(session["id"]),
            username=session.get("username", "Owner"),
            title="📢 Global Server Broadcast Sent",
            description=(
                f"**Title:** **{title}**\n"
                f"**Successful:** `{success_count}` servers\n"
                f"**Failed/Skipped:** `{failed_count}` servers"
            ),
            color=color_int
        )

        return {
            "ok": True,
            "message": f"Broadcast sent to {success_count} server(s). ({failed_count} skipped/failed)",
            "success_count": success_count,
            "failed_count": failed_count,
            "total_guilds": len(bot.guilds)
        }

    # ── Admin Bot Settings API ──────────────────────────────────

    @app.get("/api/admin/settings")
    async def api_admin_get_settings(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        default_prefix = await bot.db.get_bot_setting("default_prefix", Config.DEFAULT_PREFIX)
        default_volume = await bot.db.get_bot_setting("default_volume", "100")
        max_queue = await bot.db.get_bot_setting("max_queue_limit", "500")
        maintenance = await bot.db.get_bot_setting("maintenance_mode", "0")
        auto_disconnect = await bot.db.get_bot_setting("auto_disconnect_sec", "180")

        return {
            "ok": True,
            "settings": {
                "default_prefix": default_prefix,
                "default_volume": int(default_volume) if str(default_volume).isdigit() else 100,
                "max_queue_limit": int(max_queue) if str(max_queue).isdigit() else 500,
                "maintenance_mode": maintenance == "1",
                "auto_disconnect_sec": int(auto_disconnect) if str(auto_disconnect).isdigit() else 180
            }
        }

    @app.post("/api/admin/settings")
    async def api_admin_update_settings(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        body = await request.json()
        prefix = str(body.get("default_prefix", Config.DEFAULT_PREFIX)).strip()
        volume = int(body.get("default_volume", 100))
        max_queue = int(body.get("max_queue_limit", 500))
        maintenance = bool(body.get("maintenance_mode", False))
        auto_disconnect = int(body.get("auto_disconnect_sec", 180))

        await bot.db.set_bot_setting("default_prefix", prefix)
        await bot.db.set_bot_setting("default_volume", str(volume))
        await bot.db.set_bot_setting("max_queue_limit", str(max_queue))
        await bot.db.set_bot_setting("maintenance_mode", "1" if maintenance else "0")
        await bot.db.set_bot_setting("auto_disconnect_sec", str(auto_disconnect))

        bot.command_prefix = prefix
        bot.maintenance_mode = maintenance

        await log_dashboard_action(
            user_id=int(session["id"]),
            username=session.get("username", "Owner"),
            title="🎛️ Global Bot Settings Updated",
            description=f"Prefix: `{prefix}` | Volume: `{volume}%` | Maintenance: `{maintenance}` | AutoDisconnect: `{auto_disconnect}s`",
            color=0x3b82f6
        )

        return {"ok": True, "message": "Bot settings updated successfully"}

    # ── Admin Lavalink Nodes Health Monitor API ───────────────────

    @app.get("/api/admin/lavalink")
    async def api_admin_lavalink_nodes(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        lavalink = getattr(bot, "lavalink", None)
        node_list = []

        if lavalink and hasattr(lavalink, "node_manager"):
            for node in lavalink.node_manager.nodes:
                is_conn = getattr(node, "is_connected", False)
                players = len([p for p in lavalink.player_manager.values() if getattr(p, "node", None) == node])
                stats = getattr(node, "stats", None)

                cpu_load = round(stats.lavalink_load * 100, 1) if (stats and hasattr(stats, "lavalink_load")) else 0.0
                mem_used = round(stats.memory_used / 1024 / 1024, 1) if (stats and hasattr(stats, "memory_used")) else 0.0
                uptime_ms = getattr(stats, "uptime", 0) if stats else 0

                host_val = getattr(node, "host", None) or getattr(node, "_host", None) or getattr(node, "uri", "node")
                port_val = getattr(node, "port", None) or getattr(node, "_port", None) or 0

                node_list.append({
                    "name": getattr(node, "name", "Node"),
                    "host": str(host_val),
                    "port": port_val,
                    "is_connected": is_conn,
                    "players_count": players,
                    "cpu_load": cpu_load,
                    "memory_mb": mem_used,
                    "uptime_ms": uptime_ms,
                    "ping": getattr(node, "ping", 0)
                })

        return {"ok": True, "nodes": node_list, "count": len(node_list)}

    # ── Admin Audit Logs API ─────────────────────────────────────

    @app.get("/api/admin/audit-logs")
    async def api_admin_audit_logs(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        rows = await bot.db.get_audit_logs(limit=60)
        logs = []
        for user_id, username, title, description, color, created_at in rows:
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at)) if created_at else "Unknown"
            logs.append({
                "user_id": str(user_id),
                "username": username or "Owner",
                "title": title,
                "description": description,
                "color_hex": f"#{color:06x}" if color else "#8b5cf6",
                "created_at": time_str
            })

        return {"ok": True, "audit_logs": logs, "count": len(logs)}

    # ── Admin Cog Reload & Bot Maintenance Control API ───────────

    @app.get("/api/admin/cogs")
    async def api_admin_get_cogs(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        cog_names = ["music", "noprefix", "utility", "owner", "info", "events", "config_cog", "playlist_cog"]
        cogs_status = []

        for name in cog_names:
            loaded = f"cogs.{name}" in bot.extensions or name in bot.extensions
            cogs_status.append({"name": name, "extension": f"cogs.{name}", "loaded": loaded})

        return {"ok": True, "cogs": cogs_status}

    @app.post("/api/admin/cogs/{cog_name}/reload")
    async def api_admin_reload_cog(request: Request, cog_name: str):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        ext_name = f"cogs.{cog_name}"
        try:
            await bot.reload_extension(ext_name)
            await log_dashboard_action(
                user_id=int(session["id"]),
                username=session.get("username", "Owner"),
                title=f"🔄 Cog Reloaded: {cog_name}",
                description=f"Extension `{ext_name}` was successfully reloaded.",
                color=0x10b981
            )
            return {"ok": True, "message": f"Successfully reloaded '{cog_name}' cog!"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to reload cog '{cog_name}': {e}")

    @app.post("/api/admin/gc")
    async def api_admin_force_gc(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        import gc
        collected = gc.collect()
        await log_dashboard_action(
            user_id=int(session["id"]),
            username=session.get("username", "Owner"),
            title="🧹 Garbage Collection Executed",
            description=f"Memory GC executed. Cleaned `{collected}` unreferenced Python objects.",
            color=0x06b6d4
        )
        return {"ok": True, "message": f"Garbage Collection freed {collected} objects."}

    # ── Home Notification Banner APIs ────────────────────────────

    @app.get("/api/public/notifications")
    async def api_public_notifications():
        rows = await bot.db.get_active_home_notifications()
        notifications = []
        now = int(time.time())

        for notif_id, title, message, notif_type, color, created_at, expires_at in rows:
            time_left_str = "Permanent"
            if expires_at:
                diff = expires_at - now
                if diff > 86400:
                    time_left_str = f"{diff // 86400} days left"
                elif diff > 3600:
                    time_left_str = f"{diff // 3600} hours left"
                else:
                    time_left_str = f"{max(1, diff // 60)} mins left"

            created_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(created_at))
            notifications.append({
                "id": notif_id,
                "title": title,
                "message": message,
                "type": notif_type or "info",
                "color": color or "#8b5cf6",
                "created_at": created_str,
                "expires_at": expires_at,
                "time_left_str": time_left_str
            })

        return {"ok": True, "notifications": notifications, "count": len(notifications)}

    @app.get("/api/admin/notifications")
    async def api_admin_notifications(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        rows = await bot.db.get_all_home_notifications(limit=50)
        notifications = []
        now = int(time.time())

        for notif_id, title, message, notif_type, color, created_by, created_at, expires_at in rows:
            is_expired = bool(expires_at and expires_at <= now)
            time_left_str = "Expired" if is_expired else ("Permanent" if not expires_at else f"{max(1, (expires_at - now)//3600)}h left")
            created_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(created_at))

            notifications.append({
                "id": notif_id,
                "title": title,
                "message": message,
                "type": notif_type or "info",
                "color": color or "#8b5cf6",
                "created_by": str(created_by),
                "created_at": created_str,
                "expires_at": expires_at,
                "is_expired": is_expired,
                "time_left_str": time_left_str
            })

        return {"ok": True, "notifications": notifications, "count": len(notifications)}

    @app.post("/api/admin/notifications")
    async def api_admin_create_notification(request: Request):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        body = await request.json()
        title = str(body.get("title", "")).strip()
        message = str(body.get("message", "")).strip()
        notif_type = str(body.get("type", "info")).strip()
        color = str(body.get("color", "#8b5cf6")).strip()
        duration_str = str(body.get("duration", "lifetime")).strip().lower()

        if not title or not message:
            raise HTTPException(status_code=400, detail="Title and message content are required.")

        now = int(time.time())
        expires_at = None

        if duration_str == "1h":
            expires_at = now + 3600
        elif duration_str == "6h":
            expires_at = now + 21600
        elif duration_str == "1d":
            expires_at = now + 86400
        elif duration_str == "3d":
            expires_at = now + 259200
        elif duration_str == "7d":
            expires_at = now + 604800
        elif duration_str == "30d":
            expires_at = now + 2592000

        await bot.db.add_home_notification(
            title=title,
            message=message,
            notif_type=notif_type,
            color=color,
            created_by=int(session["id"]),
            expires_at=expires_at
        )

        await log_dashboard_action(
            user_id=int(session["id"]),
            username=session.get("username", "Owner"),
            title="🔔 Home Banner Notification Published",
            description=f"**{title}** | Duration: `{duration_str}` | Type: `{notif_type}`",
            color=0xf59e0b
        )

        return {"ok": True, "message": f"Successfully published notification '{title}' to home page!"}

    @app.delete("/api/admin/notifications/{notif_id}")
    async def api_admin_delete_notification(request: Request, notif_id: int):
        session = require_session(request)
        if not _is_owner(session):
            raise HTTPException(status_code=403, detail="Forbidden: Bot owner access required")

        existed = await bot.db.delete_home_notification(notif_id)
        if not existed:
            raise HTTPException(status_code=404, detail="Notification not found")

        await log_dashboard_action(
            user_id=int(session["id"]),
            username=session.get("username", "Owner"),
            title="🗑️ Home Banner Notification Deleted",
            description=f"Owner deleted home notification ID `{notif_id}`.",
            color=0xef4444
        )

        return {"ok": True, "message": "Notification removed successfully"}

    @app.get("/api/guilds")
    async def api_guilds(request: Request):
        session = require_session(request)
        user_id = int(session["id"])

        cache = app.state.guild_cache.get(session["id"])
        if not cache:
            raise HTTPException(status_code=401, detail="Session expired")
        user_guilds = cache["guilds"]

        result = []
        for ug in user_guilds:
            guild_id_int = int(ug["id"])
            g = bot.get_guild(guild_id_int)
            
            perms = int(ug.get("permissions", 0))
            is_admin = (perms & 0x8) == 0x8 or (perms & 0x20) == 0x20 or ug.get("owner", False)
            
            if g:
                level = await member_permission_level(g, user_id)
                if level is not None:
                    lavalink = getattr(bot, "lavalink", None)
                    player = lavalink.player_manager.get(g.id) if lavalink else None
                    is_playing = bool(player and player.is_connected and player.current)
                    
                    result.append({
                        "id": str(g.id),
                        "name": g.name,
                        "icon": g.icon.url if g.icon else None,
                        "member_count": g.member_count,
                        "permission_level": level,
                        "is_playing": is_playing,
                        "bot_present": True
                    })
            else:
                if is_admin:
                    icon_hash = ug.get("icon")
                    icon_url = f"https://cdn.discordapp.com/icons/{ug['id']}/{icon_hash}.png" if icon_hash else None
                    
                    bot_id = bot.user.id if bot.user else ""
                    invite_url = f"https://discord.com/oauth2/authorize?client_id={bot_id}&permissions=7107797346413761&integration_type=0&scope=bot%20applications.commands&guild_id={ug['id']}&disable_guild_select=true"
                    
                    result.append({
                        "id": ug["id"],
                        "name": ug["name"],
                        "icon": icon_url,
                        "member_count": 0,
                        "permission_level": "admin" if ug.get("owner") else "manager",
                        "is_playing": False,
                        "bot_present": False,
                        "invite_url": invite_url
                    })
                    
        result.sort(key=lambda x: (not x["bot_present"], x["name"]))
        return {"guilds": result}

    @app.get("/api/guilds/{guild_id}/player")
    async def api_player_state(request: Request, guild_id: int):
        session = require_session(request)
        guild = bot.get_guild(guild_id)
        if not guild or await member_permission_level(guild, int(session["id"])) is None:
            raise HTTPException(status_code=403, detail="Forbidden")
        return player_to_dict(guild_id)


    @app.get("/api/guilds/{guild_id}/lyrics")
    async def api_get_lyrics(request: Request, guild_id: int, q: str = None):
        session = require_session(request)
        if not session:
            raise HTTPException(status_code=401, detail="Unauthorized")
            
        search_query = q
        if not search_query:
            music_cog = bot.get_cog("Music")
            if not music_cog or not music_cog.lavalink:
                raise HTTPException(status_code=500, detail="Music system unavailable")
            player = music_cog.lavalink.player_manager.get(guild_id)
            if not player or not player.current:
                return {"ok": False, "error": "Nothing is playing"}
            
            title = player.current.title
            author = player.current.author
            clean_title = re.sub(r'[\(\[][^\)\]]*[\)\]]', '', title)
            clean_title = re.sub(r'(?i)\b(official|video|lyrics|audio|music|hd|4k|mv)\b', '', clean_title)
            clean_title = " ".join(clean_title.split())
            
            search_query = f"{clean_title} {author}"

        import urllib.parse
        encoded_query = urllib.parse.quote(search_query)
        url = f"https://lrclib.net/api/search?q={encoded_query}"
        
        headers = {
            "User-Agent": "EchoBotLyricsAPI/1.0"
        }
        
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session_http:
                async with session_http.get(url, headers=headers, timeout=5.0) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and len(data) > 0:
                            best_match = data[0]
                            return {
                                "ok": True,
                                "title": best_match.get("trackName"),
                                "artist": best_match.get("artistName"),
                                "lyrics": best_match.get("plainLyrics") or "Instrumental track or lyrics not available.",
                                "synced": best_match.get("syncedLyrics")
                            }
            return {"ok": False, "error": "Lyrics not found."}
        except Exception as e:
            err_msg = str(e) or e.__class__.__name__
            print(f"[Lyrics Fetch Error] Query: '{search_query}', Error: {err_msg} ({type(e)})")
            return {"ok": False, "error": f"Failed to fetch lyrics: {err_msg}"}

    @app.get("/api/guilds/{guild_id}/music-stats")
    async def api_get_music_stats(request: Request, guild_id: int):
        session = require_session(request)
        guild = bot.get_guild(guild_id)
        if not guild:
            raise HTTPException(status_code=404, detail="Guild not found")
        if await member_permission_level(guild, int(session["id"])) is None:
            raise HTTPException(status_code=403, detail="Forbidden")

        top_tracks = await bot.db.get_top_played(guild_id, limit=15)
        recent_tracks = await bot.db.get_recently_played(guild_id, limit=15)

        return {
            "ok": True,
            "top": top_tracks,
            "recent": recent_tracks
        }

    # ── Playlists API ───────────────────────────────────────────

    @app.get("/api/playlists")
    async def api_get_playlists(request: Request):
        session = require_session(request)
        user_id = int(session["id"])
        playlists = await bot.db.get_playlists(user_id)
        result = []
        for p in playlists:
            code = p[4]
            if not code:
                code = await bot.db.ensure_playlist_code(p[0])
            result.append({
                "id": p[0],
                "name": p[1],
                "created_at": p[2],
                "track_count": p[3],
                "code": code,
                "is_public": bool(p[5])
            })
        return result

    @app.post("/api/playlists")
    async def api_create_playlist(request: Request):
        session = require_session(request)
        user_id = int(session["id"])
        body = await request.json()
        name = body.get("name", "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Playlist name cannot be empty")
        existing = await bot.db.get_playlist_by_name(user_id, name)
        if existing:
            raise HTTPException(status_code=400, detail="A playlist with that name already exists")
        playlist_id, code = await bot.db.create_playlist(user_id, name)
        
        # Log playlist create
        try:
            user_name = session.get("username", f"User {user_id}")
            await log_dashboard_action(
                user_id=user_id,
                username=user_name,
                title="📁 Playlist Created",
                description=(
                    f"**Name:** **{name}**\n"
                    f"**Code:** `{code}`\n\n"
                    f"Created successfully via web dashboard."
                ),
                color=0x2ecc71
            )
        except Exception:
            pass

        return {"id": playlist_id, "name": name, "code": code, "is_public": False}

    @app.patch("/api/playlists/{playlist_id}")
    async def api_rename_playlist(request: Request, playlist_id: int):
        session = require_session(request)
        user_id = int(session["id"])
        body = await request.json()
        new_name = body.get("name", "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="Playlist name cannot be empty")
        existing = await bot.db.get_playlist_by_name(user_id, new_name)
        if existing and existing[0] != playlist_id:
            raise HTTPException(status_code=400, detail="A playlist with that name already exists")
        playlist = await bot.db.get_playlist(user_id, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")
        ok = await bot.db.rename_playlist(user_id, playlist_id, new_name)
        if not ok:
            raise HTTPException(status_code=404, detail="Playlist not found")

        # Log rename
        try:
            user_name = session.get("username", f"User {user_id}")
            await log_dashboard_action(
                user_id=user_id,
                username=user_name,
                title="✏️ Playlist Renamed",
                description=(
                    f"**Old Name:** **{playlist[1]}**\n"
                    f"**New Name:** **{new_name}**\n"
                    f"**ID:** `{playlist_id}`\n\n"
                    f"Renamed successfully via web dashboard."
                ),
                color=0x3498db
            )
        except Exception:
            pass

        return {"ok": True}

    @app.delete("/api/playlists/{playlist_id}")
    async def api_delete_playlist(request: Request, playlist_id: int):
        session = require_session(request)
        user_id = int(session["id"])
        playlist = await bot.db.get_playlist(user_id, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")
        ok = await bot.db.delete_playlist(user_id, playlist_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Playlist not found")

        # Log playlist delete
        try:
            user_name = session.get("username", f"User {user_id}")
            await log_dashboard_action(
                user_id=user_id,
                username=user_name,
                title="🗑️ Playlist Deleted",
                description=(
                    f"**Name:** **{playlist[1]}**\n"
                    f"**ID:** `{playlist_id}`\n\n"
                    f"Deleted successfully via web dashboard."
                ),
                color=0xe74c3c
            )
        except Exception:
            pass

        return {"ok": True}

    @app.patch("/api/playlists/{playlist_id}/privacy")
    async def api_set_playlist_privacy(request: Request, playlist_id: int):
        session = require_session(request)
        user_id = int(session["id"])
        playlist = await bot.db.get_playlist(user_id, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")

        body = await request.json()
        is_public = bool(body.get("is_public", False))
        ok = await bot.db.set_playlist_privacy(user_id, playlist_id, is_public)
        if not ok:
            raise HTTPException(status_code=404, detail="Playlist not found")

        # Log privacy
        try:
            user_name = session.get("username", f"User {user_id}")
            privacy_str = "Public 🔓" if is_public else "Private 🔒"
            await log_dashboard_action(
                user_id=user_id,
                username=user_name,
                title="🛡️ Playlist Privacy Updated",
                description=(
                    f"**Playlist Name:** **{playlist[1]}**\n"
                    f"**New Privacy:** `{privacy_str}`\n"
                    f"**Code:** `{playlist[5]}`\n\n"
                    f"Privacy updated successfully via web dashboard."
                ),
                color=0xf1c40f
            )
        except Exception:
            pass

        return {"ok": True, "is_public": is_public}

    @app.get("/api/playlists/code/{code}")
    async def api_get_playlist_by_code(request: Request, code: str):
        """Lookup any playlist by share code. Returns info if public (or owner)."""
        playlist = await bot.db.get_playlist_by_code(code.upper())
        if not playlist:
            raise HTTPException(status_code=404, detail="No playlist found with that code")
        # Check auth - allow if public or owner
        try:
            session = require_session(request)
            user_id = int(session["id"])
        except Exception:
            user_id = None
        is_owner = user_id == playlist[2]
        if not playlist[4] and not is_owner:  # not public and not owner
            raise HTTPException(status_code=403, detail="This playlist is private")
        tracks = await bot.db.get_playlist_tracks(playlist[0])
        return {
            "id": playlist[0],
            "name": playlist[1],
            "track_count": playlist[3],
            "is_public": bool(playlist[4]),
            "code": playlist[5],
            "is_owner": is_owner,
            "tracks": [
                {"id": t[0], "title": t[1], "author": t[2], "uri": t[3], "identifier": t[4]}
                for t in tracks
            ]
        }

    @app.get("/api/leaderboard")
    async def api_get_leaderboard(request: Request, timeframe: str = "all"):
        """Get the public playlist leaderboard."""
        require_session(request)
        raw_leaderboard = await bot.db.get_playlist_leaderboard(timeframe, limit=20)
        
        leaderboard = []
        for row in raw_leaderboard:
            playlist_id, name, owner_id, code, play_count, track_count = row
            
            owner_name = f"User {owner_id}"
            user = bot.get_user(owner_id)
            if user:
                owner_name = user.name
            else:
                try:
                    user = await bot.fetch_user(owner_id)
                    if user:
                        owner_name = user.name
                except Exception:
                    pass
            
            leaderboard.append({
                "id": playlist_id,
                "name": name,
                "owner_id": owner_id,
                "owner_name": owner_name,
                "code": code,
                "play_count": play_count,
                "track_count": track_count
            })
            
        return leaderboard

    @app.get("/api/search")
    async def api_search(request: Request, query: str):
        session = require_session(request)
        query = query.strip()
        if not query:
            return {"tracks": []}

        music_cog = bot.get_cog("Music")
        if not music_cog or not music_cog.lavalink:
            raise HTTPException(status_code=500, detail="Music system is not available")

        nodes = [n for n in music_cog.lavalink.node_manager.nodes if n.is_connected]
        if not nodes:
            raise HTTPException(status_code=500, detail="No Lavalink audio nodes connected")
        node = music_cog.lavalink.node_manager.find_ideal_node() or nodes[0]

        search_query = query
        if not (search_query.startswith("http://") or search_query.startswith("https://")):
            search_query = f"ytsearch:{search_query}"

        try:
            results = await node.get_tracks(search_query)
            if not results or not results.tracks:
                return {"tracks": []}
            return {
                "tracks": [
                    {
                        "title": track.title,
                        "author": track.author,
                        "uri": track.uri,
                        "identifier": track.identifier,
                        "duration": track.duration
                    }
                    for track in results.tracks[:10]
                ]
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Search failed: {e}")

    @app.get("/api/playlists/{playlist_id}/tracks")
    async def api_get_playlist_tracks(request: Request, playlist_id: int):
        session = require_session(request)
        user_id = int(session["id"])
        playlist = await bot.db.get_playlist(user_id, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")
        tracks = await bot.db.get_playlist_tracks(playlist_id)
        return [
            {
                "id": t[0],
                "title": t[1],
                "author": t[2],
                "uri": t[3],
                "identifier": t[4],
                "added_at": t[5]
            }
            for t in tracks
        ]

    @app.post("/api/playlists/{playlist_id}/tracks")
    async def api_add_playlist_track(request: Request, playlist_id: int):
        session = require_session(request)
        user_id = int(session["id"])
        playlist = await bot.db.get_playlist(user_id, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")

        body = await request.json()
        query = body.get("query", "").strip()

        if query:
            if "spotify.com/" in query.lower():
                print(f"[PlaylistAPI] Resolving Spotify URL: '{query}'")
                spotify_tracks = await resolve_spotify_url(query)
                print(f"[PlaylistAPI] Result from resolve_spotify_url: {spotify_tracks}")
                if spotify_tracks:
                    added_tracks = []
                    for t in spotify_tracks:
                        track_id = await bot.db.add_to_playlist(
                            playlist_id, 
                            t["title"], 
                            t["author"], 
                            t["uri"], 
                            t["identifier"]
                        )
                        added_tracks.append({"id": track_id, "title": t["title"]})
                    
                    if len(added_tracks) > 1:
                        return {"playlist": True, "count": len(added_tracks), "tracks": added_tracks}
                    elif len(added_tracks) == 1:
                        return {"id": added_tracks[0]["id"], "title": added_tracks[0]["title"]}
                    else:
                        raise HTTPException(status_code=400, detail="No tracks found in the Spotify link.")
                else:
                    raise HTTPException(status_code=400, detail="Failed to resolve Spotify link. Please ensure the link is public.")

            music_cog = bot.get_cog("Music")
            if not music_cog or not music_cog.lavalink:
                raise HTTPException(status_code=500, detail="Music system is not available")
            
            nodes = [n for n in music_cog.lavalink.node_manager.nodes if n.is_connected]
            if not nodes:
                raise HTTPException(status_code=500, detail="No Lavalink audio nodes connected")
            node = music_cog.lavalink.node_manager.find_ideal_node() or nodes[0]

            search_query = query
            if not (search_query.startswith("http://") or search_query.startswith("https://")):
                search_query = f"ytsearch:{search_query}"
            
            try:
                results = await node.get_tracks(search_query)
                if not results or not results.tracks:
                    raise HTTPException(status_code=400, detail=f"No results found for '{query}'")
                track = results.tracks[0]
                title = track.title
                author = track.author
                uri = track.uri
                identifier = track.identifier
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to resolve track: {e}")
        else:
            title = body.get("title", "").strip()
            author = body.get("author", "").strip()
            uri = body.get("uri", "").strip()
            identifier = body.get("identifier", "").strip() or None

        if not title or not uri:
            raise HTTPException(status_code=400, detail="Missing track title or uri")

        track_id = await bot.db.add_to_playlist(playlist_id, title, author, uri, identifier)
        return {"id": track_id, "title": title}

    @app.delete("/api/playlists/{playlist_id}/tracks/{track_id}")
    async def api_remove_playlist_track(request: Request, playlist_id: int, track_id: int):
        session = require_session(request)
        user_id = int(session["id"])
        playlist = await bot.db.get_playlist(user_id, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")
        ok = await bot.db.remove_from_playlist(playlist_id, track_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Track not found")
        return {"ok": True}

    @app.post("/api/guilds/{guild_id}/play-playlist")
    async def api_play_playlist(request: Request, guild_id: int):
        session = require_session(request)
        user_id = int(session["id"])
        body = await request.json()
        playlist_id = body.get("playlist_id")
        if not playlist_id:
            raise HTTPException(status_code=400, detail="Missing playlist_id")

        guild = bot.get_guild(guild_id)
        if not guild:
            raise HTTPException(status_code=404, detail="Guild not found")

        member = guild.get_member(user_id)
        if not member or not member.voice or not member.voice.channel:
            raise HTTPException(status_code=400, detail="You must be in a voice channel to play music")

        music_cog = bot.get_cog("Music")
        if not music_cog:
            raise HTTPException(status_code=500, detail="Music cog not loaded")

        playlist = await bot.db.get_playlist(user_id, playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")

        tracks = await bot.db.get_playlist_tracks(playlist_id)
        if not tracks:
            raise HTTPException(status_code=400, detail="Playlist is empty")

        from cogs.music import LavalinkVoiceClient

        player = music_cog.lavalink.player_manager.create(guild_id)

        if not guild.voice_client:
            perms = member.voice.channel.permissions_for(guild.me)
            if not perms.connect or not perms.speak:
                raise HTTPException(status_code=400, detail="Echo needs Connect and Speak permissions in your voice channel")
            player.store("channel", None)
            try:
                await member.voice.channel.connect(cls=LavalinkVoiceClient, self_deaf=True)
                await asyncio.sleep(0.5)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to join voice channel: {e}")
        elif guild.voice_client.channel != member.voice.channel:
            raise HTTPException(status_code=400, detail="Echo is already playing in a different voice channel")

        # Resolve tracks in parallel with rate limit safety
        sem = asyncio.Semaphore(3)
        tasks = [
            _resolve_track(sem, player, t[1], t[3], stagger=i * 0.15)
            for i, t in enumerate(tracks)
        ]
        resolved_tracks = await asyncio.gather(*tasks)

        added_count = 0
        for track in resolved_tracks:
            if track:
                player.add(requester=user_id, track=track)
                added_count += 1

        if added_count == 0:
            raise HTTPException(status_code=400, detail="Failed to load any tracks from the playlist")

        if not player.is_playing:
            try:
                await player.play()
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Playback failed: {e}")

        await bot.db.record_playlist_play(playlist_id)
        await music_cog._notify_dashboard(guild_id)

        return {"ok": True, "message": f"Enqueued {added_count} tracks from playlist '{playlist[1]}'!"}

    @app.post("/api/guilds/{guild_id}/play-playlist-code")
    async def api_play_playlist_by_code(request: Request, guild_id: int):
        """Play a playlist by its share code (must be public, or owned by requester)."""
        session = require_session(request)
        user_id = int(session["id"])
        body = await request.json()
        code = body.get("code", "").strip().upper()
        if not code:
            raise HTTPException(status_code=400, detail="Missing playlist code")

        playlist = await bot.db.get_playlist_by_code(code)
        if not playlist:
            raise HTTPException(status_code=404, detail="No playlist found with that code")

        is_owner = user_id == playlist[2]
        if not playlist[4] and not is_owner:
            raise HTTPException(status_code=403, detail="This playlist is private")

        guild = bot.get_guild(guild_id)
        if not guild:
            raise HTTPException(status_code=404, detail="Guild not found")

        member = guild.get_member(user_id)
        if not member or not member.voice or not member.voice.channel:
            raise HTTPException(status_code=400, detail="You must be in a voice channel to play music")

        music_cog = bot.get_cog("Music")
        if not music_cog:
            raise HTTPException(status_code=500, detail="Music cog not loaded")

        tracks = await bot.db.get_playlist_tracks(playlist[0])
        if not tracks:
            raise HTTPException(status_code=400, detail="Playlist is empty")

        from cogs.music import LavalinkVoiceClient
        player = music_cog.lavalink.player_manager.create(guild_id)

        if not guild.voice_client:
            perms = member.voice.channel.permissions_for(guild.me)
            if not perms.connect or not perms.speak:
                raise HTTPException(status_code=400, detail="Echo needs Connect and Speak permissions in your voice channel")
            player.store("channel", None)
            try:
                await member.voice.channel.connect(cls=LavalinkVoiceClient, self_deaf=True)
                await asyncio.sleep(0.5)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to join voice channel: {e}")
        elif guild.voice_client.channel != member.voice.channel:
            raise HTTPException(status_code=400, detail="Echo is already playing in a different voice channel")

        # Resolve tracks in parallel with rate limit safety
        sem = asyncio.Semaphore(3)
        tasks = [
            _resolve_track(sem, player, t[1], t[3], stagger=i * 0.15)
            for i, t in enumerate(tracks)
        ]
        resolved_tracks = await asyncio.gather(*tasks)

        added_count = 0
        for track in resolved_tracks:
            if track:
                player.add(requester=user_id, track=track)
                added_count += 1

        if added_count == 0:
            raise HTTPException(status_code=400, detail="Failed to load any tracks from the playlist")

        if not player.is_playing:
            try:
                await player.play()
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Playback failed: {e}")

        await bot.db.record_playlist_play(playlist[0])
        await music_cog._notify_dashboard(guild_id)
        return {"ok": True, "message": f"Enqueued {added_count} tracks from \"{playlist[1]}\"!", "playlist_name": playlist[1]}

    # ── Player control actions ──────────────────────────────────
    # These call the SAME lavalink player_manager the Discord
    # commands use — a dashboard click and a `>skip` command do
    # the exact same thing under the hood.

    async def _require_player_control(request: Request, guild_id: int):
        session = require_session(request)
        guild = bot.get_guild(guild_id)
        if not guild:
            raise HTTPException(status_code=404, detail="Guild not found")
        level = await member_permission_level(guild, int(session["id"]))
        if level is None:
            raise HTTPException(status_code=403, detail="Forbidden")

        lavalink = getattr(bot, "lavalink", None)
        player = lavalink.player_manager.get(guild_id) if lavalink else None
        if not player or not player.is_connected:
            raise HTTPException(status_code=409, detail="Bot is not connected to a voice channel")

        # Managers/owners can always control. Regular members can
        # control only if they're actually sitting in the same voice
        # channel as the bot — mirrors normal in-Discord expectations.
        if level == "member":
            member = guild.get_member(int(session["id"]))
            bot_vc = guild.voice_client
            in_same_vc = (
                member and member.voice and bot_vc
                and member.voice.channel and member.voice.channel.id == bot_vc.channel.id
            )
            if not in_same_vc:
                raise HTTPException(
                    status_code=403,
                    detail="Join the voice channel Echo is in to control playback"
                )
            music_cog = bot.get_cog("Music")
            if music_cog and member:
                allowed, dj_msg = await music_cog._check_dj_permission(member, guild)
                if not allowed:
                    raise HTTPException(status_code=403, detail=dj_msg)
        return player

    @app.post("/api/guilds/{guild_id}/pause")
    async def api_pause(request: Request, guild_id: int):
        player = await _require_player_control(request, guild_id)
        player.delete("auto_paused_empty_vc")
        await player.set_pause(True)
        music_cog = bot.get_cog("Music")
        if music_cog and player.current:
            await music_cog._update_vc_status(guild_id, f"{emojis.BTN_PAUSE} Paused: {player.current.title}")
            await music_cog._update_now_playing(guild_id)
        await broadcast_player_update(guild_id)
        return {"ok": True}

    @app.post("/api/guilds/{guild_id}/resume")
    async def api_resume(request: Request, guild_id: int):
        player = await _require_player_control(request, guild_id)
        player.delete("auto_paused_empty_vc")
        await player.set_pause(False)
        music_cog = bot.get_cog("Music")
        if music_cog and player.current:
            await music_cog._update_vc_status(guild_id, f"{emojis.MYMUSIC} {player.current.title}")
            await music_cog._update_now_playing(guild_id)
        await broadcast_player_update(guild_id)
        return {"ok": True}

    @app.post("/api/guilds/{guild_id}/skip")
    async def api_skip(request: Request, guild_id: int):
        player = await _require_player_control(request, guild_id)
        await player.skip()
        await broadcast_player_update(guild_id)
        return {"ok": True}

    @app.post("/api/guilds/{guild_id}/stop")
    async def api_stop(request: Request, guild_id: int):
        player = await _require_player_control(request, guild_id)
        player.queue.clear()
        await player.stop()
        await broadcast_player_update(guild_id)
        return {"ok": True}

    @app.post("/api/guilds/{guild_id}/volume")
    async def api_volume(request: Request, guild_id: int):
        player = await _require_player_control(request, guild_id)
        body = await request.json()
        vol = max(0, min(150, int(body.get("volume", 100))))
        await player.set_volume(vol)
        await broadcast_player_update(guild_id)
        return {"ok": True, "volume": vol}

    @app.post("/api/guilds/{guild_id}/loop")
    async def api_loop(request: Request, guild_id: int):
        player = await _require_player_control(request, guild_id)
        body = await request.json()
        player.loop = bool(body.get("loop", False))
        await broadcast_player_update(guild_id)
        return {"ok": True, "loop": player.loop}

    @app.post("/api/guilds/{guild_id}/shuffle")
    async def api_shuffle(request: Request, guild_id: int):
        player = await _require_player_control(request, guild_id)
        import random
        if player.queue:
            random.shuffle(player.queue)
        await broadcast_player_update(guild_id)
        return {"ok": True}

    @app.post("/api/guilds/{guild_id}/autoplay")
    async def api_autoplay(request: Request, guild_id: int):
        await _require_player_control(request, guild_id)
        music_cog = bot.get_cog("Music")
        if not music_cog:
            raise HTTPException(status_code=500, detail="Music system unavailable")
        body = await request.json()
        music_cog.autoplay_states[guild_id] = bool(body.get("autoplay", False))
        await broadcast_player_update(guild_id)
        return {"ok": True, "autoplay": music_cog.autoplay_states[guild_id]}

    @app.post("/api/guilds/{guild_id}/seek")
    async def api_seek(request: Request, guild_id: int):
        player = await _require_player_control(request, guild_id)
        body = await request.json()
        pos = int(body.get("position", 0))
        await player.seek(pos)
        await broadcast_player_update(guild_id)
        return {"ok": True, "position": pos}

    @app.post("/api/guilds/{guild_id}/previous")
    async def api_previous(request: Request, guild_id: int):
        player = await _require_player_control(request, guild_id)
        music_cog = bot.get_cog("Music")
        if not music_cog:
            raise HTTPException(status_code=500, detail="Music system unavailable")
        ok = await music_cog.play_previous(guild_id)
        if not ok:
            raise HTTPException(status_code=409, detail="No previous track")
        await broadcast_player_update(guild_id)
        return {"ok": True}

    @app.post("/api/guilds/{guild_id}/replay")
    async def api_replay(request: Request, guild_id: int):
        player = await _require_player_control(request, guild_id)
        if not player.current:
            raise HTTPException(status_code=409, detail="Nothing is playing")
        await player.seek(0)
        await broadcast_player_update(guild_id)
        return {"ok": True}

    @app.post("/api/guilds/{guild_id}/queue/{index}/remove")
    async def api_queue_remove(request: Request, guild_id: int, index: int):
        player = await _require_player_control(request, guild_id)
        if 0 <= index < len(player.queue):
            player.queue.pop(index)
        await broadcast_player_update(guild_id)
        return {"ok": True}

    @app.get("/api/guilds/{guild_id}/settings")
    async def api_get_guild_settings(request: Request, guild_id: int):
        session = require_session(request)
        if not session:
            raise HTTPException(status_code=401, detail="Login required")
        guild = bot.get_guild(guild_id)
        if not guild:
            try:
                guild = await bot.fetch_guild(guild_id)
            except Exception:
                raise HTTPException(status_code=404, detail="Server not found")

        level = await member_permission_level(guild, int(session["id"]))
        if level is None:
            raise HTTPException(status_code=403, detail="Access denied")

        settings = await bot.db.get_guild_settings(guild_id)

        roles_list = []
        vc_channels = []
        tc_channels = []

        try:
            raw_roles = await bot.http.get_roles(guild_id)
            for r in raw_roles:
                r_id = str(r.get("id"))
                if r_id != str(guild_id) and not r.get("managed", False):
                    roles_list.append({"id": r_id, "name": r.get("name")})
        except Exception as e:
            print(f"[Settings API] Error fetching HTTP roles: {e}")

        try:
            raw_channels = await bot.http.get_all_guild_channels(guild_id)
            for c in raw_channels:
                c_id = str(c.get("id"))
                c_name = str(c.get("name"))
                c_type = int(c.get("type", 0))
                # 2 = Guild Voice, 13 = Stage Voice
                if c_type in (2, 13):
                    vc_channels.append({"id": c_id, "name": c_name})
                # 0 = Guild Text, 5 = Announcement, 15 = Forum
                elif c_type in (0, 5, 15):
                    tc_channels.append({"id": c_id, "name": c_name})
        except Exception as e:
            print(f"[Settings API] Error fetching HTTP channels: {e}")

        # Fallback to in-memory guild objects if HTTP returned empty
        if not roles_list and hasattr(guild, "roles") and guild.roles:
            for r in guild.roles:
                if not r.is_default() and not getattr(r, "managed", False):
                    roles_list.append({"id": str(r.id), "name": r.name})

        if not vc_channels and hasattr(guild, "voice_channels") and guild.voice_channels:
            vc_channels = [{"id": str(c.id), "name": c.name} for c in guild.voice_channels]

        if not tc_channels and hasattr(guild, "text_channels") and guild.text_channels:
            tc_channels = [{"id": str(c.id), "name": c.name} for c in guild.text_channels]

        return {
            "ok": True,
            "settings": settings,
            "roles": roles_list,
            "vc_channels": vc_channels,
            "tc_channels": tc_channels
        }

    @app.post("/api/guilds/{guild_id}/settings")
    async def api_update_guild_settings(request: Request, guild_id: int):
        session = require_session(request)
        if not session:
            raise HTTPException(status_code=401, detail="Login required")
        guild = bot.get_guild(guild_id)
        if not guild:
            raise HTTPException(status_code=404, detail="Server not found")

        level = await member_permission_level(guild, int(session["id"]))
        if level not in ("owner", "manager"):
            raise HTTPException(status_code=403, detail="Admin/Manager permissions required")

        body = await request.json()
        prefix = str(body.get("prefix", ">")).strip() or ">"
        twentyfourseven = bool(body.get("twentyfourseven", False))
        default_volume = max(0, min(150, int(body.get("default_volume", 100))))
        announce_now_playing = bool(body.get("announce_now_playing", True))

        def safe_int_or_none(val):
            if not val:
                return None
            try:
                return int(val)
            except (ValueError, TypeError):
                return None

        dj_role_id = safe_int_or_none(body.get("dj_role_id"))
        restricted_vc_id = safe_int_or_none(body.get("restricted_vc_id"))
        restricted_tc_id = safe_int_or_none(body.get("restricted_tc_id"))
        twentyfourseven_channel_id = safe_int_or_none(body.get("twentyfourseven_channel_id"))
        if twentyfourseven and not twentyfourseven_channel_id:
            if guild and guild.voice_client and guild.voice_client.channel:
                twentyfourseven_channel_id = guild.voice_client.channel.id

        await bot.db.update_guild_settings(
            guild_id=guild_id,
            prefix=prefix,
            twentyfourseven=twentyfourseven,
            twentyfourseven_channel_id=twentyfourseven_channel_id,
            default_volume=default_volume,
            announce_now_playing=announce_now_playing,
            dj_role_id=dj_role_id,
            restricted_vc_id=restricted_vc_id,
            restricted_tc_id=restricted_tc_id
        )

        player = bot.lavalink.player_manager.get(guild_id) if hasattr(bot, "lavalink") and bot.lavalink else None
        if player:
            await player.set_volume(default_volume)

        await log_dashboard_action(
            user_id=int(session["id"]),
            username=session.get("username", "Admin"),
            title="⚙️ Guild Settings Updated",
            description=f"Admin updated guild configuration for **{guild.name}** (`{guild_id}`).",
            fields=[
                {"name": "Prefix", "value": f"`{prefix}`", "inline": True},
                {"name": "24/7 Mode", "value": "Enabled" if twentyfourseven else "Disabled", "inline": True},
                {"name": "Default Volume", "value": f"`{default_volume}%`", "inline": True}
            ],
            color=0x3498db
        )

        return {"ok": True, "message": "Settings updated successfully"}

    @app.get("/api/guilds/{guild_id}/filter")
    async def api_get_filter(request: Request, guild_id: int):
        player = bot.lavalink.player_manager.get(guild_id) if hasattr(bot, "lavalink") and bot.lavalink else None
        active_filter = player.fetch("active_filter") if player else None
        return {"ok": True, "active_filter": active_filter}

    @app.post("/api/guilds/{guild_id}/filter")
    async def api_apply_filter(request: Request, guild_id: int):
        player = await _require_player_control(request, guild_id)
        session = require_session(request)
        guild = bot.get_guild(guild_id)
        member = guild.get_member(int(session["id"])) if (guild and session) else None

        music_cog = bot.get_cog("Music")
        if not music_cog:
            raise HTTPException(status_code=500, detail="Music system unavailable")

        body = await request.json()
        filter_type = str(body.get("filter", "clear"))
        ok, msg = await music_cog.apply_filter(guild_id, filter_type, requester=member)
        if not ok:
            raise HTTPException(status_code=403 if "DJ Role" in msg else 400, detail=msg)

        await broadcast_player_update(guild_id)
        return {"ok": True, "message": msg, "active_filter": filter_type if filter_type != "clear" else None}

    @app.post("/api/guilds/{guild_id}/play")
    async def api_play(request: Request, guild_id: int):
        """Queue a track from the dashboard search box. User must already
        be in a voice channel, same rule as the >play command."""
        session = require_session(request)
        guild = bot.get_guild(guild_id)
        if not guild:
            raise HTTPException(status_code=404, detail="Guild not found")
        if await member_permission_level(guild, int(session["id"])) is None:
            raise HTTPException(status_code=403, detail="Forbidden")

        member = guild.get_member(int(session["id"]))
        if not member or not member.voice or not member.voice.channel:
            raise HTTPException(status_code=400, detail="Join a voice channel in Discord first")

        body = await request.json()
        query = (body.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="No search query given")

        music_cog = bot.get_cog("Music")
        if not music_cog:
            raise HTTPException(status_code=500, detail="Music system unavailable")

        ok, message = await music_cog.play_from_dashboard(guild, member, query)
        if not ok:
            raise HTTPException(status_code=400, detail=message)

        await broadcast_player_update(guild_id)
        return {"ok": True, "message": message}

    # ── WebSocket — live player sync ─────────────────────────────

    @app.websocket("/ws/{guild_id}")
    async def ws_player(websocket: WebSocket, guild_id: int):
        token = websocket.cookies.get("Echo_session")
        session = oauth.read_session_token(token) if token else None
        if not session:
            await websocket.close(code=4001)
            return

        guild = bot.get_guild(guild_id)
        if not guild or await member_permission_level(guild, int(session["id"])) is None:
            await websocket.close(code=4003)
            return

        await websocket.accept()
        app.state.ws_clients.setdefault(guild_id, set()).add(websocket)

        try:
            # Send initial state immediately on connect
            await websocket.send_json({"type": "player_update", "data": player_to_dict(guild_id)})
            while True:
                # We don't expect incoming messages other than pings —
                # all control goes through the REST endpoints above.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            app.state.ws_clients.get(guild_id, set()).discard(websocket)

    return app


async def run_dashboard(bot):
    """Entry point called from main.py — runs the dashboard server
    as a background task on the bot's own event loop.

    Deliberately does NOT call server.serve() — that method wraps
    everything in uvicorn's capture_signals(), which installs its own
    SIGINT/SIGTERM handlers. Since this task shares a process (and event
    loop) with the Discord bot, those handlers fight with asyncio's/the
    bot's own shutdown handling: on a host-issued stop, both try to react
    to the same signal, and this task's resulting KeyboardInterrupt had
    nowhere to go because nothing was awaiting it — hence the
    "Task exception was never retrieved" warning during shutdown.

    Calling startup()/main_loop()/shutdown() directly is uvicorn's own
    documented pattern for embedding the server inside another
    application's event loop instead of owning the process' signals.
    """
    import uvicorn

    app = create_dashboard(bot)
    dashboard_host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    config = uvicorn.Config(app, host=dashboard_host, port=DASHBOARD_PORT, log_level="warning")
    server = uvicorn.Server(config)
    bot.dashboard_server = server  # so bot shutdown can trigger a clean stop
    print(f"  🌐 Dashboard starting on http://{dashboard_host}:{DASHBOARD_PORT}")
    try:
        if not config.loaded:
            config.load()
        server.lifespan = config.lifespan_class(config)
        await server.startup()
        await server.main_loop()
    except asyncio.CancelledError:
        pass  # normal path when the bot cancels this task on shutdown
    finally:
        try:
            await server.shutdown()
        except Exception:
            pass
