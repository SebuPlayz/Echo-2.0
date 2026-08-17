import discord
from discord.ext import commands
from discord import ui
import time
import platform
import psutil
from config import Config
import emojis


# helpers

def make_text_container(text: str) -> ui.LayoutView:
    view = ui.LayoutView()
    container = ui.Container(accent_colour=None)
    container.add_item(ui.TextDisplay(text))
    view.add_item(container)
    return view


def make_progress_bar(percent: float, length: int = 10) -> str:
    percent = max(0.0, min(100.0, percent))
    filled = int(round((percent / 100.0) * length))
    bar = "█" * filled + "░" * (length - filled)
    return f"`[{bar}] {percent:.1f}%`"


def format_uptime(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def get_latency_badge(latency_ms: int) -> str:
    if latency_ms < 100:
        return f"{emojis.SUCCESS} **Optimal**"
    elif latency_ms < 250:
        return f"{emojis.INFO} **Good**"
    else:
        return f"{emojis.ERROR} **High Latency**"


# help menu layout

class HelpCategorySelect(ui.Select):
    def __init__(self, bot, ctx, layout):
        self.bot_ref = bot
        self.ctx = ctx
        self.layout = layout
        options = [
            discord.SelectOption(label="Home Overview", value="home", description="Return to main help desk overview", emoji=emojis.Echo),
            discord.SelectOption(label="Music Module", value="Music", description="320kbps playback, EQ filters & voice controls", emoji=emojis.CAT_MUSIC),
            discord.SelectOption(label="Playlist Engine", value="Playlist", description="Custom user playlists & track management", emoji=emojis.MYMUSIC),
            discord.SelectOption(label="Server Configuration", value="Config", description="24/7 playback mode & prefix setup", emoji=emojis.CAT_CONFIG),
            discord.SelectOption(label="Bot Information", value="Information", description="System stats, latency, support & invite", emoji=emojis.CAT_INFO),
            discord.SelectOption(label="Utilities", value="Utility", description="User, server, avatar & banner tools", emoji=emojis.CAT_UTILITY),
        ]
        super().__init__(placeholder="Select a category module...", options=options)

    async def callback(self, interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("This help menu was requested by another user.", ephemeral=True)
        self.layout.current = self.values[0]
        self.layout.rebuild()
        await interaction.response.edit_message(view=self.layout)


class HelpLayout(ui.LayoutView):
    def __init__(self, bot, ctx, prefix: str):
        super().__init__(timeout=120)
        self.bot_ref = bot
        self.ctx = ctx
        self.prefix = prefix
        self.current = "home"
        self.message = None
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        container = ui.Container(accent_colour=None)

        if self.current == "home":
            self._build_home(container)
        else:
            self._build_category(container, self.current)

        container.add_item(ui.Separator())
        row = ui.ActionRow()
        row.add_item(HelpCategorySelect(self.bot_ref, self.ctx, self))
        container.add_item(row)

        self.add_item(container)

    def _build_home(self, container):
        prefix = self.prefix
        non_hidden_cmds = [c for c in self.bot_ref.commands if not c.hidden]
        bot_name = self.bot_ref.user.name if (self.bot_ref and self.bot_ref.user) else "Echo"
        dash_url = getattr(Config, "DASHBOARD_URL", "http://localhost:3000")

        container.add_item(ui.TextDisplay(f"### {emojis.Echo} **{bot_name} — Help Desk**"))
        container.add_item(ui.TextDisplay(
            f"*{emojis.MUSIC} 320kbps Lossless Audio • 24/7 Playback • Web Dashboard*\n"
            f"{emojis.DOT} **Prefix:** `{prefix}`  •  **Commands:** `{len(non_hidden_cmds)}` active  •  [Web Console]({dash_url})"
        ))
        container.add_item(ui.Separator())

        music_cmds = len([c for c in self.bot_ref.commands if c.cog_name == "Music" and not c.hidden])
        pl_cmds = len([c for c in self.bot_ref.commands if c.cog_name == "Playlist" and not c.hidden])
        cfg_cmds = len([c for c in self.bot_ref.commands if c.cog_name == "Config" and not c.hidden])
        info_cmds = len([c for c in self.bot_ref.commands if c.cog_name == "Information" and not c.hidden])
        util_cmds = len([c for c in self.bot_ref.commands if c.cog_name == "Utility" and not c.hidden])

        overview = (
            f"{emojis.CAT_MUSIC} **Music Console** — `{music_cmds}` commands\n"
            f"↳ *Playback, 8D EQ filters, queue management & 24/7 mode*\n\n"
            f"{emojis.MYMUSIC} **Playlist Engine** — `{pl_cmds}` commands\n"
            f"↳ *Custom user playlists & track management*\n\n"
            f"{emojis.CAT_CONFIG} **Server Config** — `{cfg_cmds}` commands\n"
            f"↳ *Guild prefix customization & voice settings*\n\n"
            f"{emojis.CAT_INFO} **Information** — `{info_cmds}` commands\n"
            f"↳ *Latency ping, system stats & support server*\n\n"
            f"{emojis.CAT_UTILITY} **Utilities** — `{util_cmds}` commands\n"
            f"↳ *Avatars, banners, server info & member count*"
        )
        container.add_item(ui.TextDisplay(overview))
        container.add_item(ui.TextDisplay(f"-# Select a category module below to inspect commands."))

    def _build_category(self, container, category):
        cat_emojis = {
            "Music": emojis.CAT_MUSIC,
            "Playlist": emojis.MYMUSIC,
            "Config": emojis.CAT_CONFIG,
            "Information": emojis.CAT_INFO,
            "Utility": emojis.CAT_UTILITY,
        }
        ce = cat_emojis.get(category, emojis.DOT)
        container.add_item(ui.TextDisplay(f"### {ce} **{category} Module**"))
        container.add_item(ui.Separator())

        cmds = [c for c in self.bot_ref.commands if (c.cog_name or "Other") == category and not c.hidden]
        if not cmds:
            container.add_item(ui.TextDisplay(f"{emojis.INFO} No active commands in this module."))
            return

        lines = []
        for c in sorted(cmds, key=lambda x: x.name):
            aliases = f" *(alias: `{', '.join(c.aliases)}`)*" if c.aliases else ""
            h_text = c.help.split('\n')[0] if c.help else "Execute command."
            if len(h_text) > 42:
                h_text = h_text[:39] + "..."
            lines.append(f"{emojis.DOT} **`/{c.name}`**{aliases} — *{h_text}*")
            
        container.add_item(ui.TextDisplay("\n".join(lines)))
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay(f"-# **Total Commands:** `{len(cmds)}` in **{category}**"))


# stats layout

class StatsSelect(ui.Select):
    def __init__(self, bot, ctx, layout):
        self.bot_ref = bot
        self.ctx = ctx
        self.layout = layout
        options = [
            discord.SelectOption(
                label="Overview",
                value="overview",
                description="Server reach, voice streams, latency & uptime",
                emoji=emojis.Echo
            ),
            discord.SelectOption(
                label="System Performance",
                value="system",
                description="CPU load, RAM usage, cores & host OS",
                emoji=emojis.CAT_UTILITY
            ),
            discord.SelectOption(
                label="Lavalink Cluster",
                value="lavalink",
                description="Audio node connections, active players & memory",
                emoji=emojis.MUSIC
            ),
            discord.SelectOption(
                label="Architecture & Network",
                value="architecture",
                description="Bot identity, ownership, shards & database state",
                emoji=emojis.CAT_INFO
            ),
        ]
        super().__init__(placeholder="Select statistics view...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("This statistics menu was requested by another user.", ephemeral=True)
        self.layout.current = self.values[0]
        self.layout.rebuild()
        await interaction.response.edit_message(view=self.layout)


class RefreshStatsBtn(ui.Button):
    def __init__(self, layout):
        super().__init__(style=discord.ButtonStyle.secondary, label="Refresh", emoji="🔄")
        self.layout = layout

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.layout.ctx.author.id:
            return await interaction.response.send_message("This statistics menu was requested by another user.", ephemeral=True)
        self.layout.rebuild()
        await interaction.response.edit_message(view=self.layout)


class StatsLayout(ui.LayoutView):
    def __init__(self, bot, ctx):
        super().__init__(timeout=120)
        self.bot_ref = bot
        self.ctx = ctx
        self.current = "overview"
        self.message = None
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        container = ui.Container(accent_colour=0x5865F2)

        if self.current == "overview":
            self._overview(container)
        elif self.current == "system":
            self._system(container)
        elif self.current == "lavalink":
            self._lavalink(container)
        else:
            self._architecture(container)

        container.add_item(ui.Separator())

        row_select = ui.ActionRow()
        row_select.add_item(StatsSelect(self.bot_ref, self.ctx, self))
        container.add_item(row_select)

        row_btns = ui.ActionRow()
        row_btns.add_item(RefreshStatsBtn(self))

        dash_url = getattr(Config, "DASHBOARD_URL", None)
        if dash_url:
            row_btns.add_item(ui.Button(label="Web Dashboard", url=dash_url, style=discord.ButtonStyle.link, emoji="🌐"))

        if getattr(Config, "SUPPORT_SERVER", None):
            row_btns.add_item(ui.Button(label="Support Server", url=Config.SUPPORT_SERVER, style=discord.ButtonStyle.link, emoji="💬"))

        container.add_item(row_btns)
        self.add_item(container)

    def _overview(self, container):
        bot = self.bot_ref
        guilds = len(bot.guilds)
        users = sum((g.member_count or 0) for g in bot.guilds)
        channels = sum(1 for _ in bot.get_all_channels())
        commands_count = len([c for c in bot.commands if not c.hidden])

        uptime_sec = int(time.time() - getattr(bot, "start_time", time.time()))
        uptime_str = format_uptime(uptime_sec)

        vcs = 0
        for vc in bot.voice_clients:
            try:
                conn_attr = getattr(vc, 'is_connected', None)
                if (callable(conn_attr) and conn_attr()) or (not callable(conn_attr) and conn_attr):
                    vcs += 1
            except Exception:
                pass

        playing_players = 0
        music_cog = bot.get_cog("Music")
        if music_cog and getattr(music_cog, "lavalink", None):
            try:
                playing_players = len([p for p in music_cog.lavalink.player_manager.players.values() if getattr(p, "is_playing", False)])
            except Exception:
                pass

        api_ms = round(bot.latency * 1000)
        badge = get_latency_badge(api_ms)
        shards = bot.shard_count or 1

        header_text = ui.TextDisplay(
            f"### {emojis.Echo} **{bot.user.name} Telemetry & Operations**\n"
            f"-# *Lossless 320kbps Audio Engine • Live Telemetry Overview*"
        )
        try:
            bot_avatar_url = bot.user.display_avatar.url
            header_section = ui.Section(
                header_text,
                accessory=ui.Thumbnail(media=discord.UnfurledMediaItem(url=str(bot_avatar_url)))
            )
            container.add_item(header_section)
        except Exception:
            container.add_item(header_text)

        container.add_item(ui.Separator())

        overview_text = (
            f"### 📊 **Global Reach**\n"
            f"{emojis.DOT} **Guild Servers:** `{guilds:,}` guilds\n"
            f"{emojis.DOT} **Total User Reach:** `{users:,}` members\n"
            f"{emojis.DOT} **Channels Monitored:** `{channels:,}` channels\n"
            f"{emojis.DOT} **Commands Registered:** `{commands_count}` commands\n\n"
            f"### 🎵 **Audio Streams & Connections**\n"
            f"{emojis.DOT} **Active Voice Channels:** `{vcs}` connected\n"
            f"{emojis.DOT} **Active Music Streams:** `{playing_players}` playing\n\n"
            f"### ⚡ **System Telemetry**\n"
            f"{emojis.DOT} **API Latency:** `{api_ms}ms` ({badge})\n"
            f"{emojis.DOT} **Shards Active:** `{shards}` shard(s)\n"
            f"{emojis.DOT} **System Uptime:** `{uptime_str}`"
        )
        container.add_item(ui.TextDisplay(overview_text))
        container.add_item(ui.TextDisplay(f"-# Use the select menu below to switch telemetry views."))

    def _system(self, container):
        proc = psutil.Process()
        mem = proc.memory_info()
        ram_mb = mem.rss / (1024 * 1024)
        sys_mem = psutil.virtual_memory()
        total_ram_gb = sys_mem.total / (1024 * 1024 * 1024)
        ram_pct = (mem.rss / sys_mem.total) * 100.0

        try:
            cpu_pct = proc.cpu_percent(interval=None)
        except Exception:
            cpu_pct = psutil.cpu_percent()
        
        cpu_cores = psutil.cpu_count(logical=True)
        threads = proc.num_threads()

        cpu_bar = make_progress_bar(cpu_pct, 12)
        ram_bar = make_progress_bar(ram_pct, 12)

        container.add_item(ui.TextDisplay(f"### {emojis.CAT_UTILITY} **Hardware & System Metrics**"))
        container.add_item(ui.TextDisplay(f"*Real-time host server load, RAM allocation & runtime engine*"))
        container.add_item(ui.Separator())

        sys_text = (
            f"### {emojis.RAM} **Memory Utilization (RAM)**\n"
            f"{emojis.DOT} **Process Footprint:** `{ram_mb:.1f} MB` / `{total_ram_gb:.1f} GB`\n"
            f"{emojis.DOT} **Usage Allocation:** {ram_bar}\n\n"
            f"### {emojis.CPU} **CPU Processing**\n"
            f"{emojis.DOT} **CPU Usage:** `{cpu_pct:.1f}%` (`{cpu_cores}` Cores)\n"
            f"{emojis.DOT} **Compute Load:** {cpu_bar}\n"
            f"{emojis.DOT} **Active Threads:** `{threads}` execution threads\n\n"
            f"### {emojis.TERMINAL} **Runtime Environment**\n"
            f"{emojis.DOT} **Host OS:** `{platform.system()} {platform.release()}` ({platform.machine()})\n"
            f"{emojis.DOT} **Python Engine:** `{platform.python_version()}`\n"
            f"{emojis.DOT} **discord.py Library:** `{discord.__version__}`"
        )
        container.add_item(ui.TextDisplay(sys_text))
        container.add_item(ui.TextDisplay(f"-# Click Refresh to update live hardware utilization."))

    def _lavalink(self, container):
        container.add_item(ui.TextDisplay(f"### {emojis.MUSIC} **Lavalink Audio Cluster**"))
        container.add_item(ui.TextDisplay(f"*High-fidelity audio stream nodes & playback cluster status*"))
        container.add_item(ui.Separator())

        music_cog = self.bot_ref.get_cog("Music")
        if not music_cog or not getattr(music_cog, "lavalink", None):
            container.add_item(ui.TextDisplay(f"{emojis.INFO} No active Lavalink client initialized."))
            return

        nodes = music_cog.lavalink.node_manager.nodes
        if not nodes:
            container.add_item(ui.TextDisplay(f"{emojis.INFO} No audio nodes currently configured."))
            return

        node_blocks = []
        for node in nodes:
            is_avail = getattr(node, "available", False)
            status = f"{emojis.SUCCESS} **Online**" if is_avail else f"{emojis.ERROR} **Offline**"
            players = [p for p in music_cog.lavalink.player_manager.players.values() if getattr(p, "node", None) == node]
            playing = [p for p in players if getattr(p, "is_playing", False)]
            
            stats = getattr(node, "stats", None)
            if stats and is_avail:
                uptime_ms = getattr(stats, "uptime", 0) // 1000
                node_up = format_uptime(uptime_ms)
                mem_used = getattr(stats, "memory_used", 0) / (1024 * 1024)
                mem_alloc = getattr(stats, "memory_allocated", 0) / (1024 * 1024)
                lavalink_load = getattr(stats, "lavalink_load", 0) * 100
                
                node_text = (
                    f"**Node Name:** `{node.name}` ({status})\n"
                    f"{emojis.DOT} **Address:** `{node.host}:{node.port}`\n"
                    f"{emojis.DOT} **Players:** `{len(players)}` total (`{len(playing)}` active streaming)\n"
                    f"{emojis.DOT} **Node Uptime:** `{node_up}`\n"
                    f"{emojis.DOT} **Node Load:** `{lavalink_load:.1f}%` CPU  •  **RAM:** `{mem_used:.1f} MB` / `{mem_alloc:.1f} MB`"
                )
            else:
                node_text = (
                    f"**Node Name:** `{node.name}` ({status})\n"
                    f"{emojis.DOT} **Address:** `{node.host}:{node.port}`\n"
                    f"{emojis.DOT} **Players:** `{len(players)}` total (`{len(playing)}` streaming)"
                )
            node_blocks.append(node_text)

        container.add_item(ui.TextDisplay("\n\n".join(node_blocks)))

    def _architecture(self, container):
        bot = self.bot_ref
        owner_name = "Bot Developer"
        if getattr(bot, "owner_id", None):
            owner_u = bot.get_user(bot.owner_id)
            if owner_u:
                owner_name = f"{owner_u.name}"

        start_ts = int(getattr(bot, "start_time", time.time()))

        container.add_item(ui.TextDisplay(f"### {emojis.CAT_INFO} **Architecture & Network Telemetry**"))
        container.add_item(ui.TextDisplay(f"*Bot identity, infrastructure routing & database state*"))
        container.add_item(ui.Separator())

        arch_text = (
            f"### 🛡️ **Identity & Ownership**\n"
            f"{emojis.DOT} **Application:** `{bot.user.name}`\n"
            f"{emojis.DOT} **Application ID:** `{bot.user.id}`\n"
            f"{emojis.CROWN} **Bot Owner:** `{owner_name}`\n"
            f"{emojis.DOT} **Online Since:** <t:{start_ts}:F> (<t:{start_ts}:R>)\n\n"
            f"### {emojis.SHARD} **Network & Routing**\n"
            f"{emojis.DOT} **Shard Count:** `{bot.shard_count or 1}` shard(s)\n"
            f"{emojis.DOT} **Gateway API Latency:** `{round(bot.latency * 1000)}ms`\n"
            f"{emojis.DOT} **Database Connection:** {emojis.SUCCESS} **Active & Healthy**\n"
            f"{emojis.DOT} **Default Prefix:** `{Config.DEFAULT_PREFIX}`"
        )
        container.add_item(ui.TextDisplay(arch_text))


class PingLayout(ui.LayoutView):
    def __init__(self, bot, ctx, initial_mlat: int = 0):
        super().__init__(timeout=120)
        self.bot_ref = bot
        self.ctx = ctx
        self.mlat = initial_mlat
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        container = ui.Container(accent_colour=None)

        bot = self.bot_ref
        api = round(bot.latency * 1000)
        uptime = int(time.time() - getattr(bot, "start_time", time.time()))
        h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60

        if api < 150:
            status_badge = f"{emojis.SUCCESS} **Optimal Connection**"
        elif api < 350:
            status_badge = f"{emojis.INFO} **Good Signal**"
        else:
            status_badge = f"{emojis.ERROR} **High Latency**"

        container.add_item(ui.TextDisplay(f"### {emojis.Echo} **Pong! Telemetry Active**"))
        container.add_item(ui.Separator())

        mlat_text = f"`{self.mlat}ms`" if self.mlat > 0 else "`measuring...`"

        text = (
            f"{emojis.DOT} **Gateway API Ping:** `{api}ms`  •  {status_badge}\n"
            f"{emojis.DOT} **Message Roundtrip:** {mlat_text}\n"
            f"{emojis.DOT} **Engine Uptime:** `{h}h {m}m {s}s`\n"
            f"{emojis.DOT} **Active Commands:** `{len(bot.commands)}` registered"
        )
        container.add_item(ui.TextDisplay(text))
        container.add_item(ui.Separator())

        row = ui.ActionRow()
        row.add_item(RePingButton(self.bot_ref, self.ctx, self))
        container.add_item(row)

        self.add_item(container)


class RePingButton(ui.Button):
    def __init__(self, bot, ctx, layout):
        self.bot_ref = bot
        self.ctx = ctx
        self.layout = layout
        super().__init__(style=discord.ButtonStyle.secondary, label="Re-Ping Telemetry", emoji=emojis.RELOAD)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Run /ping to measure your latency.", ephemeral=True)
        
        start = time.perf_counter()
        await interaction.response.defer()
        end = time.perf_counter()
        
        mlat = round((end - start) * 1000)
        self.layout.mlat = mlat
        self.layout.rebuild()
        await interaction.edit_original_response(view=self.layout)


class Info(commands.Cog, name="Information"):
    """Information & stats commands."""

    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="help", aliases=["h"])
    async def help_cmd(self, ctx, *, command: str = None):
        """Show help menu or command details."""
        prefix = Config.DEFAULT_PREFIX
        if ctx.guild:
            prefix = await self.bot.db.get_prefix(ctx.guild.id) or Config.DEFAULT_PREFIX

        if command:
            cmd = self.bot.get_command(command)
            if not cmd:
                view = make_text_container(f"{emojis.ERROR} No command named `{command}`.")
                return await ctx.reply(view=view)
            aliases = ", ".join(f"`{a}`" for a in cmd.aliases) if cmd.aliases else "`None`"
            text = (
                f"### Command: {cmd.name}\n"
                f"{emojis.DOT} **Category:** `{cmd.cog_name or 'None'}`\n"
                f"{emojis.DOT} **Description:** {cmd.help or 'No description'}\n"
                f"{emojis.DOT} **Aliases:** {aliases}\n"
                f"{emojis.DOT} **Usage:** `{prefix}{cmd.qualified_name} {cmd.signature}`"
            )
            view = make_text_container(text)
            return await ctx.reply(view=view)

        layout = HelpLayout(self.bot, ctx, prefix)
        msg = await ctx.reply(view=layout)
        layout.message = msg

    @commands.hybrid_command(name="ping")
    async def ping(self, ctx):
        """Check bot network latency & cluster telemetry."""
        start = time.perf_counter()
        loading = make_text_container(f"{emojis.LOADING} Measuring network latency...")
        msg = await ctx.reply(view=loading)
        end = time.perf_counter()
        mlat = round((end - start) * 1000)

        layout = PingLayout(self.bot, ctx, initial_mlat=mlat)
        await msg.edit(view=layout)

    @commands.hybrid_command(name="stats", aliases=["botinfo", "bi"])
    async def stats(self, ctx):
        """Detailed bot statistics."""
        layout = StatsLayout(self.bot, ctx)
        msg = await ctx.reply(view=layout)
        layout.message = msg

    @commands.hybrid_command(name="invite")
    async def invite(self, ctx):
        """Get bot invite link."""
        url = discord.utils.oauth_url(self.bot.user.id, permissions=discord.Permissions(8))
        layout = ui.LayoutView()
        container = ui.Container(accent_colour=None)
        container.add_item(ui.TextDisplay(f"### {emojis.INFO} Invite {self.bot.user.name} to your server"))
        container.add_item(ui.Separator())
        row = ui.ActionRow()
        row.add_item(ui.Button(label="Invite", url=url, style=discord.ButtonStyle.link))
        if Config.SUPPORT_SERVER:
            row.add_item(ui.Button(label="Support", url=Config.SUPPORT_SERVER, style=discord.ButtonStyle.link))
        container.add_item(row)
        layout.add_item(container)
        await ctx.reply(view=layout)

    @commands.hybrid_command(name="support")
    async def support(self, ctx):
        """Get support server link."""
        layout = ui.LayoutView()
        container = ui.Container(accent_colour=None)
        container.add_item(ui.TextDisplay("### Need help? Join our support server."))
        container.add_item(ui.Separator())
        row = ui.ActionRow()
        row.add_item(ui.Button(label="Support Server", url=Config.SUPPORT_SERVER, style=discord.ButtonStyle.link))
        container.add_item(row)
        layout.add_item(container)
        await ctx.reply(view=layout)

    @commands.hybrid_command(name="membercount", aliases=["mc"])
    async def membercount(self, ctx):
        """Show server member count."""
        g = ctx.guild
        bots = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        online = sum(1 for m in g.members if m.status != discord.Status.offline)

        text = (
            f"### Member Count\n"
            f"```yaml\n"
            f"Total Members : {g.member_count}\n"
            f"Humans        : {humans}\n"
            f"Bots          : {bots}\n"
            f"Online        : {online}\n"
            f"```"
        )
        view = make_text_container(text)
        await ctx.reply(view=view)


async def setup(bot):
    await bot.add_cog(Info(bot))