"""
Music cog for Rose - lavalink.py based player with YouTube, Spotify
and Apple Music support, 24/7 mode, autoplay and live VC status.
"""

import discord
from discord.ext import commands
from discord import ui
import lavalink
import re
import math
import random
import asyncio
import aiohttp
from config import Config
import emojis
import datetime
from utils.canvas import generate_music_banner, fetch_image_bytes
# ─── Monkeypatch lavalink.DefaultPlayer for Automatic Node Failover ───
def make_auto_failover_wrapper(func_name):
    original_func = getattr(lavalink.DefaultPlayer, func_name)
    
    async def wrapper(self, *args, **kwargs):
        try:
            return await original_func(self, *args, **kwargs)
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
            print(f"[Music Failover] Connection/HTTP error in player.{func_name} for guild {self.guild_id}: {e}. Attempting node failover...")
            available_nodes = [n for n in self.client.node_manager.nodes if n.available and n != self.node]
            if available_nodes:
                new_node = random.choice(available_nodes)
                print(f"[Music Failover] Moving player in guild {self.guild_id} from '{self.node.name}' to '{new_node.name}'")
                try:
                    await self.change_node(new_node)
                    # Retry the original operation on the new node
                    return await getattr(self, func_name)(*args, **kwargs)
                except Exception as fe:
                    print(f"[Music Failover] Failed to retry player.{func_name} after moving to node '{new_node.name}': {fe}")
                    raise fe
            else:
                print(f"[Music Failover] No other nodes available for failover to handle exception in player.{func_name}: {e}")
                raise e
    return wrapper

for method_name in ['play', 'stop', 'handle_event', 'set_pause', 'set_volume', 'seek', 'set_filter', 'clear_filters']:
    if hasattr(lavalink.DefaultPlayer, method_name):
        setattr(lavalink.DefaultPlayer, method_name, make_auto_failover_wrapper(method_name))


from utils.helpers import format_time, truncate, send_log_webhook


# node list lives in config.py (Config.LAVALINK_NODES)

URL_REGEX = re.compile(r"https?://(?:www\.)?.+")

MAX_QUEUE_DISPLAY = 10


# helpers

def make_text_container(text: str) -> ui.LayoutView:
    view = ui.LayoutView()
    container = ui.Container(accent_colour=None)
    container.add_item(ui.TextDisplay(text))
    view.add_item(container)
    return view


def format_duration(ms: int) -> str:
    if not ms or ms <= 0:
        return "00:00"
    s = int(ms / 1000)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def get_source_emoji_from_uri(uri: str) -> str:
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


def get_source_name_from_uri(uri: str) -> str:
    if not uri:
        return "other"
    u = uri.lower()
    if "spotify" in u:
        return "spotify"
    elif "apple" in u:
        return "apple"
    elif "soundcloud" in u:
        return "soundcloud"
    elif "youtube" in u or "youtu.be" in u:
        return "youtube"
    return "other"


def resolve_track_artwork(uri: str = "", identifier: str = "", artwork_url: str = None) -> str:
    if artwork_url and str(artwork_url).startswith("http"):
        return artwork_url

    raw_uri = uri or ""
    ident = identifier or ""
    if not ident:
        if "v=" in raw_uri:
            ident = raw_uri.split("v=")[1].split("&")[0]
        elif "youtu.be/" in raw_uri:
            ident = raw_uri.split("youtu.be/")[1].split("?")[0]

    if ident:
        return f"https://img.youtube.com/vi/{ident}/hqdefault.jpg"
    return None


def make_thumbnail(url: str):
    """Create a Thumbnail component with proper UnfurledMediaItem."""
    return ui.Thumbnail(media=discord.UnfurledMediaItem(url=url))



# lavalink voice client

class LavalinkVoiceClient(discord.VoiceProtocol):
    """VoiceProtocol implementation for lavalink.py v5+"""

    def __init__(self, client: discord.Client, channel: discord.abc.Connectable):
        self.client = client
        self.channel = channel
        self._guild = channel.guild
        self._destroyed = False

        if not hasattr(client, "lavalink") or client.lavalink is None:
            raise RuntimeError("Lavalink client not initialized on bot!")

        self.lavalink: lavalink.Client = client.lavalink

    async def on_voice_server_update(self, data: dict):
        lavalink_data = {
            "t": "VOICE_SERVER_UPDATE",
            "d": {
                "guild_id": str(self._guild.id),
                "token": data["token"],
                "endpoint": data["endpoint"],
            }
        }
        await self.lavalink.voice_update_handler(lavalink_data)

    async def on_voice_state_update(self, data: dict):
        channel_id = data.get("channel_id")

        if not channel_id:
            self.cleanup()
            player = self.lavalink.player_manager.get(self._guild.id)
            if player:
                player.channel_id = None
            return

        channel = self._guild.get_channel(int(channel_id))
        if channel:
            self.channel = channel

        lavalink_data = {
            "t": "VOICE_STATE_UPDATE",
            "d": {
                "guild_id": str(self._guild.id),
                "user_id": str(self.client.user.id),
                "channel_id": channel_id,
                "session_id": data.get("session_id", ""),
            }
        }
        await self.lavalink.voice_update_handler(lavalink_data)

    async def connect(self, *, timeout: float, reconnect: bool,
                      self_deaf: bool = True, self_mute: bool = False) -> None:
        player = self.lavalink.player_manager.create(guild_id=self._guild.id)
        player.delete("voluntary_disconnect")
        await self._guild.change_voice_state(
            channel=self.channel,
            self_mute=self_mute,
            self_deaf=self_deaf,
        )

    async def disconnect(self, *, force: bool = False) -> None:
        player = self.lavalink.player_manager.get(self._guild.id)

        if not force and player and not player.is_connected:
            return

        try:
            await asyncio.wait_for(self._guild.change_voice_state(channel=None), timeout=3.0)
        except Exception:
            pass

        self.cleanup()

        if not self._destroyed:
            self._destroyed = True
            try:
                await asyncio.wait_for(self.lavalink.player_manager.destroy(self._guild.id), timeout=3.0)
            except Exception:
                pass

    async def move_to(self, channel: discord.abc.Connectable):
        await self._guild.change_voice_state(channel=channel)
        self.channel = channel

    def is_connected(self) -> bool:
        """Check if voice client is connected."""
        return self.channel is not None and not self._destroyed


# now playing layout

class NowPlayingLayout(ui.LayoutView):
    def __init__(self, cog, guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.message: discord.Message = None
        self.card_buf = None

    async def build(self, player):
        self.clear_items()
        self.card_buf = None
        track = player.current

        if not track:
            container = ui.Container(accent_colour=None)
            container.add_item(ui.TextDisplay(f"{emojis.ERROR} Nothing is playing."))
            self.add_item(container)
            return self

        src = get_source_emoji_from_uri(track.uri)
        status = "Paused" if player.paused else "Playing"
        req_user = self.cog.bot.get_user(track.requester)
        req_str = str(req_user) if req_user else "Unknown"

        container = ui.Container(accent_colour=0x5865F2)

        bot_user = self.cog.bot.user
        header_text = ui.TextDisplay(f"### {emojis.PLAYING} Now Playing\n-# Requested by {req_str}")
        try:
            header_section = ui.Section(
                header_text,
                accessory=ui.Thumbnail(media=discord.UnfurledMediaItem(url=str(bot_user.display_avatar.url)))
            )
            container.add_item(header_section)
        except Exception:
            container.add_item(header_text)

        container.add_item(ui.Separator())

        # Track link & Live Discord Timer
        import time
        now_ts = int(time.time())
        rem_sec = max(0, int((track.duration - player.position) / 1000))
        end_ts = now_ts + rem_sec

        if not player.paused and track.duration > 0 and not track.is_stream:
            time_info = f"⏳ **Ends:** <t:{end_ts}:R>"
        else:
            time_info = f"⏸️ **Status:** `Paused`" if player.paused else "🔴 **Live Stream**"

        container.add_item(ui.TextDisplay(f"{src} **[{track.title}]({track.uri})**\n{time_info}"))

        # Dynamic PIL Canvas Banner for Now Playing
        try:
            thumb_url = self.cog._get_thumbnail(track)
            art_bytes = await fetch_image_bytes(thumb_url) if thumb_url else None
            bot_name = bot_user.name if bot_user else "ECHO"
            pct = player.position / max(1, track.duration)

            self.card_buf = generate_music_banner(
                title=track.title,
                author=track.author,
                artwork_bytes=art_bytes,
                badge_text="PAUSED" if player.paused else "NOW PLAYING",
                bot_name=bot_name,
                sub_info=f"{format_duration(player.position)} / {format_duration(track.duration)}",
                progress_pct=pct
            )

            gallery = ui.MediaGallery()
            gallery.add_item(media=discord.UnfurledMediaItem(url="attachment://np_banner.png"))
            container.add_item(gallery)
        except Exception as e:
            print(f"[NP Canvas Error] {e}")

        container.add_item(ui.Separator())

        # Row 0: Filter Select Dropdown
        row_filter = ui.ActionRow()
        row_filter.add_item(NPFilterSelect(self))
        container.add_item(row_filter)

        # Row 1
        row1 = ui.ActionRow()
        pause_label = "Resume" if player.paused else "Pause"
        pause_emoji = emojis.BTN_RESUME if player.paused else emojis.BTN_PAUSE
        row1.add_item(NPButton("pause", pause_label, pause_emoji,
                               discord.ButtonStyle.primary, self))
        row1.add_item(NPButton("skip", "Skip", emojis.BTN_SKIP,
                               discord.ButtonStyle.secondary, self))
        row1.add_item(NPButton("stop", "Stop", emojis.BTN_STOP,
                               discord.ButtonStyle.danger, self))
        container.add_item(row1)

        # Row 2
        row2 = ui.ActionRow()
        loop_style = (discord.ButtonStyle.success if player.loop
                      else discord.ButtonStyle.secondary)
        row2.add_item(NPButton("loop", "Loop", emojis.BTN_LOOP, loop_style, self))

        ap_on = self.cog.autoplay_states.get(self.guild_id, False)
        ap_style = (discord.ButtonStyle.success if ap_on
                    else discord.ButtonStyle.secondary)
        row2.add_item(NPButton("autoplay", "Autoplay", emojis.BTN_AUTOPLAY, ap_style, self))

        row2.add_item(NPButton("add_playlist", "Playlist", "➕", discord.ButtonStyle.secondary, self))
        container.add_item(row2)

        self.add_item(container)
        return self


class NPFilterSelect(ui.Select):
    def __init__(self, layout):
        self.layout = layout
        player = layout.cog.lavalink.player_manager.get(layout.guild_id) if (layout.cog and layout.cog.lavalink) else None
        active_filter = player.fetch("active_filter", "clear") if player else "clear"

        options = [
            discord.SelectOption(label="Clear Filters", value="clear", description="Reset all audio filters to normal", emoji="⚙️", default=(active_filter == "clear")),
            discord.SelectOption(label="Bassboost", value="bassboost", description="Enhanced heavy bass equalizer", emoji="🔊", default=(active_filter == "bassboost")),
            discord.SelectOption(label="Nightcore", value="nightcore", description="Faster speed & pitch shift", emoji="⚡", default=(active_filter == "nightcore")),
            discord.SelectOption(label="Vaporwave", value="vaporwave", description="Slower relaxed lofi speed & pitch", emoji="🌊", default=(active_filter == "vaporwave")),
            discord.SelectOption(label="8D Audio", value="8d", description="Spatial 3D surround sound panning", emoji="🎧", default=(active_filter == "8d")),
            discord.SelectOption(label="Karaoke", value="karaoke", description="Vocal suppressor filter", emoji="🎤", default=(active_filter == "karaoke")),
            discord.SelectOption(label="Treble Boost", value="treble", description="Enhanced high-frequency equalizer", emoji="🎼", default=(active_filter == "treble")),
        ]
        super().__init__(placeholder="🎛️ Select Audio Filter...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        cog = self.layout.cog
        player = cog.lavalink.player_manager.get(interaction.guild.id)
        if not player:
            return await interaction.followup.send(f"{emojis.ERROR} Player not found.", ephemeral=True)

        if not interaction.user.voice or not interaction.guild.voice_client or interaction.user.voice.channel != interaction.guild.voice_client.channel:
            return await interaction.followup.send("You must be in the same voice channel.", ephemeral=True)

        allowed, dj_msg = await cog._check_dj_permission(interaction.user, interaction.guild)
        if not allowed:
            return await interaction.followup.send(f"{emojis.ERROR} {dj_msg}", ephemeral=True)

        filter_choice = self.values[0]
        ok, msg = await cog.apply_filter(interaction.guild.id, filter_choice, requester=interaction.user)

        await self.layout.build(player)
        if getattr(self.layout, 'card_buf', None):
            file = discord.File(fp=self.layout.card_buf, filename="np_banner.png")
            await interaction.edit_original_response(view=self.layout, attachments=[file])
        else:
            await interaction.edit_original_response(view=self.layout, attachments=[])

        if ok:
            await interaction.followup.send(f"🎛️ {msg}", ephemeral=True)
        else:
            await interaction.followup.send(f"{emojis.ERROR} {msg}", ephemeral=True)


class NPButton(ui.Button):
    def __init__(self, action: str, label: str, emoji_str: str,
                 style: discord.ButtonStyle, layout: NowPlayingLayout):
        super().__init__(label=label, emoji=emoji_str, style=style)
        self.action = action
        self.layout = layout

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        cog = self.layout.cog
        player = cog.lavalink.player_manager.get(interaction.guild.id)

        if not player:
            return await interaction.followup.send(
                f"{emojis.ERROR} Player not found.", ephemeral=True
            )

        if not interaction.user.voice or not interaction.guild.voice_client:
            return await interaction.followup.send(
                "You must be in the same voice channel.", ephemeral=True
            )

        if interaction.user.voice.channel != interaction.guild.voice_client.channel:
            return await interaction.followup.send(
                "You must be in the same voice channel.", ephemeral=True
            )

        allowed, dj_msg = await cog._check_dj_permission(interaction.user, interaction.guild)
        if not allowed:
            return await interaction.followup.send(
                f"{emojis.ERROR} {dj_msg}", ephemeral=True
            )

        if self.action == "pause":
            new_pause = not player.paused
            player.delete("auto_paused_empty_vc")
            await player.set_pause(new_pause)
            if player.current:
                if new_pause:
                    await cog._update_vc_status(interaction.guild.id, f"{emojis.BTN_PAUSE} Paused: {player.current.title}")
                else:
                    await cog._update_vc_status(interaction.guild.id, f"{emojis.MYMUSIC} {player.current.title}")
            await self.layout.build(player)
            if getattr(self.layout, 'card_buf', None):
                file = discord.File(fp=self.layout.card_buf, filename="np_banner.png")
                await interaction.edit_original_response(view=self.layout, attachments=[file])
            else:
                await interaction.edit_original_response(view=self.layout, attachments=[])

        elif self.action == "skip":
            await player.skip()
            await interaction.followup.send(
                f"{emojis.BTN_SKIP} Skipped", ephemeral=True
            )

        elif self.action == "stop":
            player.queue.clear()
            await player.stop()
            cog.now_playing_messages.pop(interaction.guild.id, None)
            
            is_247 = await cog.bot.db.get_247(interaction.guild.id)
            if not is_247:
                cog._start_idle_timer(interaction.guild.id)
            else:
                await cog._update_vc_status(interaction.guild.id, f"{emojis.INFO} Use **Ep <song>**")

            await interaction.followup.send(
                f"{emojis.BTN_STOP} Playback stopped and queue cleared.", ephemeral=True
            )

        elif self.action == "loop":
            player.set_loop(0 if player.loop else 1)
            await self.layout.build(player)
            if getattr(self.layout, 'card_buf', None):
                file = discord.File(fp=self.layout.card_buf, filename="np_banner.png")
                await interaction.edit_original_response(view=self.layout, attachments=[file])
            else:
                await interaction.edit_original_response(view=self.layout, attachments=[])
            await interaction.followup.send(
                f"Loop: **{'On' if player.loop else 'Off'}**", ephemeral=True
            )

        elif self.action == "autoplay":
            current = cog.autoplay_states.get(interaction.guild.id, False)
            cog.autoplay_states[interaction.guild.id] = not current
            await self.layout.build(player)
            if getattr(self.layout, 'card_buf', None):
                file = discord.File(fp=self.layout.card_buf, filename="np_banner.png")
                await interaction.edit_original_response(view=self.layout, attachments=[file])
            else:
                await interaction.edit_original_response(view=self.layout, attachments=[])
            await interaction.followup.send(
                f"Autoplay: **{'Enabled' if not current else 'Disabled'}**",
                ephemeral=True
            )

        elif self.action == "add_playlist":
            if not player.current:
                return await interaction.followup.send(f"{emojis.ERROR} No track is currently playing.", ephemeral=True)

            playlists = await cog.bot.db.get_playlists(interaction.user.id)
            if not playlists:
                view = make_text_container(
                    f"{emojis.ERROR} You don't have any playlists yet!\n"
                    f"{emojis.DOT} Create one using `/playlist create <name>` or `!playlist create <name>`."
                )
                return await interaction.followup.send(view=view, ephemeral=True)

            if len(playlists) == 1:
                pl_id, pl_name, _, _, _, _ = playlists[0]
                t = player.current
                await cog.bot.db.add_to_playlist(pl_id, t.title, t.author, t.uri, t.identifier)
                view = make_text_container(f"{emojis.SUCCESS} Added **[{t.title}]({t.uri})** to your playlist **{pl_name}**!")
                return await interaction.followup.send(view=view, ephemeral=True)

            select_view = SaveToPlaylistSelectView(cog, player.current, playlists, interaction.user.id)
            return await interaction.followup.send(view=select_view, ephemeral=True)

        # Every in-Discord control mutates player/autoplay state, so push
        # the change to any connected dashboard clients too — otherwise
        # the website goes stale the moment someone uses these buttons.
        await cog._notify_dashboard(interaction.guild.id)


class SaveToPlaylistSelectView(ui.LayoutView):
    def __init__(self, cog, track, playlists, user_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.track = track
        self.playlists = playlists
        self.user_id = user_id

        container = ui.Container(accent_colour=0x5865F2)
        container.add_item(ui.TextDisplay(f"### 📃 **Save to Playlist**\n-# Choose which playlist to add **{track.title}** to:"))
        container.add_item(ui.Separator())

        row = ui.ActionRow()
        options = []
        for p in playlists[:25]:
            pl_id, pl_name, _, track_count, code, _ = p
            options.append(discord.SelectOption(
                label=pl_name,
                value=str(pl_id),
                description=f"{track_count} tracks • Code: {code}",
                emoji="📃"
            ))
        row.add_item(SaveToPlaylistDropdown(self, options))
        container.add_item(row)
        self.add_item(container)


class SaveToPlaylistDropdown(ui.Select):
    def __init__(self, parent_view, options):
        self.parent_view = parent_view
        super().__init__(placeholder="Select a playlist...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.user_id:
            return await interaction.response.send_message("This menu is for another user.", ephemeral=True)

        chosen_id = int(self.values[0])
        track = self.parent_view.track

        pl_name = "Playlist"
        for p in self.parent_view.playlists:
            if p[0] == chosen_id:
                pl_name = p[1]
                break

        await self.parent_view.cog.bot.db.add_to_playlist(chosen_id, track.title, track.author, track.uri, track.identifier)
        view = make_text_container(f"{emojis.SUCCESS} Added **[{track.title}]({track.uri})** to your playlist **{pl_name}**!")
        await interaction.response.edit_message(view=view)


# ── Server Music Statistics Layout ──

class MusicStatsSelect(ui.Select):
    def __init__(self, bot, ctx, layout):
        self.bot = bot
        self.ctx = ctx
        self.layout = layout
        options = [
            discord.SelectOption(label="Overview", value="overview", description="Server playback summary & top highlights", emoji="📊"),
            discord.SelectOption(label="Tracks", value="tracks", description="Most played tracks in this server", emoji="🎵"),
            discord.SelectOption(label="Artists", value="artists", description="Top played artists & creators", emoji="🎤"),
            discord.SelectOption(label="Listeners", value="listeners", description="Top listeners & DJ members", emoji="🎧"),
            discord.SelectOption(label="Sources", value="sources", description="Platform playback distribution", emoji="📻"),
        ]
        super().__init__(placeholder="Overview", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("This statistics menu was requested by another user.", ephemeral=True)
        self.layout.current = self.values[0]
        await self.layout.rebuild()
        await interaction.response.edit_message(view=self.layout)


class MusicStatsTabBtn(ui.Button):
    def __init__(self, tab_id: str, label: str, layout, style=discord.ButtonStyle.secondary):
        super().__init__(label=label, style=style)
        self.tab_id = tab_id
        self.layout = layout

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.layout.ctx.author.id:
            return await interaction.response.send_message("This statistics menu was requested by another user.", ephemeral=True)
        self.layout.current = self.tab_id
        await self.layout.rebuild()
        await interaction.response.edit_message(view=self.layout)


class MusicStatsLayout(ui.LayoutView):
    def __init__(self, bot, ctx):
        super().__init__(timeout=120)
        self.bot = bot
        self.ctx = ctx
        self.current = "overview"
        self.message = None

    async def init_build(self):
        await self.rebuild()

    async def rebuild(self):
        self.clear_items()
        container = ui.Container(accent_colour=0x5865F2)

        guild = self.ctx.guild

        # Top Title Header with Server Icon accessory inside ui.Section (ONLY if server logo exists)
        header_text = ui.TextDisplay(f"### Music Statistics — {guild.name}")
        if guild.icon:
            try:
                header_section = ui.Section(
                    header_text,
                    accessory=ui.Thumbnail(media=discord.UnfurledMediaItem(url=str(guild.icon.url)))
                )
                container.add_item(header_section)
            except Exception:
                container.add_item(header_text)
        else:
            container.add_item(header_text)

        container.add_item(ui.Separator())

        if self.current == "overview":
            await self._build_overview(container)
        elif self.current == "tracks":
            await self._build_tracks(container)
        elif self.current == "artists":
            await self._build_artists(container)
        elif self.current == "listeners":
            await self._build_listeners(container)
        else:
            await self._build_sources(container)

        container.add_item(ui.Separator())

        # Select menu row
        row_select = ui.ActionRow()
        row_select.add_item(MusicStatsSelect(self.bot, self.ctx, self))
        container.add_item(row_select)

        # 5 Tab buttons row: Overview, Tracks, Artists, Listeners, Sources
        row_btns = ui.ActionRow()
        tabs = [
            ("overview", "Overview"),
            ("tracks", "Tracks"),
            ("artists", "Artists"),
            ("listeners", "Listeners"),
            ("sources", "Sources"),
        ]
        for tab_id, label in tabs:
            btn_style = discord.ButtonStyle.primary if self.current == tab_id else discord.ButtonStyle.secondary
            row_btns.add_item(MusicStatsTabBtn(tab_id, label, self, style=btn_style))

        container.add_item(row_btns)
        self.add_item(container)

    async def _build_overview(self, container):
        guild_id = self.ctx.guild.id
        stats = await self.bot.db.get_guild_music_stats(guild_id)

        total_tracks = stats["total_tracks"]
        total_ms = stats["total_duration_ms"]
        unique_listeners = stats["unique_listeners"]
        avg_per_listener = round(total_tracks / max(1, unique_listeners), 1) if unique_listeners > 0 else 0

        # Format listening time (hours, minutes, seconds)
        s = int(total_ms / 1000)
        h, r = divmod(s, 3600)
        m, s = divmod(r, 60)
        time_str = f"{h}h {m}m {s}s" if h > 0 else f"{m}m {s}s"

        overview_metrics = (
            f"• **Total Tracks Played:** `{total_tracks}`\n"
            f"• **Total Listening Time:** `{time_str}`\n"
            f"• **Unique Listeners:** `{unique_listeners}`\n"
            f"• **Average / Listener:** `{avg_per_listener}` tracks"
        )
        container.add_item(ui.TextDisplay(overview_metrics))

        top_track = stats["top_track"]
        top_artist = stats["top_artist"]
        top_listener = stats["top_listener"]

        highlights = []
        if top_track:
            highlights.append(
                f"**Most Played Track:**\n"
                f"| [{top_track['title']}]({top_track['uri']}) — `{top_track['author']}` ({top_track['plays']} plays)"
            )
        if top_artist:
            highlights.append(
                f"**Top Artist:**\n"
                f"| `{top_artist['name']}` ({top_artist['plays']} plays)"
            )
        if top_listener:
            user_mention = f"<@{top_listener['user_id']}>" if top_listener['user_id'] else "Unknown"
            highlights.append(
                f"**Top Listener:**\n"
                f"| {user_mention} ({top_listener['plays']} plays)"
            )

        if highlights:
            container.add_item(ui.Separator())
            container.add_item(ui.TextDisplay("\n\n".join(highlights)))

        # Display Media Gallery banner of top track (original cover art banner)
        if top_track:
            banner_url = resolve_track_artwork(
                uri=top_track.get("uri"),
                identifier=top_track.get("identifier"),
                artwork_url=top_track.get("artwork_url")
            )

            if banner_url:
                try:
                    gallery = ui.MediaGallery()
                    gallery.add_item(media=discord.UnfurledMediaItem(url=banner_url))
                    container.add_item(gallery)
                except Exception as e:
                    print(f"[MusicStats MediaGallery Error] {e}")

    async def _build_tracks(self, container):
        guild_id = self.ctx.guild.id
        top_tracks = await self.bot.db.get_guild_top_tracks(guild_id, limit=10)

        container.add_item(ui.TextDisplay(f"### 🎵 **Most Played Tracks in {self.ctx.guild.name}**"))
        container.add_item(ui.Separator())

        if not top_tracks:
            container.add_item(ui.TextDisplay(f"{emojis.INFO} No tracks recorded yet in this server."))
            return

        lines = []
        for i, t in enumerate(top_tracks, start=1):
            src = get_source_emoji_from_uri(t['uri'])
            lines.append(f"**{i}.** {src} [{t['title']}]({t['uri']}) — `{t['author']}` (`{t['plays']}` plays)")

        container.add_item(ui.TextDisplay("\n".join(lines)))

    async def _build_artists(self, container):
        guild_id = self.ctx.guild.id
        top_artists = await self.bot.db.get_guild_top_artists(guild_id, limit=10)

        container.add_item(ui.TextDisplay(f"### 🎤 **Top Artists in {self.ctx.guild.name}**"))
        container.add_item(ui.Separator())

        if not top_artists:
            container.add_item(ui.TextDisplay(f"{emojis.INFO} No artist data recorded yet in this server."))
            return

        lines = []
        for i, a in enumerate(top_artists, start=1):
            lines.append(f"**{i}.** 🎤 **`{a['author']}`** — `{a['plays']}` plays")

        container.add_item(ui.TextDisplay("\n".join(lines)))

    async def _build_listeners(self, container):
        guild_id = self.ctx.guild.id
        top_listeners = await self.bot.db.get_guild_top_listeners(guild_id, limit=10)

        container.add_item(ui.TextDisplay(f"### 🎧 **Top Listeners in {self.ctx.guild.name}**"))
        container.add_item(ui.Separator())

        if not top_listeners:
            container.add_item(ui.TextDisplay(f"{emojis.INFO} No listener data recorded yet in this server."))
            return

        lines = []
        for i, l in enumerate(top_listeners, start=1):
            u_mention = f"<@{l['user_id']}>" if l['user_id'] else "Unknown"
            lines.append(f"**{i}.** 🎧 {u_mention} — `{l['plays']}` tracks requested")

        container.add_item(ui.TextDisplay("\n".join(lines)))

    async def _build_sources(self, container):
        guild_id = self.ctx.guild.id
        top_sources = await self.bot.db.get_guild_top_sources(guild_id)

        container.add_item(ui.TextDisplay(f"### 📻 **Music Platforms & Sources in {self.ctx.guild.name}**"))
        container.add_item(ui.Separator())

        if not top_sources:
            container.add_item(ui.TextDisplay(f"{emojis.INFO} No platform source data recorded yet."))
            return

        source_emojis = {
            "youtube": emojis.YOUTUBE,
            "spotify": emojis.SPOTIFY,
            "soundcloud": emojis.SOUNDCLOUD,
            "apple": emojis.APPLE_MUSIC,
            "other": emojis.MUSIC
        }
        source_names = {
            "youtube": "YouTube",
            "spotify": "Spotify",
            "soundcloud": "SoundCloud",
            "apple": "Apple Music",
            "other": "Other / Direct"
        }

        total_plays = sum(s['plays'] for s in top_sources)
        lines = []
        for s in top_sources:
            src_key = s['source']
            e = source_emojis.get(src_key, emojis.MUSIC)
            name = source_names.get(src_key, src_key.capitalize())
            pct = (s['plays'] / max(1, total_plays)) * 100
            lines.append(f"{e} **{name}:** `{s['plays']}` plays (`{pct:.1f}%`)")

        container.add_item(ui.TextDisplay("\n".join(lines)))



class Music(commands.Cog, name="Music"):
    """Music commands — play from YouTube, Spotify, Apple Music."""

    def __init__(self, bot):
        self.bot = bot
        self.lavalink: lavalink.Client = None
        self.now_playing_messages: dict[int, discord.Message] = {}
        self.autoplay_states: dict[int, bool] = {}
        self.recent_tracks: dict[int, list] = {}
        self.history: dict[int, list] = {}  # guild_id -> [track, ...] most-recent last, for Previous
        self._last_announced: dict[int, str] = {}
        self.idle_tasks: dict[int, asyncio.Task] = {}
        self._node_ready = asyncio.Event()
        self.thumbnail_cache: dict[str, str] = {}
        bot.loop.create_task(self._init_lavalink())

    def cog_unload(self):
        if self.lavalink:
            try:
                self.lavalink._event_hooks.clear()
            except Exception:
                pass
        for task in self.idle_tasks.values():
            if not task.done():
                task.cancel()

    # ─── Lavalink Init ───

    async def _init_lavalink(self):
        await self.bot.wait_until_ready()

        if not hasattr(self.bot, "lavalink") or self.bot.lavalink is None:
            self.bot.lavalink = lavalink.Client(self.bot.user.id)
            print(f"[Music] Lavalink client created for bot ID {self.bot.user.id}")

        self.lavalink = self.bot.lavalink

        # Clear old hooks
        try:
            for k in list(self.lavalink._event_hooks.keys()):
                self.lavalink._event_hooks[k] = [
                    h for h in self.lavalink._event_hooks[k]
                    if not isinstance(getattr(h, "__self__", None), Music)
                ]
        except Exception:
            pass

        self.lavalink.add_event_hooks(self)

        # Add nodes
        for node in Config.LAVALINK_NODES:
            try:
                existing = [
                    n for n in self.lavalink.node_manager.nodes
                    if n.name == node["name"]
                ]
                if existing:
                    continue

                self.lavalink.add_node(
                    host=node["host"],
                    port=node["port"],
                    password=node["password"],
                    region=node["region"],
                    name=node["name"],
                    ssl=node.get("ssl", False),
                )
                print(f"[Music] Added node '{node['name']}' ({node['host']}:{node['port']})")
            except Exception as e:
                print(f"[Music] Failed to add node '{node['name']}': {e}")

        # Wait for connection
        print("[Music] Waiting for Lavalink node...")
        for attempt in range(60):
            if self.lavalink.node_manager.available_nodes:
                self._node_ready.set()
                print(f"[Music] Node ready after {attempt + 1}s!")
                self.bot.loop.create_task(self._auto_reconnect_247())
                return
            await asyncio.sleep(1)

        print("[Music] WARNING: No Lavalink nodes connected after 60s!")

    async def _auto_reconnect_247(self):
        await asyncio.sleep(2)  # brief delay to let bot settle
        try:
            guilds_247 = await self.bot.db.get_all_247_guilds()
        except Exception as e:
            print(f"[AutoReconnect] Failed to fetch 24/7 guilds: {e}")
            return

        if not guilds_247:
            return

        print(f"[AutoReconnect] Found {len(guilds_247)} guild(s) with active 24/7 settings.")
        for guild_id, channel_id in guilds_247:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                print(f"[AutoReconnect] Guild {guild_id} not found/cached.")
                continue

            channel = guild.get_channel(channel_id)
            if not channel:
                print(f"[AutoReconnect] Channel {channel_id} not found in guild '{guild.name}'.")
                continue

            if guild.voice_client:
                print(f"[AutoReconnect] Already connected to voice in guild '{guild.name}'.")
                continue

            try:
                player = self.lavalink.player_manager.create(guild_id)
                player.store("channel", None)
                print(f"[AutoReconnect] Reconnecting to voice channel '{channel.name}' in '{guild.name}'...")
                await channel.connect(cls=LavalinkVoiceClient, self_deaf=True)
                await self._update_vc_status(guild_id, f"{emojis.INFO} Use **Ep <song>**")
                print(f"[AutoReconnect] Connected successfully in '{guild.name}'.")
            except Exception as e:
                print(f"[AutoReconnect] Failed to connect in '{guild.name}': {e}")

    async def _reconnect_247_vc(self, guild_id: int, channel: discord.VoiceChannel):
        # Wait a brief moment to let Discord connection settle
        await asyncio.sleep(2.0)
        
        guild = self.bot.get_guild(guild_id)
        if not guild or guild.voice_client:
            return

        print(f"[24/7 Reconnect] Bot was kicked/disconnected from voice in server '{guild.name}'. Auto-rejoining channel '{channel.name}'...")
        try:
            player = self.lavalink.player_manager.create(guild_id)
            player.store("channel", None)
            player.delete("voluntary_disconnect")
            await channel.connect(cls=LavalinkVoiceClient, self_deaf=True)
            await self._update_vc_status(guild_id, f"{emojis.INFO} Use **Ep <song>**")
            print(f"[24/7 Reconnect] Reconnected successfully in '{guild.name}'.")
        except Exception as e:
            print(f"[24/7 Reconnect] Failed to auto-rejoin in '{guild.name}': {e}")

    # ─── Voice cleanup ───

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                     before: discord.VoiceState,
                                     after: discord.VoiceState):
        guild_id = member.guild.id

        # ── 1. Bot's own voice state update ──
        if member.id == self.bot.user.id:
            if before.channel and not after.channel:
                await self._clear_vc_status(guild_id)
                player = self.lavalink.player_manager.get(guild_id) if self.lavalink else None
                if player:
                    player.channel_id = None
                
                is_247 = await self.bot.db.get_247(guild_id)
                if is_247:
                    channel_id = await self.bot.db.get_247_channel(guild_id)
                    voluntary = player.fetch("voluntary_disconnect") if player else False
                    if channel_id and not voluntary:
                        channel = member.guild.get_channel(channel_id)
                        if channel:
                            self.bot.loop.create_task(self._reconnect_247_vc(guild_id, channel))
                else:
                    await self.bot.db.set_247_channel(guild_id, None)
            elif after.channel:
                is_247 = await self.bot.db.get_247(guild_id)
                if is_247:
                    await self.bot.db.set_247_channel(guild_id, after.channel.id)

                # Check if the bot was dragged/moved to another voice channel
                if before.channel and before.channel.id != after.channel.id:
                    if member.guild.voice_client:
                        try:
                            # Sync connection state to the new channel (forces Voice Server handshake update)
                            await member.guild.voice_client.move_to(after.channel)
                            
                            # Sync player channel ID
                            if self.lavalink:
                                player = self.lavalink.player_manager.get(guild_id)
                                if player:
                                    player.channel_id = after.channel.id
                                    
                                    # Clear all old voice channel statuses
                                    await self._clear_vc_status(guild_id)
                                    
                                    # Set status for the new voice channel
                                    if player.current:
                                        if player.paused:
                                            status_text = f"{emojis.BTN_PAUSE} Paused: {player.current.title}"
                                        else:
                                            status_text = f"{emojis.MYMUSIC} {player.current.title}"
                                        await self._update_vc_status(guild_id, status_text)
                                    else:
                                        is_247 = await self.bot.db.get_247(guild_id)
                                        if is_247:
                                            await self._update_vc_status(guild_id, f"{emojis.INFO} Use **Ep <song>**")
                        except Exception as e:
                            print(f"[Voice Move] Error syncing voice state on drag: {e}")
            return

        # ── 2. Human members joining/leaving bot's voice channel ──
        vc = member.guild.voice_client
        if not vc or not vc.channel:
            return

        # Check if member moved, left, or joined the channel bot is in
        if (before.channel and before.channel.id == vc.channel.id) or (after.channel and after.channel.id == vc.channel.id):
            human_members = [m for m in vc.channel.members if not m.bot]
            player = self.lavalink.player_manager.get(guild_id) if self.lavalink else None
            if not player:
                return

            if len(human_members) == 0:
                # All human users left the VC
                if player.is_playing and not player.paused:
                    await player.set_pause(True)
                    player.store("auto_paused_empty_vc", True)
                    if player.current:
                        await self._update_vc_status(guild_id, f"{emojis.BTN_PAUSE} Paused: {player.current.title}")
                    await self._update_now_playing(guild_id)
                    await self._notify_dashboard(guild_id)
                elif player.paused and player.current:
                    await self._update_vc_status(guild_id, f"{emojis.BTN_PAUSE} Paused: {player.current.title}")
            else:
                # Human user(s) present in VC — unpause if it was auto-paused
                if player.paused and player.fetch("auto_paused_empty_vc"):
                    player.delete("auto_paused_empty_vc")
                    await player.set_pause(False)
                    if player.current:
                        await self._update_vc_status(guild_id, f"{emojis.MYMUSIC} {player.current.title}")
                    await self._update_now_playing(guild_id)
                    await self._notify_dashboard(guild_id)

    # ─── Lavalink Events ───

    @lavalink.listener(lavalink.events.NodeConnectedEvent)
    async def on_node_connected(self, event):
        self._node_ready.set()
        print(f"[Music] Node '{event.node.name}' connected!")

    @lavalink.listener(lavalink.events.NodeDisconnectedEvent)
    async def on_node_disconnected(self, event):
        if not self.lavalink.node_manager.available_nodes:
            self._node_ready.clear()
        print(f"[Music] Node '{event.node.name}' disconnected!")

        # Failover players assigned to the disconnected node to another healthy node
        available_nodes = [n for n in self.lavalink.node_manager.nodes if n.available and n != event.node]
        if available_nodes:
            for player in list(self.lavalink.player_manager.values()):
                if player.node == event.node:
                    new_node = random.choice(available_nodes)
                    print(f"[Music] Failover: Moving player in guild {player.guild_id} from disconnected node '{event.node.name}' to '{new_node.name}'")
                    try:
                        await player.change_node(new_node)
                    except Exception as e:
                        print(f"[Music] Failed to move player in guild {player.guild_id}: {e}")

    async def _notify_dashboard(self, guild_id: int):
        """Push a live update to any connected dashboard clients for this
        guild. No-op if the dashboard isn't running."""
        broadcaster = getattr(self.bot, "dashboard_broadcast", None)
        if broadcaster:
            try:
                await broadcaster(guild_id)
            except Exception as e:
                print(f"[Dashboard] broadcast failed: {e}")

    @lavalink.listener(lavalink.events.TrackStartEvent)
    async def on_track_start(self, event: lavalink.events.TrackStartEvent):
        player = event.player
        guild_id = int(player.guild_id)
        track = event.track

        # Pre-cache thumbnail as soon as track starts
        artwork_url = await self._cache_thumbnail(track)
        if not artwork_url:
            artwork_url = self._get_thumbnail(track)

        requester_id = getattr(track, "requester", 0) or 0
        duration = getattr(track, "duration", 0) or 0
        source = get_source_name_from_uri(track.uri)

        # Record track play in database for stats & history
        self.bot.loop.create_task(self.bot.db.record_song_play(
            guild_id=guild_id,
            title=track.title,
            author=track.author,
            uri=track.uri,
            identifier=track.identifier,
            user_id=requester_id,
            duration=duration,
            source=source,
            artwork_url=artwork_url
        ))

        # Webhook Song Play Log
        try:
            requester_id = getattr(track, "requester", None)
            requester_mention = "Unknown User"
            if requester_id:
                requester_user = self.bot.get_user(requester_id)
                if requester_user:
                    requester_mention = f"{requester_user.mention} (`{requester_user.name}`)"
                else:
                    requester_mention = f"<@{requester_id}>"

            guild = self.bot.get_guild(guild_id)
            guild_name = guild.name if guild else "Unknown Server"
            
            src_emoji = get_source_emoji_from_uri(track.uri)
            embed = {
                "title": f"{emojis.MYMUSIC} Track Playing",
                "fields": [
                    {"name": f"{src_emoji} Track Title", "value": f"[{track.title}]({track.uri})"},
                    {"name": "👤 Artist / Author", "value": f"`{track.author}`"},
                    {"name": "⏱️ Duration", "value": f"`{format_duration(track.duration)}`"},
                    {"name": "🏠 Server", "value": f"**{guild_name}** (`{guild_id}`)"},
                    {"name": f"{emojis.USER_ICO} Requested By", "value": requester_mention}
                ],
                "color": 10181046,  # #9b59b6 Purple
                "thumbnail": {"url": self._get_thumbnail(track)},
                "footer": {"text": "Echo Logs • Music Monitor"}
            }
            self.bot.loop.create_task(send_log_webhook(Config.MUSIC_LOG_WEBHOOK_URL, self.bot, embed))
        except Exception as e:
            print(f"[Webhook Log] Error preparing song log: {e}")

        existing = self.idle_tasks.pop(guild_id, None)
        if existing and not existing.done():
            existing.cancel()

        # Real-time VC status update
        guild = self.bot.get_guild(guild_id)
        if guild and guild.voice_client and guild.voice_client.channel:
            human_members = [m for m in guild.voice_client.channel.members if not m.bot]
            if len(human_members) == 0:
                await player.set_pause(True)
                player.store("auto_paused_empty_vc", True)
                await self._update_vc_status(guild_id, f"{emojis.BTN_PAUSE} Paused: {track.title}")
                await self._notify_dashboard(guild_id)
                if self._last_announced.get(guild_id) == track.identifier:
                    await self._update_now_playing(guild_id)
                    return
                self._last_announced[guild_id] = track.identifier
                await self._send_now_playing(guild_id, player)
                return

        status_text = f"{emojis.MYMUSIC} {track.title}"
        await self._update_vc_status(guild_id, status_text)

        hist = self.history.setdefault(guild_id, [])
        if not hist or hist[-1].identifier != track.identifier:
            hist.append(track)
            if len(hist) > 15:
                hist.pop(0)

        await self._notify_dashboard(guild_id)

        self._last_announced[guild_id] = track.identifier

        recent = self.recent_tracks.setdefault(guild_id, [])
        recent.append(track.identifier)
        if len(recent) > 5:
            recent.pop(0)

        await self._send_now_playing(guild_id, player)

    @lavalink.listener(lavalink.events.TrackEndEvent)
    async def on_track_end(self, event: lavalink.events.TrackEndEvent):
        self._last_announced.pop(int(event.player.guild_id), None)
        await self._notify_dashboard(int(event.player.guild_id))

    @lavalink.listener(lavalink.events.TrackExceptionEvent)
    async def on_track_exception(self, event: lavalink.events.TrackExceptionEvent):
        guild_id = int(event.player.guild_id)
        channel_id = event.player.fetch("channel")
        guild = self.bot.get_guild(guild_id)
        print(f"[Music] Track exception in {guild_id}: {event.exception}")
        if guild and channel_id:
            ch = guild.get_channel(channel_id)
            if ch:
                view = make_text_container(f"{emojis.ERROR} Track error: `{event.exception}`")
                try:
                    await ch.send(view=view)
                except Exception:
                    pass

        # Webhook Track Exception Log
        try:
            guild_name = guild.name if guild else "Unknown Server"
            embed = {
                "title": "⚠️ Track Playback Exception",
                "description": (
                    f"**Track:** [{event.track.title}]({event.track.uri})\n"
                    f"**Server:** **{guild_name}** (`{guild_id}`)\n\n"
                    f"**Playback Exception:**\n"
                    f"```\n{str(event.exception)[:1000]}\n```"
                ),
                "color": 0xff3838,
                "footer": {"text": "Echo Logs v2 • Crash Reporter"},
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            self.bot.loop.create_task(send_log_webhook(Config.ERROR_LOG_WEBHOOK_URL, self.bot, embed))
        except Exception as e:
            print(f"[Webhook Log] Error preparing track error log: {e}")

    @lavalink.listener(lavalink.events.TrackStuckEvent)
    async def on_track_stuck(self, event: lavalink.events.TrackStuckEvent):
        print(f"[Music] Track stuck, skipping...")
        await event.player.skip()

    @lavalink.listener(lavalink.events.QueueEndEvent)
    async def on_queue_end(self, event: lavalink.events.QueueEndEvent):
        player = event.player
        guild_id = int(player.guild_id)
        self._last_announced.pop(guild_id, None)
        await self._notify_dashboard(guild_id)

        # Autoplay
        if self.autoplay_states.get(guild_id, False):
            if await self._try_autoplay(guild_id, player):
                return

        # 24/7 check
        is_247 = await self.bot.db.get_247(guild_id)
        if is_247:
            # Set "Ep <song>" status
            await self._update_vc_status(
                guild_id, f"{emojis.INFO} Use **Ep <song>**"
            )
            msg = self.now_playing_messages.pop(guild_id, None)
            if msg:
                try:
                    view = make_text_container(
                        f"{emojis.INFO} Queue ended — staying in VC (24/7 mode)."
                    )
                    await msg.edit(view=view)
                except discord.HTTPException:
                    pass
            return

        # Start idle timer
        self._start_idle_timer(guild_id)

        msg = self.now_playing_messages.pop(guild_id, None)
        if msg:
            try:
                view = make_text_container(
                    f"{emojis.INFO} Queue ended — idling for 5 minutes."
                )
                await msg.edit(view=view)
            except discord.HTTPException:
                pass

    def _start_idle_timer(self, guild_id: int):
        existing = self.idle_tasks.pop(guild_id, None)
        if existing and not existing.done():
            existing.cancel()
        task = self.bot.loop.create_task(self._idle_disconnect_delay(guild_id))
        self.idle_tasks[guild_id] = task

    async def _idle_disconnect_delay(self, guild_id: int):
        # Update voice status
        await self._update_vc_status(guild_id, f"{emojis.INFO} Idle — leaving soon")

        player = self.lavalink.player_manager.get(guild_id)
        channel_id = player.fetch("channel") if player else None
        guild = self.bot.get_guild(guild_id)
        ch = None

        if guild and channel_id:
            ch = guild.get_channel(channel_id)
            if ch:
                try:
                    view = make_text_container(f"{emojis.INFO} Queue ended. I will disconnect in 5 minutes if no songs are added.")
                    await ch.send(view=view)
                except Exception:
                    pass

        try:
            await asyncio.sleep(300.0) # 5 minutes
            player = self.lavalink.player_manager.get(guild_id)
            if player and not player.is_playing:
                player.store("voluntary_disconnect", True)
                await self._clear_vc_status(guild_id)
                if guild and guild.voice_client:
                    try:
                        await guild.voice_client.disconnect(force=True)
                    except Exception:
                        pass
                await self._notify_dashboard(guild_id)
                if ch:
                    try:
                        view = make_text_container(f"{emojis.INFO} Left the voice channel due to inactivity.")
                        await ch.send(view=view)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass
        finally:
            self.idle_tasks.pop(guild_id, None)

    # ─── Autoplay ───

    async def _try_autoplay(self, guild_id: int, player) -> bool:
        recent = self.recent_tracks.get(guild_id, [])
        if not recent:
            return False
        seed = recent[-1]
        try:
            mix = f"https://www.youtube.com/watch?v={seed}&list=RD{seed}"
            try:
                res = await player.node.get_tracks(mix)
            except Exception as e:
                print(f"[Autoplay] {e} on node '{player.node.name}'. Attempting failover...")
                available_nodes = [n for n in self.lavalink.node_manager.nodes if n.available and n != player.node]
                if available_nodes:
                    new_node = random.choice(available_nodes)
                    try:
                        await player.change_node(new_node)
                        res = await player.node.get_tracks(mix)
                    except Exception as fe:
                        print(f"[Autoplay Failover] Failed: {fe}")
                        return False
                else:
                    return False

            if not res or not res.tracks:
                return False
            added = 0
            for t in res.tracks:
                if t.identifier in recent:
                    continue
                player.add(requester=self.bot.user.id, track=t)
                added += 1
                if added >= 5:
                    break
            if added > 0 and (not player.is_playing or not player.current):
                try:
                    await player.play()
                except Exception as e:
                    print(f"[Autoplay Play] {e} on node '{player.node.name}'. Attempting failover...")
                    available_nodes = [n for n in self.lavalink.node_manager.nodes if n.available and n != player.node]
                    if available_nodes:
                        new_node = random.choice(available_nodes)
                        try:
                            await player.change_node(new_node)
                            await player.play()
                        except Exception as fe:
                            print(f"[Autoplay Play Failover] Failed: {fe}")
            return added > 0
        except Exception as e:
            print(f"[Autoplay] Error: {e}")
            return False

    # ─── Internal helpers ───

    def _get_thumbnail(self, track) -> str:
        """Get best available thumbnail URL for a track."""
        if not track:
            return None

        # Try artwork_url first (Spotify, Apple Music, SoundCloud have this)
        artwork = getattr(track, 'artwork_url', None)
        if artwork:
            return artwork

        # Check cache
        if hasattr(self, 'thumbnail_cache') and track.identifier in self.thumbnail_cache:
            return self.thumbnail_cache[track.identifier]

        # For YouTube, fallback to mqdefault (16:9)
        try:
            source = getattr(track, 'source_name', '').lower()
            uri = (track.uri or '').lower()

            if 'youtube' in source or 'youtube' in uri or 'youtu.be' in uri:
                return f"https://img.youtube.com/vi/{track.identifier}/mqdefault.jpg"
        except Exception:
            pass

        return None

    async def _cache_thumbnail(self, track):
        """Asynchronously pre-cache the best 16:9 thumbnail for a track."""
        if not track:
            return
        if not hasattr(self, 'thumbnail_cache'):
            self.thumbnail_cache = {}
        if track.identifier in self.thumbnail_cache:
            return

        source = getattr(track, 'source_name', '').lower()
        uri = (track.uri or '').lower()

        if 'youtube' in source or 'youtube' in uri or 'youtu.be' in uri:
            maxres_url = f"https://img.youtube.com/vi/{track.identifier}/maxresdefault.jpg"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.head(maxres_url, timeout=1.5) as resp:
                        if resp.status == 200:
                            self.thumbnail_cache[track.identifier] = maxres_url
                            return
            except Exception:
                pass
            self.thumbnail_cache[track.identifier] = f"https://img.youtube.com/vi/{track.identifier}/mqdefault.jpg"

    async def _update_vc_status(self, guild_id: int, text: str):
        """Update voice channel status text."""
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            return
        channel = guild.voice_client.channel
        if not channel:
            return

        # Clear status on all other voice channels in the server to prevent leakage to wrong VCs
        for vc in guild.voice_channels:
            if vc.id != channel.id:
                try:
                    await self.bot.http.request(
                        discord.http.Route(
                            "PUT", "/channels/{channel_id}/voice-status",
                            channel_id=vc.id,
                        ),
                        json={"status": ""},
                    )
                except Exception:
                    pass

        try:
            await self.bot.http.request(
                discord.http.Route(
                    "PUT", "/channels/{channel_id}/voice-status",
                    channel_id=channel.id,
                ),
                json={"status": text[:500]},
            )
        except Exception as e:
            print(f"[VC Status] {e}")

    async def _clear_vc_status(self, guild_id: int, channel_id: int = None):
        """Clear voice channel status."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channels_to_clear = []
        if channel_id:
            ch = guild.get_channel(channel_id)
            if ch:
                channels_to_clear.append(ch)
        if not channels_to_clear:
            if guild.voice_client and guild.voice_client.channel:
                channels_to_clear.append(guild.voice_client.channel)
            else:
                channels_to_clear = list(guild.voice_channels)

        async def _clear_one(vc):
            try:
                await asyncio.wait_for(
                    self.bot.http.request(
                        discord.http.Route(
                            "PUT", "/channels/{channel_id}/voice-status",
                            channel_id=vc.id,
                        ),
                        json={"status": ""},
                    ),
                    timeout=2.0
                )
            except Exception:
                pass

        if channels_to_clear:
            await asyncio.gather(*[_clear_one(vc) for vc in channels_to_clear], return_exceptions=True)

    async def _send_now_playing(self, guild_id: int, player):
        channel_id = player.fetch("channel")
        guild = self.bot.get_guild(guild_id)
        if not guild or not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not channel:
            return

        # Pre-cache thumbnail asynchronously before building
        if player.current:
            await self._cache_thumbnail(player.current)

        layout = NowPlayingLayout(self, guild_id)
        await layout.build(player)

        old = self.now_playing_messages.get(guild_id)
        if old:
            try:
                await old.delete()
            except discord.HTTPException:
                pass

        try:
            if getattr(layout, 'card_buf', None):
                file = discord.File(fp=layout.card_buf, filename="np_banner.png")
                msg = await channel.send(view=layout, file=file)
            else:
                msg = await channel.send(view=layout)
            layout.message = msg
            self.now_playing_messages[guild_id] = msg
        except discord.HTTPException as e:
            print(f"[Now Playing] Send failed: {e}")

    async def _update_now_playing(self, guild_id: int):
        player = self.lavalink.player_manager.get(guild_id)
        if not player:
            return
        msg = self.now_playing_messages.get(guild_id)
        if not msg:
            return await self._send_now_playing(guild_id, player)

        # Pre-cache thumbnail asynchronously before building
        if player.current:
            await self._cache_thumbnail(player.current)

        layout = NowPlayingLayout(self, guild_id)
        await layout.build(player)
        try:
            if getattr(layout, 'card_buf', None):
                file = discord.File(fp=layout.card_buf, filename="np_banner.png")
                await msg.edit(view=layout, attachments=[file])
            else:
                await msg.edit(view=layout, attachments=[])
        except discord.HTTPException:
            await self._send_now_playing(guild_id, player)

    async def _ensure_voice(self, ctx) -> bool:
        # Wait for lavalink node
        if not self._node_ready.is_set():
            view = make_text_container(f"{emojis.LOADING} Connecting to music server...")
            wait_msg = await ctx.send(view=view)
            try:
                await asyncio.wait_for(self._node_ready.wait(), timeout=20)
            except asyncio.TimeoutError:
                view = make_text_container(
                    f"{emojis.ERROR} Music server unavailable. Try again later."
                )
                await wait_msg.edit(view=view)
                return False
            try:
                await wait_msg.delete()
            except Exception:
                pass

        # Check user VC
        if not ctx.author.voice or not ctx.author.voice.channel:
            view = make_text_container(f"{emojis.ERROR} You must be in a voice channel.")
            await ctx.reply(view=view)
            return False

        vc = ctx.author.voice.channel
        player = self.lavalink.player_manager.create(ctx.guild.id)

        # Bot not connected
        if not ctx.guild.voice_client:
            perms = vc.permissions_for(ctx.guild.me)
            if not perms.connect or not perms.speak:
                view = make_text_container(
                    f"{emojis.ERROR} I need **Connect** and **Speak** permissions."
                )
                await ctx.reply(view=view)
                return False

            player.store("channel", ctx.channel.id)

            try:
                await vc.connect(cls=LavalinkVoiceClient, self_deaf=True)
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"[Connect] Error: {e}")
                view = make_text_container(f"{emojis.ERROR} Failed to connect: `{e}`")
                await ctx.reply(view=view)
                return False
        else:
            if ctx.guild.voice_client.channel != vc:
                view = make_text_container(f"{emojis.ERROR} You must be in my voice channel.")
                await ctx.reply(view=view)
                return False

        return True

    # dashboard bridge

    async def play_from_dashboard(self, guild: discord.Guild, member: discord.Member, query: str):
        """
        Same connect + search + queue pipeline as the `>play` command,
        called from dashboard/app.py's /api/guilds/{id}/play route.
        Returns (ok: bool, message: str) instead of editing a Discord
        message, since there's no ctx here.
        """
        if not self._node_ready.is_set():
            try:
                await asyncio.wait_for(self._node_ready.wait(), timeout=15)
            except asyncio.TimeoutError:
                return False, "Music server is unavailable right now."

        vc = member.voice.channel
        player = self.lavalink.player_manager.create(guild.id)

        if not guild.voice_client:
            perms = vc.permissions_for(guild.me)
            if not perms.connect or not perms.speak:
                return False, "Rose needs Connect and Speak permissions in that channel."
            player.store("channel", None)
            try:
                await vc.connect(cls=LavalinkVoiceClient, self_deaf=True)
                await asyncio.sleep(0.5)
            except Exception as e:
                return False, f"Failed to join voice channel: {e}"
        elif guild.voice_client.channel != vc:
            return False, "Rose is already playing in a different voice channel."

        search = query if URL_REGEX.match(query) else f"ytsearch:{query}"

        try:
            results = await player.node.get_tracks(search)
        except Exception as e:
            print(f"[Dashboard Search] {e} on node '{player.node.name}'. Attempting failover...")
            available_nodes = [n for n in self.lavalink.node_manager.nodes if n.available and n != player.node]
            if available_nodes:
                new_node = random.choice(available_nodes)
                try:
                    await player.change_node(new_node)
                    results = await player.node.get_tracks(search)
                except Exception as fe:
                    print(f"[Dashboard Search Failover] Failed: {fe}")
                    return False, f"Search failed: {e}"
            else:
                return False, f"Search failed: {e}"

        if not results or not results.tracks:
            return False, f"No results found for '{query}'."

        if results.load_type == lavalink.LoadType.PLAYLIST:
            for t in results.tracks:
                player.add(requester=member.id, track=t)
            message = f"Queued playlist '{results.playlist_info.name}' ({len(results.tracks)} tracks)."
        else:
            track = results.tracks[0]
            player.add(requester=member.id, track=track)
            message = f"Queued '{track.title}'."

        if not player.is_playing:
            try:
                await player.play()
            except Exception as e:
                print(f"[Dashboard Play] {e} on node '{player.node.name}'. Attempting failover...")
                available_nodes = [n for n in self.lavalink.node_manager.nodes if n.available and n != player.node]
                if available_nodes:
                    new_node = random.choice(available_nodes)
                    try:
                        await player.change_node(new_node)
                        await player.play()
                    except Exception as fe:
                        print(f"[Dashboard Play Failover] Failed: {fe}")
                        return False, f"Playback failed: {e}"
                else:
                    return False, f"Playback failed: {e}"

        return True, message

    # commands

    @commands.hybrid_command(name="play", aliases=["p"])
    @commands.guild_only()
    async def play(self, ctx, *, query: str = None):
        """Play a song or playlist."""
        if not query:
            view = make_text_container(
                f"{emojis.ERROR} Provide a song name or URL.\n"
                f"Usage: `{ctx.prefix}play <song / url>`"
            )
            return await ctx.reply(view=view)

        if not await self._ensure_voice(ctx):
            return

        player = self.lavalink.player_manager.get(ctx.guild.id)
        player.store("channel", ctx.channel.id)

        # Resolve query
        if not URL_REGEX.match(query):
            search = f"ytsearch:{query}"
        else:
            search = query

        loading_view = make_text_container(f"{emojis.LOADING} Searching for **{query}**...")
        loading_msg = None
        try:
            loading_msg = await ctx.reply(view=loading_view)
        except Exception:
            try:
                loading_msg = await ctx.send(view=loading_view)
            except Exception:
                pass

        async def _safe_edit(view):
            if loading_msg:
                try:
                    await loading_msg.edit(view=view)
                    return
                except Exception:
                    pass
            try:
                await ctx.send(view=view)
            except Exception:
                pass

        try:
            results = await player.node.get_tracks(search)
        except Exception as e:
            print(f"[Search] {e} on node '{player.node.name}'. Attempting failover...")
            available_nodes = [n for n in self.lavalink.node_manager.nodes if n.available and n != player.node]
            if available_nodes:
                new_node = random.choice(available_nodes)
                try:
                    await player.change_node(new_node)
                    print(f"[Search Failover] Switched player to '{new_node.name}'")
                    results = await player.node.get_tracks(search)
                except Exception as fe:
                    print(f"[Search Failover] Failed: {fe}")
                    view = make_text_container(f"{emojis.ERROR} Search failed: `{e}`")
                    return await _safe_edit(view)
            else:
                view = make_text_container(f"{emojis.ERROR} Search failed: `{e}`")
                return await _safe_edit(view)

        if not results or not results.tracks:
            view = make_text_container(f"{emojis.ERROR} No results found for **{query}**.")
            return await _safe_edit(view)

        # Playlist
        if results.load_type == lavalink.LoadType.PLAYLIST:
            for t in results.tracks:
                player.add(requester=ctx.author.id, track=t)

            view = make_text_container(
                f"### {emojis.SUCCESS} Playlist Enqueued\n"
                f"{emojis.DOT} **Name:** {results.playlist_info.name}\n"
                f"{emojis.DOT} **Tracks:** `{len(results.tracks)}`\n"
                f"{emojis.DOT} **Requester:** {ctx.author.mention}"
            )
            await _safe_edit(view)

        # Single track
        else:
            track = results.tracks[0]
            player.add(requester=ctx.author.id, track=track)

            src = get_source_emoji_from_uri(track.uri)

            if player.is_playing:
                # Show enqueued
                layout = ui.LayoutView()
                container = ui.Container(accent_colour=None)
                container.add_item(ui.TextDisplay(f"### {emojis.SUCCESS} Enqueued"))
                container.add_item(ui.Separator())

                info = (
                    f"### {src} [{track.title}]({track.uri})\n"
                    f"{emojis.DOT} **Artist:** {track.author}\n"
                    f"{emojis.DOT} **Duration:** `{format_duration(track.duration)}`\n"
                    f"{emojis.DOT} **Position:** `{len(player.queue)}`\n"
                    f"{emojis.DOT} **Requester:** {ctx.author.mention}"
                )
                container.add_item(ui.TextDisplay(info))
                await self._cache_thumbnail(track)
                thumb = self._get_thumbnail(track)
                if thumb:
                    try:
                        gallery = ui.MediaGallery()
                        gallery.add_item(media=discord.UnfurledMediaItem(url=thumb))
                        container.add_item(gallery)
                    except Exception as e:
                        print(f"[Enqueue Thumbnail Error] {e}")
                layout.add_item(container)
                await _safe_edit(layout)
            else:
                if loading_msg:
                    try:
                        await loading_msg.delete()
                    except Exception:
                        pass

        # Start playback
        if not player.is_playing:
            try:
                await player.play()
            except Exception as e:
                print(f"[Play] {e} on node '{player.node.name}'. Attempting failover...")
                available_nodes = [n for n in self.lavalink.node_manager.nodes if n.available and n != player.node]
                if available_nodes:
                    new_node = random.choice(available_nodes)
                    try:
                        await player.change_node(new_node)
                        print(f"[Play Failover] Switched player to '{new_node.name}'")
                        await player.play()
                    except Exception as fe:
                        print(f"[Play Failover] Failed: {fe}")
                        view = make_text_container(f"{emojis.ERROR} Playback failed: `{e}`")
                        await ctx.send(view=view)
                else:
                    view = make_text_container(f"{emojis.ERROR} Playback failed: `{e}`")
                    await ctx.send(view=view)

    @commands.hybrid_command(name="join", aliases=["connect"])
    @commands.guild_only()
    async def join(self, ctx, *, channel: discord.VoiceChannel = None):
        """Connect to a voice channel."""
        if not channel:
            if not ctx.author.voice or not ctx.author.voice.channel:
                return await ctx.reply(view=make_text_container(f"{emojis.ERROR} You must be in a voice channel or specify one."))
            channel = ctx.author.voice.channel

        player = self.lavalink.player_manager.create(ctx.guild.id)
        
        perms = channel.permissions_for(ctx.guild.me)
        if not perms.connect or not perms.speak:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} I need Connect and Speak permissions in `{channel.name}`."))

        if ctx.guild.voice_client:
            if ctx.guild.voice_client.channel == channel:
                return await ctx.reply(view=make_text_container(f"{emojis.INFO} Already connected to `{channel.name}`."))
            player.store("channel", ctx.channel.id)
            await ctx.guild.voice_client.move_to(channel)
            if player and player.current:
                status_text = f"{emojis.BTN_PAUSE} Paused: {player.current.title}" if player.paused else f"{emojis.MYMUSIC} {player.current.title}"
                await self._update_vc_status(ctx.guild.id, status_text)
            await self.bot.db.set_247_channel(ctx.guild.id, channel.id)
            return await ctx.reply(view=make_text_container(f"{emojis.SUCCESS} Moved to voice channel **{channel.name}**."))

        player.store("channel", ctx.channel.id)
        await channel.connect(cls=LavalinkVoiceClient, self_deaf=True)
        is_247 = await self.bot.db.get_247(ctx.guild.id)
        if is_247:
            await self.bot.db.set_247_channel(ctx.guild.id, channel.id)
            
        await ctx.reply(view=make_text_container(f"{emojis.SUCCESS} Connected to voice channel **{channel.name}**."))

    @commands.hybrid_command(name="move")
    @commands.guild_only()
    async def move(self, ctx, *, channel: discord.VoiceChannel = None):
        """Move the bot to another voice channel."""
        if not ctx.guild.voice_client:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} I am not connected to any voice channel. Use `/join` instead."))

        if not channel:
            if not ctx.author.voice or not ctx.author.voice.channel:
                return await ctx.reply(view=make_text_container(f"{emojis.ERROR} You must be in a voice channel or specify one to move me."))
            channel = ctx.author.voice.channel

        if ctx.guild.voice_client.channel == channel:
            return await ctx.reply(view=make_text_container(f"{emojis.INFO} Already in `{channel.name}`."))

        perms = channel.permissions_for(ctx.guild.me)
        if not perms.connect or not perms.speak:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} I need Connect and Speak permissions in `{channel.name}`."))

        player = self.lavalink.player_manager.create(ctx.guild.id)
        player.store("channel", ctx.channel.id)
        await ctx.guild.voice_client.move_to(channel)
        if player and player.current:
            status_text = f"{emojis.BTN_PAUSE} Paused: {player.current.title}" if player.paused else f"{emojis.MYMUSIC} {player.current.title}"
            await self._update_vc_status(ctx.guild.id, status_text)
        
        is_247 = await self.bot.db.get_247(ctx.guild.id)
        if is_247:
            await self.bot.db.set_247_channel(ctx.guild.id, channel.id)

        await ctx.reply(view=make_text_container(f"{emojis.SUCCESS} Moved to voice channel **{channel.name}**."))

    @commands.hybrid_command(name="dc", aliases=["leave", "disconnect"])
    @commands.guild_only()
    async def dc_cmd(self, ctx):
        """Disconnect the bot from voice."""
        player = self.lavalink.player_manager.get(ctx.guild.id) if self.lavalink else None
        current_vc_id = ctx.guild.voice_client.channel.id if ctx.guild.voice_client and ctx.guild.voice_client.channel else None

        if player:
            self._last_announced.pop(ctx.guild.id, None)
            player.queue.clear()
            try:
                await asyncio.wait_for(player.stop(), timeout=3.0)
            except Exception:
                pass
            player.store("voluntary_disconnect", True)
        elif self.lavalink:
            player = self.lavalink.player_manager.create(ctx.guild.id)
            player.store("voluntary_disconnect", True)

        try:
            await asyncio.wait_for(self._clear_vc_status(ctx.guild.id, current_vc_id), timeout=3.0)
        except Exception:
            pass

        if ctx.guild.voice_client:
            try:
                await asyncio.wait_for(ctx.guild.voice_client.disconnect(force=True), timeout=5.0)
            except Exception:
                pass

        try:
            await asyncio.wait_for(self._notify_dashboard(ctx.guild.id), timeout=2.0)
        except Exception:
            pass

        msg = self.now_playing_messages.pop(ctx.guild.id, None)
        if msg:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass

        view = make_text_container(f"{emojis.ERROR} Disconnected from voice channel.")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="rejoin")
    @commands.guild_only()
    async def rejoin(self, ctx):
        """Disconnect and reconnect to the voice channel."""
        vc = ctx.guild.voice_client
        if not vc or not vc.channel:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} I am not connected to any voice channel."))

        channel = vc.channel
        player = self.lavalink.player_manager.get(ctx.guild.id)
        bound_channel = player.fetch("channel") if player else None
        
        try:
            await vc.disconnect(force=True)
        except Exception:
            pass

        await asyncio.sleep(1.0)

        try:
            player = self.lavalink.player_manager.create(ctx.guild.id)
            if bound_channel:
                player.store("channel", bound_channel)
            await channel.connect(cls=LavalinkVoiceClient, self_deaf=True)
            
            is_247 = await self.bot.db.get_247(ctx.guild.id)
            if is_247:
                await self.bot.db.set_247_channel(ctx.guild.id, channel.id)

            await ctx.reply(view=make_text_container(f"{emojis.SUCCESS} Successfully rejoined voice channel **{channel.name}**."))
        except Exception as e:
            await ctx.reply(view=make_text_container(f"{emojis.ERROR} Failed to reconnect to `{channel.name}`: `{e}`"))

    async def _check_dj_permission(self, member: discord.Member, guild: discord.Guild) -> tuple[bool, str]:
        """
        Check if member has DJ role / permissions required for DJ actions (skip, stop, filter).
        """
        if not member or not guild:
            return True, ""

        if guild.owner_id == member.id or member.id in self.bot.owner_ids or getattr(member.guild_permissions, "manage_guild", False):
            return True, ""

        # Allow user if they are the only non-bot listener in the voice channel
        bot_vc = guild.voice_client
        if hasattr(member, "voice") and member.voice and bot_vc and member.voice.channel and member.voice.channel.id == bot_vc.channel.id:
            non_bot_members = [m for m in member.voice.channel.members if not m.bot]
            if len(non_bot_members) <= 1:
                return True, ""

        settings = await self.bot.db.get_guild_settings(guild.id)
        dj_role_id = settings.get("dj_role_id") if settings else None

        if not dj_role_id:
            return True, ""

        has_role = any(str(r.id) == str(dj_role_id) for r in getattr(member, "roles", []))
        if not has_role:
            try:
                role = guild.get_role(int(dj_role_id))
            except (ValueError, TypeError):
                role = None
            role_name = f"<@&{dj_role_id}>" if role else f"DJ Role (`{dj_role_id}`)"
            return False, f"You need the {role_name} role or Manage Server permissions to perform DJ actions (pause, resume, skip, stop, volume, loop, filter)."

        return True, ""

    @commands.hybrid_command(name="skip", aliases=["s"])
    @commands.guild_only()
    async def skip(self, ctx):
        """Skip current track."""
        allowed, dj_msg = await self._check_dj_permission(ctx.author, ctx.guild)
        if not allowed:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} {dj_msg}"))

        player = self.lavalink.player_manager.get(ctx.guild.id) if self.lavalink else None
        if not player or not player.is_playing:
            view = make_text_container(f"{emojis.ERROR} Nothing is playing.")
            return await ctx.reply(view=view)
        await player.skip()
        await self._notify_dashboard(ctx.guild.id)
        view = make_text_container(f"{emojis.BTN_SKIP} Skipped.")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="stop")
    @commands.guild_only()
    async def stop(self, ctx):
        """Stop playback and clear queue without disconnecting."""
        allowed, dj_msg = await self._check_dj_permission(ctx.author, ctx.guild)
        if not allowed:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} {dj_msg}"))

        player = self.lavalink.player_manager.get(ctx.guild.id) if self.lavalink else None
        if not player:
            view = make_text_container(f"{emojis.ERROR} Not connected.")
            return await ctx.reply(view=view)

        self._last_announced.pop(ctx.guild.id, None)
        player.queue.clear()
        try:
            await asyncio.wait_for(player.stop(), timeout=3.0)
        except Exception:
            pass

        # Start idle timer if 24/7 is disabled
        is_247 = await self.bot.db.get_247(ctx.guild.id)
        if not is_247:
            self._start_idle_timer(ctx.guild.id)

        try:
            await asyncio.wait_for(self._notify_dashboard(ctx.guild.id), timeout=2.0)
        except Exception:
            pass

        msg = self.now_playing_messages.pop(ctx.guild.id, None)
        if msg:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass

        view = make_text_container(f"{emojis.BTN_STOP} Playback stopped and queue cleared.")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="pause")
    @commands.guild_only()
    async def pause(self, ctx):
        """Pause player."""
        allowed, dj_msg = await self._check_dj_permission(ctx.author, ctx.guild)
        if not allowed:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} {dj_msg}"))

        player = self.lavalink.player_manager.get(ctx.guild.id) if self.lavalink else None
        if not player or not player.is_playing:
            view = make_text_container(f"{emojis.ERROR} Nothing is playing.")
            return await ctx.reply(view=view)
        player.delete("auto_paused_empty_vc")
        await player.set_pause(True)
        if player.current:
            await self._update_vc_status(ctx.guild.id, f"{emojis.BTN_PAUSE} Paused: {player.current.title}")
        await self._update_now_playing(ctx.guild.id)
        await self._notify_dashboard(ctx.guild.id)
        view = make_text_container(f"{emojis.BTN_PAUSE} Paused.")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="resume", aliases=["unpause"])
    @commands.guild_only()
    async def resume(self, ctx):
        """Resume player."""
        allowed, dj_msg = await self._check_dj_permission(ctx.author, ctx.guild)
        if not allowed:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} {dj_msg}"))

        player = self.lavalink.player_manager.get(ctx.guild.id) if self.lavalink else None
        if not player:
            view = make_text_container(f"{emojis.ERROR} Nothing to resume.")
            return await ctx.reply(view=view)
        player.delete("auto_paused_empty_vc")
        await player.set_pause(False)
        if player.current:
            await self._update_vc_status(ctx.guild.id, f"{emojis.MYMUSIC} {player.current.title}")
        await self._update_now_playing(ctx.guild.id)
        await self._notify_dashboard(ctx.guild.id)
        view = make_text_container(f"{emojis.BTN_RESUME} Resumed.")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="volume", aliases=["vol"])
    @commands.guild_only()
    async def volume(self, ctx, vol: int = None):
        """Set/view volume."""
        player = self.lavalink.player_manager.get(ctx.guild.id) if self.lavalink else None
        if not player:
            view = make_text_container(f"{emojis.ERROR} Not connected.")
            return await ctx.reply(view=view)
        if vol is None:
            view = make_text_container(f"Current volume: `{player.volume}%`")
            return await ctx.reply(view=view)

        allowed, dj_msg = await self._check_dj_permission(ctx.author, ctx.guild)
        if not allowed:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} {dj_msg}"))

        vol = max(0, min(150, vol))
        await player.set_volume(vol)
        await self._notify_dashboard(ctx.guild.id)
        view = make_text_container(f"{emojis.SUCCESS} Volume set to `{vol}%`")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="loop", aliases=["repeat"])
    @commands.guild_only()
    async def loop(self, ctx):
        """Toggle loop."""
        allowed, dj_msg = await self._check_dj_permission(ctx.author, ctx.guild)
        if not allowed:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} {dj_msg}"))

        player = self.lavalink.player_manager.get(ctx.guild.id) if self.lavalink else None
        if not player or not player.is_playing:
            view = make_text_container(f"{emojis.ERROR} Nothing is playing.")
            return await ctx.reply(view=view)
        player.set_loop(0 if player.loop else 1)
        await self._update_now_playing(ctx.guild.id)
        await self._notify_dashboard(ctx.guild.id)
        state = "enabled" if player.loop else "disabled"
        view = make_text_container(f"{emojis.BTN_LOOP} Loop **{state}**.")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="shuffle")
    @commands.guild_only()
    async def shuffle(self, ctx):
        """Shuffle queue."""
        allowed, dj_msg = await self._check_dj_permission(ctx.author, ctx.guild)
        if not allowed:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} {dj_msg}"))

        player = self.lavalink.player_manager.get(ctx.guild.id) if self.lavalink else None
        if not player or not player.queue:
            view = make_text_container(f"{emojis.ERROR} Queue is empty.")
            return await ctx.reply(view=view)
        random.shuffle(player.queue)
        view = make_text_container(f"{emojis.SUCCESS} Queue shuffled!")
        await ctx.reply(view=view)
        await self._notify_dashboard(ctx.guild.id)

    async def apply_filter(self, guild_id: int, filter_type: str, requester: discord.Member = None) -> tuple[bool, str]:
        if not self.lavalink:
            return False, "Music system not initialized."
        player = self.lavalink.player_manager.get(guild_id)
        if not player or not player.is_connected:
            return False, "Bot is not connected to a voice channel."

        if requester and isinstance(requester, discord.Member):
            allowed, dj_msg = await self._check_dj_permission(requester, requester.guild)
            if not allowed:
                return False, dj_msg

        from lavalink.filters import Equalizer, Timescale, Rotation, Karaoke, ChannelMix

        filter_type = (filter_type or "clear").lower()

        try:
            if filter_type == "bassboost":
                eq = Equalizer()
                eq.update(bands=[(0, 0.45), (1, 0.35), (2, 0.25), (3, 0.15), (4, 0.10)])
                await player.set_filter(eq, replace=True)
                msg = "Bassboost filter applied."
            elif filter_type == "nightcore":
                ts = Timescale(speed=1.25, pitch=1.25)
                await player.set_filter(ts, replace=True)
                msg = "Nightcore filter applied."
            elif filter_type == "vaporwave":
                ts = Timescale(speed=0.80, pitch=0.75)
                await player.set_filter(ts, replace=True)
                msg = "Vaporwave filter applied."
            elif filter_type == "8d":
                rot = Rotation(rotation_hz=0.85)
                cm = ChannelMix(left_to_left=0.5, left_to_right=0.5, right_to_left=0.5, right_to_right=0.5)
                await player.set_filters(rot, cm, replace=True)
                msg = "8D Spatial Audio filter applied (Left-Right Panning)."
            elif filter_type == "karaoke":
                k = Karaoke(level=1.0, mono_level=1.0, filter_band=220.0, filter_width=100.0)
                await player.set_filter(k, replace=True)
                msg = "Karaoke filter applied."
            elif filter_type == "treble":
                eq = Equalizer()
                eq.update(bands=[(10, 0.25), (11, 0.30), (12, 0.35), (13, 0.30), (14, 0.25)])
                await player.set_filter(eq, replace=True)
                msg = "Treble Boost filter applied."
            elif filter_type in ("clear", "reset", "none"):
                filter_type = "clear"
                await player.clear_filters()
                msg = "Audio filters cleared."
            else:
                return False, f"Unknown filter type '{filter_type}'."

            player.store("active_filter", filter_type if filter_type != "clear" else None)
            await self._notify_dashboard(guild_id)
            return True, msg
        except Exception as e:
            print(f"[Filter Error] {e}")
            import traceback
            traceback.print_exc()
            return False, f"Failed to apply filter: {e}"

class FilterMenuLayout(ui.LayoutView):
    def __init__(self, cog, ctx):
        super().__init__(timeout=120)
        self.cog = cog
        self.ctx = ctx
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        container = ui.Container(accent_colour=0x5865F2)

        player = self.cog.lavalink.player_manager.get(self.ctx.guild.id) if self.cog.lavalink else None
        active_filter = player.fetch("active_filter", "clear") if player else "clear"
        if not active_filter:
            active_filter = "clear"

        filter_names = {
            "clear": "Normal (No Filter)",
            "bassboost": "Bassboost 🔊",
            "nightcore": "Nightcore ⚡",
            "vaporwave": "Vaporwave 🌊",
            "8d": "8D Audio 🎧",
            "karaoke": "Karaoke 🎤",
            "treble": "Treble Boost 🎼"
        }
        current_name = filter_names.get(active_filter.lower(), active_filter.capitalize())

        header = (
            f"### 🎛️ **Audio Equalizer & DSP Filters**\n"
            f"> ⚙️ **Active Filter:** `{current_name}`\n"
            f"-# Select an audio filter from the dropdown menu below to modify playback in real-time."
        )
        container.add_item(ui.TextDisplay(header))
        container.add_item(ui.Separator())

        # Select menu row
        row_select = ui.ActionRow()
        row_select.add_item(FilterMenuSelect(self.cog, self.ctx, self))
        container.add_item(row_select)

        self.add_item(container)


class FilterMenuSelect(ui.Select):
    def __init__(self, cog, ctx, layout):
        self.cog = cog
        self.ctx = ctx
        self.layout = layout

        player = cog.lavalink.player_manager.get(ctx.guild.id) if cog.lavalink else None
        active_filter = player.fetch("active_filter", "clear") if player else "clear"
        if not active_filter:
            active_filter = "clear"

        options = [
            discord.SelectOption(label="Clear Filters", value="clear", description="Reset all audio filters to normal", emoji="⚙️", default=(active_filter == "clear")),
            discord.SelectOption(label="Bassboost", value="bassboost", description="Enhanced heavy bass equalizer", emoji="🔊", default=(active_filter == "bassboost")),
            discord.SelectOption(label="Nightcore", value="nightcore", description="Faster speed & pitch shift", emoji="⚡", default=(active_filter == "nightcore")),
            discord.SelectOption(label="Vaporwave", value="vaporwave", description="Slower relaxed lofi speed & pitch", emoji="🌊", default=(active_filter == "vaporwave")),
            discord.SelectOption(label="8D Audio", value="8d", description="Spatial 3D surround sound panning", emoji="🎧", default=(active_filter == "8d")),
            discord.SelectOption(label="Karaoke", value="karaoke", description="Vocal suppressor filter", emoji="🎤", default=(active_filter == "karaoke")),
            discord.SelectOption(label="Treble Boost", value="treble", description="Enhanced high-frequency equalizer", emoji="🎼", default=(active_filter == "treble")),
        ]
        super().__init__(placeholder="🎛️ Choose an Audio Filter...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        if interaction.user.id != self.ctx.author.id:
            return await interaction.followup.send("This filter menu was requested by another user.", ephemeral=True)

        player = self.cog.lavalink.player_manager.get(interaction.guild.id)
        if not player or not player.is_connected:
            return await interaction.followup.send(f"{emojis.ERROR} Bot is not connected to a voice channel.", ephemeral=True)

        if not interaction.user.voice or interaction.user.voice.channel != interaction.guild.voice_client.channel:
            return await interaction.followup.send("You must be in the same voice channel.", ephemeral=True)

        filter_choice = self.values[0]
        ok, msg = await self.cog.apply_filter(interaction.guild.id, filter_choice, requester=interaction.user)

        self.layout.rebuild()
        await interaction.edit_original_response(view=self.layout)
        if ok:
            await interaction.followup.send(f"🎛️ {msg}", ephemeral=True)
        else:
            await interaction.followup.send(f"{emojis.ERROR} {msg}", ephemeral=True)


    @commands.hybrid_command(name="filter", aliases=["eq", "filters"])
    @commands.guild_only()
    async def filter_cmd(self, ctx, filter_type: str = None):
        """Apply audio EQ filters or open interactive filter selection menu."""
        if filter_type is None:
            layout = FilterMenuLayout(self, ctx)
            return await ctx.reply(view=layout)

        ok, msg = await self.apply_filter(ctx.guild.id, filter_type, requester=ctx.author)
        emoji_prefix = emojis.SUCCESS if ok else emojis.ERROR
        view = make_text_container(f"{emoji_prefix} {msg}")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="bassboost", aliases=["bb"])
    @commands.guild_only()
    async def bassboost_cmd(self, ctx):
        """Apply Bassboost filter."""
        ok, msg = await self.apply_filter(ctx.guild.id, "bassboost", requester=ctx.author)
        view = make_text_container(f"{emojis.SUCCESS if ok else emojis.ERROR} {msg}")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="nightcore", aliases=["nc"])
    @commands.guild_only()
    async def nightcore_cmd(self, ctx):
        """Apply Nightcore filter."""
        ok, msg = await self.apply_filter(ctx.guild.id, "nightcore", requester=ctx.author)
        view = make_text_container(f"{emojis.SUCCESS if ok else emojis.ERROR} {msg}")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="vaporwave")
    @commands.guild_only()
    async def vaporwave_cmd(self, ctx):
        """Apply Vaporwave filter."""
        ok, msg = await self.apply_filter(ctx.guild.id, "vaporwave", requester=ctx.author)
        view = make_text_container(f"{emojis.SUCCESS if ok else emojis.ERROR} {msg}")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="8d")
    @commands.guild_only()
    async def eight_d_cmd(self, ctx):
        """Apply 8D Spatial Audio filter."""
        ok, msg = await self.apply_filter(ctx.guild.id, "8d", requester=ctx.author)
        view = make_text_container(f"{emojis.SUCCESS if ok else emojis.ERROR} {msg}")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="karaoke")
    @commands.guild_only()
    async def karaoke_cmd(self, ctx):
        """Apply Karaoke vocal reducer filter."""
        ok, msg = await self.apply_filter(ctx.guild.id, "karaoke", requester=ctx.author)
        view = make_text_container(f"{emojis.SUCCESS if ok else emojis.ERROR} {msg}")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="treble")
    @commands.guild_only()
    async def treble_cmd(self, ctx):
        """Apply Treble Boost filter."""
        ok, msg = await self.apply_filter(ctx.guild.id, "treble", requester=ctx.author)
        view = make_text_container(f"{emojis.SUCCESS if ok else emojis.ERROR} {msg}")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="clearfilters", aliases=["reseteq", "resetfilters"])
    @commands.guild_only()
    async def clearfilters_cmd(self, ctx):
        """Clear all active audio filters."""
        ok, msg = await self.apply_filter(ctx.guild.id, "clear", requester=ctx.author)
        view = make_text_container(f"{emojis.SUCCESS if ok else emojis.ERROR} {msg}")
        await ctx.reply(view=view)

    @commands.hybrid_command(name="previous", aliases=["prev", "back"])
    @commands.guild_only()
    async def previous(self, ctx):
        """Play the previous track."""
        allowed, dj_msg = await self._check_dj_permission(ctx.author, ctx.guild)
        if not allowed:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} {dj_msg}"))

        ok = await self.play_previous(ctx.guild.id)
        if not ok:
            view = make_text_container(f"{emojis.ERROR} No previous track.")
            return await ctx.reply(view=view)
        view = make_text_container(f"{emojis.SUCCESS} Playing previous track.")
        await ctx.reply(view=view)
        await self._notify_dashboard(ctx.guild.id)

    @commands.hybrid_command(name="replay")
    @commands.guild_only()
    async def replay(self, ctx):
        """Restart the current track from the beginning."""
        allowed, dj_msg = await self._check_dj_permission(ctx.author, ctx.guild)
        if not allowed:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} {dj_msg}"))

        player = self.lavalink.player_manager.get(ctx.guild.id) if self.lavalink else None
        if not player or not player.current:
            view = make_text_container(f"{emojis.ERROR} Nothing is playing.")
            return await ctx.reply(view=view)
        await player.seek(0)
        view = make_text_container(f"{emojis.SUCCESS} Replaying current track.")
        await ctx.reply(view=view)
        await self._notify_dashboard(ctx.guild.id)

class InteractiveQueueLayout(ui.LayoutView):
    def __init__(self, cog, ctx, current_page: int = 0):
        super().__init__(timeout=120)
        self.cog = cog
        self.ctx = ctx
        self.current_page = current_page
        self.per_page = 10
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        container = ui.Container(accent_colour=0x5865F2)

        player = self.cog.lavalink.player_manager.get(self.ctx.guild.id) if self.cog.lavalink else None
        if not player or (not player.queue and not player.current):
            container.add_item(ui.TextDisplay(f"{emojis.ERROR} The music queue is currently empty."))
            self.add_item(container)
            return

        current_track = player.current
        queue_tracks = list(player.queue)
        total_tracks = len(queue_tracks)
        total_pages = max(1, math.ceil(total_tracks / self.per_page))

        if self.current_page >= total_pages:
            self.current_page = total_pages - 1
        if self.current_page < 0:
            self.current_page = 0

        start_idx = self.current_page * self.per_page
        end_idx = start_idx + self.per_page
        page_tracks = queue_tracks[start_idx:end_idx]

        header = f"### 📜 **Music Queue — {self.ctx.guild.name}**\n"
        if current_track:
            src = get_source_emoji_from_uri(current_track.uri)
            header += f"> ▶️ **Now Playing:** {src} **[{current_track.title}]({current_track.uri})** (`{format_duration(current_track.duration)}`)\n"
        container.add_item(ui.TextDisplay(header))
        container.add_item(ui.Separator())

        if not queue_tracks:
            container.add_item(ui.TextDisplay("-# *No upcoming tracks in queue. Add more using `/play <song>`.*"))
        else:
            lines = []
            for i, t in enumerate(page_tracks, start=start_idx + 1):
                src = get_source_emoji_from_uri(t.uri)
                lines.append(f"`{i}.` {src} **[{truncate(t.title, 40)}]({t.uri})** — `{format_duration(t.duration)}`")
            
            queue_body = "\n".join(lines)
            container.add_item(ui.TextDisplay(queue_body))

        container.add_item(ui.Separator())

        total_dur = sum(t.duration for t in queue_tracks) + (current_track.duration if current_track else 0)
        footer = f"-# Page {self.current_page + 1}/{total_pages} • {total_tracks} queued track(s) • Total Time: {format_duration(total_dur)}"
        container.add_item(ui.TextDisplay(footer))

        # Control Row
        row = ui.ActionRow()
        row.add_item(QueueNavButton("prev", "Prev", "◀️", self, disabled=(self.current_page == 0)))
        row.add_item(QueueNavButton("next", "Next", "▶️", self, disabled=(self.current_page >= total_pages - 1)))
        row.add_item(QueueActionButton("shuffle", "Shuffle", "🔀", self, disabled=(total_tracks < 2)))
        row.add_item(QueueActionButton("clear", "Clear", "🧹", self, disabled=(total_tracks == 0)))
        row.add_item(QueueActionButton("refresh", "Refresh", "🔄", self))
        container.add_item(row)

        self.add_item(container)


class QueueNavButton(ui.Button):
    def __init__(self, action, label, emoji_str, layout, disabled=False):
        super().__init__(label=label, emoji=emoji_str, style=discord.ButtonStyle.secondary, disabled=disabled)
        self.action = action
        self.layout = layout

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.action == "prev":
            self.layout.current_page -= 1
        elif self.action == "next":
            self.layout.current_page += 1
        
        self.layout.rebuild()
        await interaction.edit_original_response(view=self.layout)


class QueueActionButton(ui.Button):
    def __init__(self, action, label, emoji_str, layout, disabled=False):
        style = discord.ButtonStyle.danger if action == "clear" else discord.ButtonStyle.primary
        super().__init__(label=label, emoji=emoji_str, style=style, disabled=disabled)
        self.action = action
        self.layout = layout

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        cog = self.layout.cog
        player = cog.lavalink.player_manager.get(interaction.guild.id) if cog.lavalink else None
        if not player:
            return await interaction.followup.send(f"{emojis.ERROR} Player not found.", ephemeral=True)

        if self.action == "shuffle":
            allowed, dj_msg = await cog._check_dj_permission(interaction.user, interaction.guild)
            if not allowed:
                return await interaction.followup.send(f"{emojis.ERROR} {dj_msg}", ephemeral=True)
            import random
            random.shuffle(player.queue)
            await cog._notify_dashboard(interaction.guild.id)
            await interaction.followup.send("🔀 Queue shuffled!", ephemeral=True)

        elif self.action == "clear":
            allowed, dj_msg = await cog._check_dj_permission(interaction.user, interaction.guild)
            if not allowed:
                return await interaction.followup.send(f"{emojis.ERROR} {dj_msg}", ephemeral=True)
            player.queue.clear()
            await cog._notify_dashboard(interaction.guild.id)
            await interaction.followup.send("🧹 Queue cleared!", ephemeral=True)

        self.layout.rebuild()
        await interaction.edit_original_response(view=self.layout)


    @commands.hybrid_command(name="queue", aliases=["q"])
    @commands.guild_only()
    async def queue_cmd(self, ctx):
        """Show interactive paginated queue with shuffle & clear controls."""
        layout = InteractiveQueueLayout(self, ctx)
        await ctx.reply(view=layout)

    @commands.hybrid_command(name="nowplaying", aliases=["np"])
    @commands.guild_only()
    async def nowplaying(self, ctx):
        """Show now playing."""
        player = self.lavalink.player_manager.get(ctx.guild.id) if self.lavalink else None
        if not player or not player.current:
            view = make_text_container(f"{emojis.ERROR} Nothing is playing.")
            return await ctx.reply(view=view)
        layout = NowPlayingLayout(self, ctx.guild.id)
        await layout.build(player)
        if getattr(layout, 'card_buf', None):
            file = discord.File(fp=layout.card_buf, filename="np_banner.png")
            msg = await ctx.reply(view=layout, file=file)
        else:
            msg = await ctx.reply(view=layout)
        layout.message = msg
        self.now_playing_messages[ctx.guild.id] = msg

    @commands.hybrid_command(name="autoplay", aliases=["ap"])
    @commands.guild_only()
    async def autoplay_cmd(self, ctx):
        """Toggle autoplay."""
        allowed, dj_msg = await self._check_dj_permission(ctx.author, ctx.guild)
        if not allowed:
            return await ctx.reply(view=make_text_container(f"{emojis.ERROR} {dj_msg}"))

        current = self.autoplay_states.get(ctx.guild.id, False)
        self.autoplay_states[ctx.guild.id] = not current
        status = "Enabled" if not current else "Disabled"
        view = make_text_container(f"{emojis.BTN_AUTOPLAY} Autoplay: **{status}**")
        await ctx.reply(view=view)
        await self._update_now_playing(ctx.guild.id)
        await self._notify_dashboard(ctx.guild.id)

    @commands.hybrid_command(name="history")
    @commands.guild_only()
    async def history_cmd(self, ctx):
        """Show recently played songs in this server."""
        tracks = await self.bot.db.get_recently_played(ctx.guild.id, limit=15)
        if not tracks:
            view = make_text_container(f"{emojis.INFO} No songs have been played in this server yet.")
            return await ctx.reply(view=view)

        lines = []
        for i, t in enumerate(tracks):
            lines.append(f"`{i+1}.` {t['title']} — *{t['author']}*")

        desc = "\n".join(lines)
        text = f"### {emojis.MYMUSIC} Recently Played History\n{desc}"
        
        view = ui.LayoutView(timeout=120)
        container = ui.Container(accent_colour=None)
        container.add_item(ui.TextDisplay(text))
        
        row = ui.ActionRow()
        row.add_item(StatsPlaySelect(ctx, tracks))
        container.add_item(row)
        
        view.add_item(container)
        msg = await ctx.reply(view=view)
        view.message = msg



    @commands.hybrid_command(name="musicstats", aliases=["mstats", "serverstats", "musicstat", "mstat"])
    @commands.guild_only()
    async def musicstats_cmd(self, ctx):
        """Show server-specific music statistics and playback analytics."""
        layout = MusicStatsLayout(self.bot, ctx)
        await layout.init_build()
        msg = await ctx.reply(view=layout)
        layout.message = msg


class StatsPlaySelect(ui.Select):
    def __init__(self, ctx, tracks):
        self.ctx = ctx
        self.tracks = tracks
        options = []
        for i, t in enumerate(tracks[:25]):
            title = t["title"]
            if len(title) > 60:
                title = title[:57] + "..."
            options.append(
                discord.SelectOption(
                    label=f"{i+1}. {title}",
                    description=t["author"][:50],
                    value=t["uri"]
                )
            )
        super().__init__(placeholder="Select a track to play...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)

        await interaction.response.defer()
        
        music_cog = self.ctx.bot.get_cog("Music")
        if not music_cog:
            return await interaction.followup.send("Music system unavailable.", ephemeral=True)
            
        member = self.ctx.guild.get_member(interaction.user.id)
        if not member or not member.voice or not member.voice.channel:
            return await interaction.followup.send("Join a voice channel first.", ephemeral=True)

        ok, message = await music_cog.play_from_dashboard(self.ctx.guild, member, self.values[0])
        if not ok:
            return await interaction.followup.send(f"{emojis.ERROR} {message}", ephemeral=True)

        await interaction.followup.send(f"{emojis.SUCCESS} Queued track successfully!")


async def setup(bot):
    await bot.add_cog(Music(bot))