import discord
from discord.ext import commands
from discord import ui
from config import Config
import emojis
import datetime
import time
import asyncio
from utils.helpers import send_log_webhook


def make_text_container(text: str) -> ui.LayoutView:
    view = ui.LayoutView()
    container = ui.Container(accent_colour=None)
    container.add_item(ui.TextDisplay(text))
    view.add_item(container)
    return view


def make_mention_view(bot, author, prefix: str) -> ui.LayoutView:
    view = ui.LayoutView()
    container = ui.Container(accent_colour=None)

    uptime = int(time.time() - getattr(bot, "start_time", time.time()))
    h, m = uptime // 3600, (uptime % 3600) // 60
    lat = round(bot.latency * 1000)
    dash_url = getattr(Config, "DASHBOARD_URL", "http://localhost:3000")
    support_url = getattr(Config, "SUPPORT_SERVER", "")
    invite_url = discord.utils.oauth_url(bot.user.id, permissions=discord.Permissions(8)) if (bot and bot.user) else "https://discord.com"

    container.add_item(ui.TextDisplay(f"### {emojis.Echo} **Hey {author.name}! Meet {bot.user.name}**"))
    container.add_item(ui.TextDisplay(
        f"*{emojis.MUSIC} Premium 320kbps studio audio engine, custom playlists & real-time web console.*\n"
        f"{emojis.DOT} Need assistance? Type `{prefix}help` or `/help` to open the command console."
    ))
    container.add_item(ui.Separator())

    info_text = (
        f"### {emojis.CAT_CONFIG} **Quick Configuration & Telemetry**\n"
        f"{emojis.DOT} **Guild Command Prefix:** `{prefix}`\n"
        f"{emojis.DOT} **Interactive Help:** Type `{prefix}help` or `/help`\n"
        f"{emojis.DOT} **24/7 Voice Mode:** Type `{prefix}247` to enable non-stop voice playback\n"
        f"{emojis.DOT} **Gateway Latency:** `{lat}ms`  •  **Uptime:** `{h}h {m}m`  •  **Servers:** `{len(bot.guilds)}`\n"
        f"{emojis.DOT} **Real-time Web Dashboard:** [Open Console]({dash_url})"
    )
    container.add_item(ui.TextDisplay(info_text))
    container.add_item(ui.Separator())

    # Interactive Link Buttons Row
    row = ui.ActionRow()
    row.add_item(ui.Button(style=discord.ButtonStyle.link, label="Web Dashboard", url=dash_url, emoji=emojis.Echo))
    if support_url and support_url.startswith("http"):
        row.add_item(ui.Button(style=discord.ButtonStyle.link, label="Support Server", url=support_url, emoji=emojis.INFO))
    row.add_item(ui.Button(style=discord.ButtonStyle.link, label="Invite Bot", url=invite_url, emoji=emojis.SUCCESS))
    container.add_item(row)

    view.add_item(container)
    return view


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or not self.bot.user:
            return
        if self.bot.user in message.mentions and not message.mention_everyone:
            content = message.content
            for u in message.mentions:
                content = content.replace(f"<@{u.id}>", "").replace(f"<@!{u.id}>", "")
            if not content.strip():
                try:
                    prefix = await self.bot.db.get_prefix(message.guild.id) or Config.DEFAULT_PREFIX
                    view = make_mention_view(self.bot, message.author, prefix)
                    await message.reply(view=view)
                except Exception as e:
                    print(f"[Mention Error] {e}")

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            perms = ", ".join(f"`{p}`" for p in error.missing_permissions)
            view = make_text_container(f"{emojis.ERROR} You need {perms} permission(s).")
            return await ctx.reply(view=view)
        if isinstance(error, commands.BotMissingPermissions):
            perms = ", ".join(f"`{p}`" for p in error.missing_permissions)
            view = make_text_container(f"{emojis.ERROR} I need {perms} permission(s).")
            return await ctx.reply(view=view)
        if isinstance(error, commands.MissingRequiredArgument):
            text = (
                f"{emojis.ERROR} Missing argument: `{error.param.name}`\n"
                f"Usage: `>{ctx.command.qualified_name} {ctx.command.signature}`"
            )
            view = make_text_container(text)
            return await ctx.reply(view=view)
        if isinstance(error, commands.CommandOnCooldown):
            view = make_text_container(f"Slow down! Try again in `{error.retry_after:.1f}s`.")
            return await ctx.reply(view=view)
        if isinstance(error, commands.NotOwner):
            view = make_text_container(f"{emojis.ERROR} Owner-only command.")
            return await ctx.reply(view=view)

        orig_error = getattr(error, "original", error)
        if isinstance(orig_error, (asyncio.TimeoutError, TimeoutError)):
            view = make_text_container(f"{emojis.ERROR} The operation timed out. Please try again.")
            return await ctx.reply(view=view)
        if isinstance(orig_error, discord.NotFound) or (isinstance(orig_error, discord.HTTPException) and getattr(orig_error, "code", 0) == 10008):
            return

        view = make_text_container(f"{emojis.ERROR} An error occurred:\n```py\n{str(error)[:500]}\n```")
        await ctx.reply(view=view)

        # Webhook Error Log (Components V2)
        try:
            cmd_content = ctx.message.content if (ctx.message and ctx.message.content) else f"/{ctx.command.qualified_name}"
            embed = {
                "title": f"{emojis.ERROR} Command Failure",
                "fields": [
                    {"name": f"{emojis.TERMINAL} Command", "value": f"`{cmd_content}`"},
                    {"name": f"{emojis.CAT_CONFIG} Server", "value": f"**{ctx.guild.name}** (`{ctx.guild.id}`)" if ctx.guild else "Direct Message"},
                    {"name": f"{emojis.USER_ICO} User", "value": f"{ctx.author.mention} (`{ctx.author.id}`)"},
                    {"name": f"{emojis.ERROR} Traceback Exception", "value": f"```py\n{str(error)[:800]}\n```"}
                ],
                "color": 16726072,  # #ff3838
                "thumbnail": {"url": str(ctx.author.display_avatar.url) if ctx.author else None},
                "footer": {"text": "Echo Logs • Crash Reporter"}
            }
            await send_log_webhook(Config.ERROR_LOG_WEBHOOK_URL, self.bot, embed)
        except Exception as e:
            print(f"[Webhook Log] Error sending error log: {e}")

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        # Webhook Join Log (Components V2)
        try:
            owner_mention = guild.owner.mention if guild.owner else "Unknown Owner"
            owner_name = str(guild.owner) if guild.owner else "Unknown"
            embed = {
                "title": f"📥 Server Joined",
                "fields": [
                    {"name": "🏠 Server Name", "value": f"**{guild.name}**"},
                    {"name": "🆔 Server ID", "value": f"`{guild.id}`"},
                    {"name": f"{emojis.CROWN} Server Owner", "value": f"{owner_mention} (`{owner_name}`)"},
                    {"name": "👥 Member Count", "value": f"`{guild.member_count:,}`"},
                    {"name": "📅 Created Date", "value": f"<t:{int(guild.created_at.timestamp())}:F> (<t:{int(guild.created_at.timestamp())}:R>)"}
                ],
                "color": 3066993,  # #2ecc71 Green
                "thumbnail": {"url": str(guild.icon.url) if guild.icon else None},
                "footer": {"text": "Echo Logs • System Administration"}
            }
            await send_log_webhook(Config.JOIN_LOG_WEBHOOK_URL, self.bot, embed)
        except Exception as e:
            print(f"[Webhook Log] Error sending join log: {e}")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        # Webhook Leave Log (Components V2)
        try:
            embed = {
                "title": f"📤 Server Left",
                "fields": [
                    {"name": "🏠 Server Name", "value": f"**{guild.name}**"},
                    {"name": "🆔 Server ID", "value": f"`{guild.id}`"},
                    {"name": "👥 Final Member Count", "value": f"`{guild.member_count:,}`"}
                ],
                "color": 15158332,  # #e74c3c Red
                "thumbnail": {"url": str(guild.icon.url) if guild.icon else None},
                "footer": {"text": "Echo Logs • System Administration"}
            }
            await send_log_webhook(Config.LEAVE_LOG_WEBHOOK_URL, self.bot, embed)
        except Exception as e:
            print(f"[Webhook Log] Error sending leave log: {e}")

    @commands.Cog.listener()
    async def on_command(self, ctx):
        # Webhook Command Execution Log (Components V2)
        try:
            cmd_content = ctx.message.content if (ctx.message and ctx.message.content) else f"/{ctx.command.qualified_name}"
            embed = {
                "title": f"{emojis.TERMINAL} Command Executed",
                "fields": [
                    {"name": "💻 Command", "value": f"`{cmd_content}`"},
                    {"name": f"{emojis.USER_ICO} User", "value": f"{ctx.author.mention} (`{ctx.author.name}` | ID: `{ctx.author.id}`)"},
                    {"name": "📍 Channel", "value": f"{ctx.channel.mention} (ID: `{ctx.channel.id}`)" if hasattr(ctx.channel, 'mention') else "DM"},
                    {"name": "🏠 Server", "value": f"**{ctx.guild.name}** (ID: `{ctx.guild.id}`)" if ctx.guild else "Direct Message"}
                ],
                "color": 10181046,  # #9b59b6 Purple
                "thumbnail": {"url": str(ctx.author.display_avatar.url) if ctx.author else None},
                "footer": {"text": "Echo Logs • Commands Monitor"}
            }
            await send_log_webhook(Config.COMMAND_LOG_WEBHOOK_URL, self.bot, embed)
        except Exception as e:
            print(f"[Webhook Log] Error sending command log: {e}")


async def setup(bot):
    await bot.add_cog(Events(bot))