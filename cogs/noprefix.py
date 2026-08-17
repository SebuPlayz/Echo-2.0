"""
Lets the bot owner grant specific users the ability to run commands
without typing the server prefix. Only fires for real command names,
so normal chat is never touched.
"""

import discord
from discord.ext import commands, tasks
from discord import ui
import time

import emojis
from config import Config


def make_text_container(text: str, color: int = None) -> ui.LayoutView:
    view = ui.LayoutView()
    container = ui.Container(accent_colour=color)
    container.add_item(ui.TextDisplay(text))
    view.add_item(container)
    return view


DURATION_MAP = {
    "1h": 3600,
    "1d": 86400,
    "7d": 604800,
    "30d": 2592000,
    "lifetime": None,
}


async def send_noprefix_grant_dm(bot, user_id: int, added_by_id: int, expires_at: int = None):
    """Send a premium Component V2 Direct Message when NoPrefix is granted."""
    try:
        user = bot.get_user(user_id)
        if not user and hasattr(bot, "fetch_user"):
            try:
                user = await bot.fetch_user(user_id)
            except Exception:
                user = None
        if not user:
            return
        
        bot_name = bot.user.name if bot.user else "Echo"
        expiry_text = "Never (Lifetime Access)" if not expires_at else f"<t:{expires_at}:F> (<t:{expires_at}:R>)"
        
        text = (
            f"### {emojis.CROWN} **NoPrefix Access Granted!**\n"
            f"{emojis.SUCCESS} Congratulations! You have been granted **NoPrefix Access** on **{bot_name}**.\n\n"
            f"{emojis.DOT} **Granted By:** <@{added_by_id}>\n"
            f"{emojis.DOT} **Duration / Expiry:** {expiry_text}\n\n"
            f"{emojis.INFO} **What can you do?**\n"
            f"{emojis.ARROW} You can now execute any bot commands (e.g. `play`, `skip`, `queue`, `volume`, `247`) in any server without typing a command prefix!"
        )
        
        view = make_text_container(text, color=0xf1c40f)  # Gold Accent
        await user.send(view=view)
    except Exception as e:
        print(f"[NoPrefix DM Error] Could not send grant DM to {user_id}: {e}")


async def send_noprefix_revoke_dm(bot, user_id: int, removed_by_id: int = None, is_expired: bool = False):
    """Send a premium Component V2 Direct Message when NoPrefix is revoked or expired."""
    try:
        user = bot.get_user(user_id)
        if not user and hasattr(bot, "fetch_user"):
            try:
                user = await bot.fetch_user(user_id)
            except Exception:
                user = None
        if not user:
            return
        
        bot_name = bot.user.name if bot.user else "Echo"
        
        if is_expired:
            text = (
                f"### {emojis.ERROR} **NoPrefix Access Expired**\n"
                f"{emojis.INFO} Your temporary **NoPrefix Access** on **{bot_name}** has expired.\n\n"
                f"{emojis.DOT} **Status:** Expired\n"
                f"{emojis.DOT} **Notice:** You will now need to use the server prefix to run commands.\n\n"
                f"{emojis.INFO} *Contact the bot owner if you'd like to extend your NoPrefix access!*"
            )
            color = 0xf39c12  # Amber/Orange Accent
        else:
            by_str = f"<@{removed_by_id}>" if removed_by_id else "Bot Owner"
            text = (
                f"### {emojis.ERROR} **NoPrefix Access Revoked**\n"
                f"{emojis.ERROR} Your **NoPrefix Access** on **{bot_name}** has been revoked.\n\n"
                f"{emojis.DOT} **Revoked By:** {by_str}\n"
                f"{emojis.DOT} **Notice:** You will now need to use the server prefix to run commands."
            )
            color = 0xe74c3c  # Red Accent
        
        view = make_text_container(text, color=color)
        await user.send(view=view)
    except Exception as e:
        print(f"[NoPrefix DM Error] Could not send revoke DM to {user_id}: {e}")


class NoPrefix(commands.Cog, name="NoPrefix"):
    """Owner-only NoPrefix management. Hidden from the public help menu."""

    def __init__(self, bot):
        self.bot = bot
        self.check_noprefix_expirations.start()

    def cog_unload(self):
        self.check_noprefix_expirations.cancel()

    @tasks.loop(minutes=1)
    async def check_noprefix_expirations(self):
        """Background loop to auto-expire temporary NoPrefix access and notify users via DM."""
        try:
            rows = await self.bot.db.list_noprefix()
            now = int(time.time())
            for user_id, added_by, added_at, expires_at in rows:
                if expires_at and int(expires_at) <= now:
                    await self.bot.db.remove_noprefix(user_id)
                    await send_noprefix_revoke_dm(self.bot, user_id, is_expired=True)
        except Exception as e:
            print(f"[NoPrefix Expiry Task Error] {e}")

    @check_noprefix_expirations.before_loop
    async def before_check_noprefix_expirations(self):
        await self.bot.wait_until_ready()

    def _looks_like_command(self, content: str) -> bool:
        """True only if the first word matches a real registered command/alias."""
        if not content:
            return False
        first_word = content.split(maxsplit=1)[0].lower()
        if not first_word:
            return False
        return self.bot.get_command(first_word) is not None

    # ── Smart NoPrefix execution ──────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        content = message.content.strip()
        if not content:
            return

        try:
            prefixes = await self.bot._get_prefix(self.bot, message)
        except Exception:
            prefixes = [Config.DEFAULT_PREFIX]
        if isinstance(prefixes, str):
            prefixes = [prefixes]
        for p in prefixes:
            if isinstance(p, str) and content.startswith(p):
                return

        lower_content = content.lower()
        if lower_content.startswith("ep ") or lower_content.startswith("ep\n"):
            query = content[3:].strip()
            if query:
                try:
                    prefixes = await self.bot._get_prefix(self.bot, message)
                except Exception:
                    prefixes = [Config.DEFAULT_PREFIX]
                if isinstance(prefixes, str):
                    prefixes = [prefixes]
                prefix = Config.DEFAULT_PREFIX
                for p in prefixes:
                    if p and isinstance(p, str) and not p.startswith("<@"):
                        prefix = p
                        break

                import copy
                fake_message = copy.copy(message)
                fake_message.content = f"{prefix}play {query}"
                new_ctx = await self.bot.get_context(fake_message)
                new_ctx.prefix = ""
                
                if new_ctx.command is not None:
                    try:
                        await self.bot.invoke(new_ctx)
                        return
                    except Exception as e:
                        print(f"[NoPrefix] Global Ep dispatch error for {message.author.id}: {e}")

        if not self._looks_like_command(content):
            return

        is_owner = await self.bot.is_owner(message.author)
        if not is_owner and not await self.bot.db.is_noprefix(message.author.id):
            return

        try:
            prefixes = await self.bot._get_prefix(self.bot, message)
        except Exception:
            prefixes = [Config.DEFAULT_PREFIX]
        if isinstance(prefixes, str):
            prefixes = [prefixes]
        prefix = Config.DEFAULT_PREFIX
        for p in prefixes:
            if p and isinstance(p, str):
                prefix = p
                break

        import copy
        fake_message = copy.copy(message)
        fake_message.content = prefix + content
        new_ctx = await self.bot.get_context(fake_message)
        new_ctx.prefix = ""
        
        if new_ctx.command is None:
            return
        try:
            await self.bot.invoke(new_ctx)
        except Exception as e:
            print(f"[NoPrefix] dispatch error for {message.author.id}: {e}")

    # ── Owner commands ───────────────────────────────────────────

    @commands.group(name="noprefix", aliases=["nop"], invoke_without_command=True, hidden=True)
    @commands.is_owner()
    async def noprefix(self, ctx):
        text = (
            f"### {emojis.CROWN} NoPrefix Management\n"
            f"{emojis.DOT} `{ctx.prefix}noprefix add @user [1h/1d/7d/30d/lifetime]`\n"
            f"{emojis.DOT} `{ctx.prefix}noprefix remove @user`\n"
            f"{emojis.DOT} `{ctx.prefix}noprefix list`\n"
            f"{emojis.DOT} `{ctx.prefix}noprefix check @user`"
        )
        await ctx.reply(view=make_text_container(text))

    @noprefix.command(name="add", hidden=True)
    @commands.is_owner()
    async def noprefix_add(self, ctx, member: discord.Member, duration: str = "lifetime"):
        duration = duration.lower()
        if duration not in DURATION_MAP:
            view = make_text_container(
                f"{emojis.ERROR} Invalid duration. Use one of: `1h`, `1d`, `7d`, `30d`, `lifetime`."
            )
            return await ctx.reply(view=view)

        seconds = DURATION_MAP[duration]
        expires_at = int(time.time()) + seconds if seconds else None

        await self.bot.db.add_noprefix(member.id, ctx.author.id, expires_at)
        await self.bot.db.log_audit_action(
            user_id=ctx.author.id,
            username=str(ctx.author),
            title="👑 NoPrefix Granted (Discord Command)",
            description=f"Granted NoPrefix to **{member}** (`{member.id}`). Duration: `{duration}`.",
            color=0xf1c40f
        )

        expiry_text = "Never (Lifetime)" if expires_at is None else f"<t:{expires_at}:R>"
        view = make_text_container(
            f"### {emojis.SUCCESS} NoPrefix Granted\n"
            f"{emojis.DOT} **User:** {member.mention}\n"
            f"{emojis.DOT} **Expires:** {expiry_text}\n"
            f"{emojis.DOT} **Granted by:** {ctx.author.mention}"
        )
        await ctx.reply(view=view)

        # Send DM using Component V2 helper
        await send_noprefix_grant_dm(self.bot, member.id, ctx.author.id, expires_at)

    @noprefix.command(name="remove", hidden=True)
    @commands.is_owner()
    async def noprefix_remove(self, ctx, member: discord.Member):
        existed = await self.bot.db.remove_noprefix(member.id)
        if not existed:
            view = make_text_container(f"{emojis.ERROR} {member.mention} doesn't have NoPrefix.")
            return await ctx.reply(view=view)

        await self.bot.db.log_audit_action(
            user_id=ctx.author.id,
            username=str(ctx.author),
            title="🗑️ NoPrefix Revoked (Discord Command)",
            description=f"Revoked NoPrefix access from **{member}** (`{member.id}`).",
            color=0xe74c3c
        )

        view = make_text_container(f"{emojis.SUCCESS} Removed NoPrefix from {member.mention}.")
        await ctx.reply(view=view)

        # Send DM using Component V2 helper
        await send_noprefix_revoke_dm(self.bot, member.id, ctx.author.id, is_expired=False)

    @noprefix.command(name="check", hidden=True)
    @commands.is_owner()
    async def noprefix_check(self, ctx, member: discord.Member):
        row = await self.bot.db.get_noprefix(member.id)
        if not row or not await self.bot.db.is_noprefix(member.id):
            view = make_text_container(f"{emojis.INFO} {member.mention} doesn't have active NoPrefix.")
            return await ctx.reply(view=view)

        _, added_by, added_at, expires_at = row
        expiry_text = "Never (Lifetime)" if expires_at is None else f"<t:{expires_at}:R>"
        granter = ctx.guild.get_member(added_by)
        granter_text = granter.mention if granter else f"`{added_by}`"

        view = make_text_container(
            f"### {emojis.INFO} NoPrefix Status\n"
            f"{emojis.DOT} **User:** {member.mention}\n"
            f"{emojis.DOT} **Granted by:** {granter_text}\n"
            f"{emojis.DOT} **Granted:** <t:{added_at}:R>\n"
            f"{emojis.DOT} **Expires:** {expiry_text}"
        )
        await ctx.reply(view=view)

    @noprefix.command(name="list", hidden=True)
    @commands.is_owner()
    async def noprefix_list(self, ctx):
        rows = await self.bot.db.list_noprefix()
        if not rows:
            view = make_text_container(f"{emojis.INFO} No users have NoPrefix.")
            return await ctx.reply(view=view)

        lines = []
        for user_id, added_by, added_at, expires_at in rows:
            expiry_text = "Lifetime" if expires_at is None else f"<t:{expires_at}:R>"
            lines.append(f"{emojis.DOT} <@{user_id}> — expires {expiry_text}")

        text = f"### {emojis.LIST_ICO if hasattr(emojis, 'LIST_ICO') else emojis.INFO} NoPrefix Users ({len(rows)})\n" + "\n".join(lines[:25])
        await ctx.reply(view=make_text_container(text))


async def setup(bot):
    await bot.add_cog(NoPrefix(bot))
