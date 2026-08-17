import sys
import logging
import asyncio
import aiohttp

# Configure stdout and stderr to handle UTF-8 symbols gracefully
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import discord
from discord.ext import commands, tasks
import time
from config import Config
from utils.database import Database

# ── Monkeypatch discord.Client.connect to handle initial connection failures robustly ──
from discord.backoff import ExponentialBackoff
from discord.gateway import DiscordWebSocket, ReconnectWebSocket
from discord.errors import PrivilegedIntentsRequired, GatewayNotFound, ConnectionClosed, HTTPException

_discord_client_log = logging.getLogger('discord.client')

async def patched_connect(self, *, reconnect: bool = True) -> None:
    backoff = ExponentialBackoff()
    ws_params = {
        'initial': True,
        'shard_id': self.shard_id,
    }
    while not self.is_closed():
        try:
            coro = DiscordWebSocket.from_client(self, **ws_params)
            self.ws = await asyncio.wait_for(coro, timeout=60.0)
            ws_params['initial'] = False
            while True:
                await self.ws.poll_event()
        except ReconnectWebSocket as e:
            _discord_client_log.debug('Got a request to %s the websocket.', e.op)
            self.dispatch('disconnect')
            if self.ws:
                ws_params.update(sequence=self.ws.sequence, resume=e.resume, session=self.ws.session_id)
                if e.resume:
                    ws_params['gateway'] = self.ws.gateway
            else:
                ws_params.update(sequence=None, resume=False, session=None)
            continue
        except (
            OSError,
            HTTPException,
            GatewayNotFound,
            ConnectionClosed,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as exc:
            self.dispatch('disconnect')
            if not reconnect:
                await self.close()
                if isinstance(exc, ConnectionClosed) and exc.code == 1000:
                    return
                raise

            if self.is_closed():
                return

            if isinstance(exc, OSError) and exc.errno in (54, 10054) and self.ws:
                ws_params.update(
                    sequence=self.ws.sequence,
                    gateway=self.ws.gateway,
                    initial=False,
                    resume=True,
                    session=self.ws.session_id,
                )
                continue

            if isinstance(exc, ConnectionClosed):
                if exc.code == 4014:
                    raise PrivilegedIntentsRequired(exc.shard_id) from None
                if exc.code != 1000:
                    await self.close()
                    raise

            retry = backoff.delay()
            _discord_client_log.warning("Attempting a reconnect in %.2fs due to connection error: %r", retry, exc)
            await asyncio.sleep(retry)
            
            if self.ws:
                ws_params.update(
                    sequence=self.ws.sequence,
                    gateway=self.ws.gateway,
                    resume=True,
                    session=self.ws.session_id,
                )
            else:
                ws_params.update(
                    sequence=None,
                    gateway=None,
                    resume=False,
                    session=None,
                )

discord.Client.connect = patched_connect


class EchoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=self._get_prefix,
            intents=intents,
            help_command=None,
            owner_ids=set(Config.OWNER_IDS),
            case_insensitive=True,
            strip_after_prefix=True
        )
        self.db = Database()
        self.start_time = time.time()
        self.dashboard_broadcast = None  # gets set when dashboard boots
        self.dashboard_task = None
        self.current_status = discord.Status.dnd
        self.auto_presence = True

    async def _get_prefix(self, bot, message):
        if not message.guild:
            return commands.when_mentioned_or(Config.DEFAULT_PREFIX)(bot, message)
        prefix = await self.db.get_prefix(message.guild.id)
        base = prefix or Config.DEFAULT_PREFIX
        return commands.when_mentioned_or(base)(bot, message)

    async def setup_hook(self):
        await self.db.init()

        await self._autoload_emojis()
        await self._autoload_cogs()

        # dashboard shares the bot's event loop + db, no separate connection needed
        if Config.DASHBOARD_ENABLED:
            from dashboard.app import run_dashboard
            self.dashboard_task = self.loop.create_task(run_dashboard(self), name="dashboard")

            def _on_dashboard_done(task: asyncio.Task):
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is not None:
                    print(f"  ⚠ Dashboard task ended with an error: {exc!r}")

            self.dashboard_task.add_done_callback(_on_dashboard_done)

        # Start the status updater background task
        self.status_updater.start()

    async def close(self):
        # cancel the dashboard task cleanly so uvicorn shuts down properly
        # instead of getting killed mid-request on restart
        if self.dashboard_task and not self.dashboard_task.done():
            self.dashboard_task.cancel()
            try:
                await self.dashboard_task
            except asyncio.CancelledError:
                pass
        self.status_updater.cancel()
        await super().close()

    async def _autoload_emojis(self):
        # syncs emoji before cogs load since cogs need emojis.X at import time.
        # wrapped in a timeout so a slow network never blocks the bot from booting
        try:
            from scripts.upload_application_emojis import run_sync
            summary = await asyncio.wait_for(
                run_sync(token=Config.TOKEN, quiet=True, timeout=60.0),
                timeout=70.0,
            )
            if summary.get("uploaded"):
                import emojis
                emojis.reload()
                print(f"  [OK] Emoji sync: uploaded {summary['uploaded']} new emoji(s)")
            else:
                print(f"  [OK] Emoji sync: up to date ({summary.get('skipped_existing', 0)} cached)")
        except Exception as e:
            print(f"  [WARN] Emoji sync skipped ({e}) - using cached/fallback emojis")

    async def _autoload_cogs(self):
        # auto-discovers cogs so we don't need a hardcoded list here
        import pkgutil
        import cogs as cogs_pkg

        cog_names = sorted(
            name for _, name, is_pkg in pkgutil.iter_modules(cogs_pkg.__path__)
            if not is_pkg and not name.startswith("_")
        )

        for cog in cog_names:
            try:
                await self.load_extension(f"cogs.{cog}")
                print(f"  ✔ Loaded: {cog}")
            except Exception as e:
                print(f"  ✖ Failed: {cog} -> {e}")

    async def on_ready(self):
        bot_name = self.user.name
        guilds_cnt = len(self.guilds)
        users_cnt = sum(g.member_count or 0 for g in self.guilds)

        banner = (
            f"\n┌──────────────────────────────────────────────────┐\n"
            f"│  🌹  {bot_name:<41} │\n"
            f"├──────────────────────────────────────────────────┤\n"
            f"│  • Status     : Online & Ready 🟢                │\n"
            f"│  • Servers    : {guilds_cnt:<32} │\n"
            f"│  • Users      : {users_cnt:<32} │\n"
            f"│  • Default Prefix: {Config.DEFAULT_PREFIX:<27} │\n"
            f"└──────────────────────────────────────────────────┘"
        )
        print(banner)

        # Clear guild-specific duplicate command copies and sync global slash commands cleanly
        try:
            for guild in self.guilds:
                try:
                    self.tree.clear_commands(guild=guild)
                    await self.tree.sync(guild=guild)
                except Exception:
                    pass
            synced = await self.tree.sync()
            print(f"  ✔ Synced {len(synced)} clean global slash command(s) to Discord\n")
        except Exception as e:
            print(f"  ✖ Slash command sync warning: {e}\n")

    @tasks.loop(seconds=30)
    async def status_updater(self):
        if not self.is_ready() or not self.auto_presence:
            return

        guild_count = len(self.guilds)
        member_count = sum(g.member_count for g in self.guilds)

        if not hasattr(self, "_status_index"):
            self._status_index = 0

        activities = [
            discord.Activity(
                type=discord.ActivityType.listening,
                name=f"{Config.DEFAULT_PREFIX}help"
            ),
            discord.Activity(
                type=discord.ActivityType.listening,
                name=f"{guild_count} servers"
            ),
            discord.Activity(
                type=discord.ActivityType.listening,
                name=f"{member_count} users"
            )
        ]

        try:
            current_activity = activities[self._status_index % len(activities)]
            await self.change_presence(
                activity=current_activity,
                status=self.current_status
            )
            self._status_index += 1
        except Exception as e:
            print(f"[StatusUpdater] Error: {e}")


async def main():
    bot = EchoBot()
    async with bot:
        await bot.start(Config.TOKEN)


if __name__ == "__main__":
    asyncio.run(main())