import aiosqlite
import time
import random
import string


class Database:
    def __init__(self):
        self.path = "Echo.db"

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS guilds (
                    guild_id INTEGER PRIMARY KEY,
                    prefix TEXT DEFAULT '>',
                    twentyfourseven INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS liked_songs (
                    user_id INTEGER,
                    track_title TEXT,
                    track_author TEXT,
                    track_uri TEXT,
                    added_at INTEGER
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS noprefix (
                    user_id    INTEGER PRIMARY KEY,
                    added_by   INTEGER NOT NULL,
                    added_at   INTEGER NOT NULL,
                    expires_at INTEGER
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    code TEXT UNIQUE,
                    is_public INTEGER DEFAULT 0
                )
            """)
            # Safe migration: check column existence via PRAGMA before adding
            cur = await db.execute("PRAGMA table_info(playlists)")
            existing_cols = {row[1] for row in await cur.fetchall()}
            if "code" not in existing_cols:
                await db.execute("ALTER TABLE playlists ADD COLUMN code TEXT")
                await db.commit()
            if "is_public" not in existing_cols:
                await db.execute("ALTER TABLE playlists ADD COLUMN is_public INTEGER DEFAULT 0")
                await db.commit()

            # Safe migration: check guilds columns
            cur = await db.execute("PRAGMA table_info(guilds)")
            existing_guild_cols = {row[1] for row in await cur.fetchall()}
            if "twentyfourseven_channel_id" not in existing_guild_cols:
                await db.execute("ALTER TABLE guilds ADD COLUMN twentyfourseven_channel_id INTEGER")
                await db.commit()
            if "default_volume" not in existing_guild_cols:
                await db.execute("ALTER TABLE guilds ADD COLUMN default_volume INTEGER DEFAULT 100")
                await db.commit()
            if "announce_now_playing" not in existing_guild_cols:
                await db.execute("ALTER TABLE guilds ADD COLUMN announce_now_playing INTEGER DEFAULT 1")
                await db.commit()
            if "dj_role_id" not in existing_guild_cols:
                await db.execute("ALTER TABLE guilds ADD COLUMN dj_role_id INTEGER DEFAULT NULL")
                await db.commit()
            if "restricted_vc_id" not in existing_guild_cols:
                await db.execute("ALTER TABLE guilds ADD COLUMN restricted_vc_id INTEGER DEFAULT NULL")
                await db.commit()
            if "restricted_tc_id" not in existing_guild_cols:
                await db.execute("ALTER TABLE guilds ADD COLUMN restricted_tc_id INTEGER DEFAULT NULL")
                await db.commit()

            await db.execute("""
                CREATE TABLE IF NOT EXISTS playlist_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id INTEGER NOT NULL,
                    track_title TEXT NOT NULL,
                    track_author TEXT NOT NULL,
                    track_uri TEXT NOT NULL,
                    track_identifier TEXT,
                    added_at INTEGER NOT NULL,
                    FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS playlist_plays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id INTEGER NOT NULL,
                    played_at INTEGER NOT NULL,
                    FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS song_plays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    track_title TEXT NOT NULL,
                    track_author TEXT NOT NULL,
                    track_uri TEXT NOT NULL,
                    track_identifier TEXT NOT NULL,
                    played_at INTEGER NOT NULL
                )
            """)
            # Safe migration: check song_plays columns
            cur = await db.execute("PRAGMA table_info(song_plays)")
            existing_sp_cols = {row[1] for row in await cur.fetchall()}
            if "user_id" not in existing_sp_cols:
                await db.execute("ALTER TABLE song_plays ADD COLUMN user_id INTEGER DEFAULT 0")
                await db.commit()
            if "duration" not in existing_sp_cols:
                await db.execute("ALTER TABLE song_plays ADD COLUMN duration INTEGER DEFAULT 0")
                await db.commit()
            if "source" not in existing_sp_cols:
                await db.execute("ALTER TABLE song_plays ADD COLUMN source TEXT DEFAULT 'other'")
                await db.commit()
            if "artwork_url" not in existing_sp_cols:
                await db.execute("ALTER TABLE song_plays ADD COLUMN artwork_url TEXT")
                await db.commit()
            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    title TEXT,
                    description TEXT,
                    color INTEGER,
                    created_at INTEGER
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS home_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    type TEXT DEFAULT 'info',
                    color TEXT DEFAULT '#8b5cf6',
                    created_by INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER
                )
            """)
            await db.commit()

    # ── Prefix Methods ───────────────────────────────────────────

    async def get_prefix(self, guild_id):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT prefix FROM guilds WHERE guild_id = ?", (guild_id,))
            row = await cur.fetchone()
            return row[0] if row else None

    async def set_prefix(self, guild_id, prefix):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO guilds (guild_id, prefix) VALUES (?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET prefix = ?",
                (guild_id, prefix, prefix)
            )
            await db.commit()

    # ── 24/7 Methods ─────────────────────────────────────────────

    async def get_247(self, guild_id):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT twentyfourseven FROM guilds WHERE guild_id = ?", (guild_id,))
            row = await cur.fetchone()
            return bool(row[0]) if row else False

    async def set_247(self, guild_id: int, value: bool, channel_id: int = None):
        async with aiosqlite.connect(self.path) as db:
            if not value:
                await db.execute(
                    "INSERT INTO guilds (guild_id, twentyfourseven, twentyfourseven_channel_id) VALUES (?, 0, NULL) "
                    "ON CONFLICT(guild_id) DO UPDATE SET twentyfourseven = 0, twentyfourseven_channel_id = NULL",
                    (guild_id,)
                )
            else:
                if channel_id is not None:
                    await db.execute(
                        "INSERT INTO guilds (guild_id, twentyfourseven, twentyfourseven_channel_id) VALUES (?, 1, ?) "
                        "ON CONFLICT(guild_id) DO UPDATE SET twentyfourseven = 1, twentyfourseven_channel_id = ?",
                        (guild_id, channel_id, channel_id)
                    )
                else:
                    await db.execute(
                        "INSERT INTO guilds (guild_id, twentyfourseven) VALUES (?, 1) "
                        "ON CONFLICT(guild_id) DO UPDATE SET twentyfourseven = 1",
                        (guild_id,)
                    )
            await db.commit()

    async def set_247_channel(self, guild_id, channel_id):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO guilds (guild_id, twentyfourseven_channel_id) VALUES (?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET twentyfourseven_channel_id = ?",
                (guild_id, channel_id, channel_id)
            )
            await db.commit()

    async def get_247_channel(self, guild_id):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT twentyfourseven_channel_id FROM guilds WHERE guild_id = ?", (guild_id,))
            row = await cur.fetchone()
            return row[0] if row else None

    async def get_all_247_guilds(self):
        """Get list of (guild_id, twentyfourseven_channel_id) for active 24/7 guilds."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT guild_id, twentyfourseven_channel_id FROM guilds WHERE twentyfourseven = 1 AND twentyfourseven_channel_id IS NOT NULL")
            return await cur.fetchall()

    # ── Guild Settings Methods ────────────────────────────────────

    async def get_guild_settings(self, guild_id: int) -> dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM guilds WHERE guild_id = ?", (guild_id,))
            row = await cur.fetchone()
            if not row:
                return {
                    "prefix": ">",
                    "twentyfourseven": False,
                    "twentyfourseven_channel_id": None,
                    "default_volume": 100,
                    "announce_now_playing": True,
                    "dj_role_id": None,
                    "restricted_vc_id": None,
                    "restricted_tc_id": None
                }
            r = dict(row)
            return {
                "prefix": r.get("prefix") or ">",
                "twentyfourseven": bool(r.get("twentyfourseven")),
                "twentyfourseven_channel_id": str(r.get("twentyfourseven_channel_id")) if r.get("twentyfourseven_channel_id") else None,
                "default_volume": r.get("default_volume") if r.get("default_volume") is not None else 100,
                "announce_now_playing": bool(r.get("announce_now_playing", 1)) if r.get("announce_now_playing") is not None else True,
                "dj_role_id": str(r.get("dj_role_id")) if r.get("dj_role_id") else None,
                "restricted_vc_id": str(r.get("restricted_vc_id")) if r.get("restricted_vc_id") else None,
                "restricted_tc_id": str(r.get("restricted_tc_id")) if r.get("restricted_tc_id") else None
            }

    async def update_guild_settings(
        self,
        guild_id: int,
        prefix: str = ">",
        twentyfourseven: bool = False,
        twentyfourseven_channel_id: int = None,
        default_volume: int = 100,
        announce_now_playing: bool = True,
        dj_role_id: int = None,
        restricted_vc_id: int = None,
        restricted_tc_id: int = None
    ):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO guilds (
                    guild_id, prefix, twentyfourseven, twentyfourseven_channel_id, default_volume,
                    announce_now_playing, dj_role_id, restricted_vc_id, restricted_tc_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    prefix = ?,
                    twentyfourseven = ?,
                    twentyfourseven_channel_id = ?,
                    default_volume = ?,
                    announce_now_playing = ?,
                    dj_role_id = ?,
                    restricted_vc_id = ?,
                    restricted_tc_id = ?
                """,
                (
                    guild_id, prefix, int(twentyfourseven), twentyfourseven_channel_id, default_volume,
                    int(announce_now_playing), dj_role_id, restricted_vc_id, restricted_tc_id,
                    prefix, int(twentyfourseven), twentyfourseven_channel_id, default_volume,
                    int(announce_now_playing), dj_role_id, restricted_vc_id, restricted_tc_id
                )
            )
            await db.commit()

    async def set_dj_role(self, guild_id: int, role_id: int = None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO guilds (guild_id, dj_role_id) VALUES (?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET dj_role_id = ?",
                (guild_id, role_id, role_id)
            )
            await db.commit()

    # ── Liked Songs Methods ──────────────────────────────────────

    async def like_song(self, user_id, title, author, uri):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO liked_songs VALUES (?, ?, ?, ?, ?)",
                (user_id, title, author, uri, int(time.time()))
            )
            await db.commit()

    async def unlike_song(self, user_id, uri):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM liked_songs WHERE user_id = ? AND track_uri = ?",
                (user_id, uri)
            )
            await db.commit()

    async def is_liked(self, user_id, uri):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT 1 FROM liked_songs WHERE user_id = ? AND track_uri = ?",
                (user_id, uri)
            )
            return await cur.fetchone() is not None

    async def get_liked(self, user_id):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT * FROM liked_songs WHERE user_id = ? ORDER BY added_at DESC",
                (user_id,)
            )
            return await cur.fetchall()

    # ── NoPrefix Methods ─────────────────────────────────────────

    async def add_noprefix(self, user_id: int, added_by: int, expires_at: int = None):
        """Grant noprefix to a user."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO noprefix (user_id, added_by, added_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    added_by = excluded.added_by,
                    added_at = excluded.added_at,
                    expires_at = excluded.expires_at
                """,
                (user_id, added_by, int(time.time()), expires_at),
            )
            await db.commit()

    async def remove_noprefix(self, user_id: int) -> bool:
        """Remove noprefix from a user. Returns True if it existed."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "DELETE FROM noprefix WHERE user_id = ?", (user_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def is_noprefix(self, user_id: int) -> bool:
        """Check whether a user has active (non-expired) noprefix."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT expires_at FROM noprefix WHERE user_id = ?", (user_id,)
            )
            row = await cur.fetchone()
            if not row:
                return False
            expires_at = row[0]
            if expires_at is not None and expires_at < int(time.time()):
                # expired, clean it up
                await db.execute(
                    "DELETE FROM noprefix WHERE user_id = ?", (user_id,)
                )
                await db.commit()
                return False
            return True

    async def get_noprefix(self, user_id: int):
        """Return a user's noprefix info as a tuple, or None."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT user_id, added_by, added_at, expires_at FROM noprefix WHERE user_id = ?",
                (user_id,),
            )
            return await cur.fetchone()

    async def list_noprefix(self):
        """Return the list of all noprefix users."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT user_id, added_by, added_at, expires_at FROM noprefix"
            )
            return await cur.fetchall()

    # ── Playlist Methods ─────────────────────────────────────────

    def _generate_code(self) -> str:
        """Generate a random 8-character alphanumeric playlist code."""
        chars = string.ascii_uppercase + string.digits
        return ''.join(random.choices(chars, k=8))

    async def create_playlist(self, user_id: int, name: str) -> tuple:
        """Create a new playlist and return (id, code)."""
        async with aiosqlite.connect(self.path) as db:
            # Generate a unique code
            for _ in range(10):
                code = self._generate_code()
                cur = await db.execute("SELECT 1 FROM playlists WHERE code = ?", (code,))
                if not await cur.fetchone():
                    break
            cursor = await db.execute(
                "INSERT INTO playlists (user_id, name, created_at, code, is_public) VALUES (?, ?, ?, ?, 0)",
                (user_id, name, int(time.time()), code)
            )
            await db.commit()
            return cursor.lastrowid, code

    async def delete_playlist(self, user_id: int, playlist_id: int) -> bool:
        """Delete a playlist. Returns True if successful."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "DELETE FROM playlists WHERE id = ? AND user_id = ?",
                (playlist_id, user_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def rename_playlist(self, user_id: int, playlist_id: int, new_name: str) -> bool:
        """Rename a playlist. Returns True if successful."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE playlists SET name = ? WHERE id = ? AND user_id = ?",
                (new_name, playlist_id, user_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_playlists(self, user_id: int):
        """Get all playlists for a user, sorted by creation date."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """
                SELECT p.id, p.name, p.created_at, COUNT(t.id) as track_count, p.code, p.is_public
                FROM playlists p
                LEFT JOIN playlist_tracks t ON p.id = t.playlist_id
                WHERE p.user_id = ?
                GROUP BY p.id
                ORDER BY p.created_at DESC
                """,
                (user_id,)
            )
            return await cur.fetchall()

    async def get_playlist(self, user_id: int, playlist_id: int):
        """Get a single playlist entry."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT id, name, created_at, code, is_public FROM playlists WHERE id = ? AND user_id = ?",
                (playlist_id, user_id)
            )
            return await cur.fetchone()

    async def get_playlist_by_name(self, user_id: int, name: str):
        """Get a single playlist entry by name (case-insensitive)."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT id, name, created_at, code, is_public FROM playlists WHERE user_id = ? AND LOWER(name) = LOWER(?)",
                (user_id, name)
            )
            return await cur.fetchone()

    async def get_playlist_by_code(self, code: str):
        """Get a public playlist by its share code. Returns (id, name, user_id, track_count, is_public)."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """
                SELECT p.id, p.name, p.user_id, COUNT(t.id) as track_count, p.is_public, p.code
                FROM playlists p
                LEFT JOIN playlist_tracks t ON p.id = t.playlist_id
                WHERE p.code = ?
                GROUP BY p.id
                """,
                (code.upper(),)
            )
            return await cur.fetchone()

    async def set_playlist_privacy(self, user_id: int, playlist_id: int, is_public: bool) -> bool:
        """Set playlist privacy. Returns True if updated."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE playlists SET is_public = ? WHERE id = ? AND user_id = ?",
                (1 if is_public else 0, playlist_id, user_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def ensure_playlist_code(self, playlist_id: int) -> str:
        """Ensure a playlist has a code (for old playlists), return the code."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT code FROM playlists WHERE id = ?", (playlist_id,))
            row = await cur.fetchone()
            if row and row[0]:
                return row[0]
            # Generate new code
            for _ in range(10):
                code = self._generate_code()
                cur2 = await db.execute("SELECT 1 FROM playlists WHERE code = ?", (code,))
                if not await cur2.fetchone():
                    break
            await db.execute("UPDATE playlists SET code = ? WHERE id = ?", (code, playlist_id))
            await db.commit()
            return code

    async def get_playlist_tracks(self, playlist_id: int):
        """Get all tracks in a playlist, sorted by added date."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT id, track_title, track_author, track_uri, track_identifier, added_at "
                "FROM playlist_tracks WHERE playlist_id = ? ORDER BY added_at ASC",
                (playlist_id,)
            )
            return await cur.fetchall()

    async def add_to_playlist(self, playlist_id: int, title: str, author: str, uri: str, identifier: str = None) -> int:
        """Add a track to a playlist. Returns the track ID."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO playlist_tracks (playlist_id, track_title, track_author, track_uri, track_identifier, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (playlist_id, title, author, uri, identifier, int(time.time()))
            )
            await db.commit()
            return cursor.lastrowid

    async def remove_from_playlist(self, playlist_id: int, track_id: int) -> bool:
        """Remove a track from a playlist. Returns True if successful."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "DELETE FROM playlist_tracks WHERE id = ? AND playlist_id = ?",
                (track_id, playlist_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def record_playlist_play(self, playlist_id: int):
        """Record a playlist play event with the current timestamp."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO playlist_plays (playlist_id, played_at) VALUES (?, ?)",
                (playlist_id, int(time.time()))
            )
            await db.commit()

    async def get_playlist_leaderboard(self, timeframe: str = "all", limit: int = 10):
        """
        Get the most played public playlists.
        timeframe can be 'day', 'week', 'month', or 'all'.
        Returns list of (playlist_id, name, user_id, code, play_count, track_count).
        """
        since = 0
        now = int(time.time())
        if timeframe == "day":
            since = now - (60 * 60 * 24)
        elif timeframe == "week":
            since = now - (60 * 60 * 24 * 7)
        elif timeframe == "month":
            since = now - (60 * 60 * 24 * 30)

        query = """
            SELECT p.id, p.name, p.user_id, p.code, COUNT(DISTINCT pp.id) as play_count, COUNT(DISTINCT pt.id) as track_count
            FROM playlists p
            JOIN playlist_plays pp ON p.id = pp.playlist_id
            LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
            WHERE p.is_public = 1
        """
        
        params = []
        if since > 0:
            query += " AND pp.played_at >= ?"
            params.append(since)

        query += """
            GROUP BY p.id
            ORDER BY play_count DESC
            LIMIT ?
        """
        params.append(limit)

        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(query, tuple(params))
            return await cur.fetchall()

    # ── Music Stats & History Methods ────────────────────────────

    async def record_song_play(self, guild_id: int, title: str, author: str, uri: str, identifier: str, user_id: int = 0, duration: int = 0, source: str = "other", artwork_url: str = None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO song_plays (guild_id, user_id, track_title, track_author, track_uri, track_identifier, duration, source, artwork_url, played_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (guild_id, user_id, title, author, uri, identifier, duration, source, artwork_url, int(time.time()))
            )
            await db.commit()

    async def get_recently_played(self, guild_id, limit=15):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT track_title, track_author, track_uri, track_identifier, MAX(played_at) as last_played "
                "FROM song_plays WHERE guild_id = ? "
                "GROUP BY track_identifier "
                "ORDER BY last_played DESC LIMIT ?",
                (guild_id, limit)
            )
            rows = await cur.fetchall()
            return [
                {
                    "title": r[0],
                    "author": r[1],
                    "uri": r[2],
                    "identifier": r[3],
                    "played_at": r[4]
                } for r in rows
            ]

    async def get_top_played(self, guild_id, limit=10):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT track_title, track_author, track_uri, track_identifier, COUNT(*) as play_count "
                "FROM song_plays WHERE guild_id = ? "
                "GROUP BY track_identifier "
                "ORDER BY play_count DESC LIMIT ?",
                (guild_id, limit)
            )
            rows = await cur.fetchall()
            return [
                {
                    "title": r[0],
                    "author": r[1],
                    "uri": r[2],
                    "identifier": r[3],
                    "play_count": r[4]
                } for r in rows
            ]

    async def get_guild_music_stats(self, guild_id: int) -> dict:
        async with aiosqlite.connect(self.path) as db:
            # 1. Total tracks played, total duration, unique listeners
            cur = await db.execute(
                "SELECT COUNT(*), COALESCE(SUM(duration), 0), COUNT(DISTINCT user_id) FROM song_plays WHERE guild_id = ?",
                (guild_id,)
            )
            row = await cur.fetchone()
            total_tracks = row[0] if row else 0
            total_duration_ms = row[1] if row else 0
            unique_listeners = row[2] if row else 0

            # 2. Most played track
            cur = await db.execute(
                "SELECT track_title, track_author, track_uri, track_identifier, artwork_url, COUNT(*) as cnt "
                "FROM song_plays WHERE guild_id = ? GROUP BY track_uri ORDER BY cnt DESC LIMIT 1",
                (guild_id,)
            )
            top_track_row = await cur.fetchone()
            top_track = {
                "title": top_track_row[0],
                "author": top_track_row[1],
                "uri": top_track_row[2],
                "identifier": top_track_row[3],
                "artwork_url": top_track_row[4],
                "plays": top_track_row[5]
            } if top_track_row else None

            # 3. Top artist
            cur = await db.execute(
                "SELECT track_author, COUNT(*) as cnt "
                "FROM song_plays WHERE guild_id = ? AND track_author != '' GROUP BY LOWER(track_author) ORDER BY cnt DESC LIMIT 1",
                (guild_id,)
            )
            top_artist_row = await cur.fetchone()
            top_artist = {
                "name": top_artist_row[0],
                "plays": top_artist_row[1]
            } if top_artist_row else None

            # 4. Top listener
            cur = await db.execute(
                "SELECT user_id, COUNT(*) as cnt "
                "FROM song_plays WHERE guild_id = ? AND user_id > 0 GROUP BY user_id ORDER BY cnt DESC LIMIT 1",
                (guild_id,)
            )
            top_listener_row = await cur.fetchone()
            top_listener = {
                "user_id": top_listener_row[0],
                "plays": top_listener_row[1]
            } if top_listener_row else None

            return {
                "total_tracks": total_tracks,
                "total_duration_ms": total_duration_ms,
                "unique_listeners": unique_listeners,
                "top_track": top_track,
                "top_artist": top_artist,
                "top_listener": top_listener
            }

    async def get_guild_top_tracks(self, guild_id: int, limit: int = 10):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT track_title, track_author, track_uri, artwork_url, COUNT(*) as cnt "
                "FROM song_plays WHERE guild_id = ? GROUP BY track_uri ORDER BY cnt DESC LIMIT ?",
                (guild_id, limit)
            )
            rows = await cur.fetchall()
            return [{"title": r[0], "author": r[1], "uri": r[2], "artwork_url": r[3], "plays": r[4]} for r in rows]

    async def get_guild_top_artists(self, guild_id: int, limit: int = 10):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT track_author, COUNT(*) as cnt "
                "FROM song_plays WHERE guild_id = ? AND track_author != '' GROUP BY LOWER(track_author) ORDER BY cnt DESC LIMIT ?",
                (guild_id, limit)
            )
            rows = await cur.fetchall()
            return [{"author": r[0], "plays": r[1]} for r in rows]

    async def get_guild_top_listeners(self, guild_id: int, limit: int = 10):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT user_id, COUNT(*) as cnt "
                "FROM song_plays WHERE guild_id = ? AND user_id > 0 GROUP BY user_id ORDER BY cnt DESC LIMIT ?",
                (guild_id, limit)
            )
            rows = await cur.fetchall()
            return [{"user_id": r[0], "plays": r[1]} for r in rows]

    async def get_guild_top_sources(self, guild_id: int):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT source, COUNT(*) as cnt "
                "FROM song_plays WHERE guild_id = ? GROUP BY source ORDER BY cnt DESC",
                (guild_id,)
            )
            rows = await cur.fetchall()
            return [{"source": r[0], "plays": r[1]} for r in rows]

    async def get_total_song_plays(self) -> int:
        """Get global total songs played across all servers."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM song_plays")
            row = await cur.fetchone()
            return row[0] if row else 0

    async def get_total_playlists_count(self) -> int:
        """Get global total playlists count across all users."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM playlists")
            row = await cur.fetchone()
            return row[0] if row else 0

    # ── Global Bot Settings & Audit Logs ─────────────────────────

    async def get_bot_setting(self, key: str, default: str = None) -> str:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
            row = await cur.fetchone()
            return row[0] if row else default

    async def set_bot_setting(self, key: str, value: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO bot_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                (key, str(value), str(value))
            )
            await db.commit()

    async def get_audit_logs(self, limit: int = 60):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT user_id, username, title, description, color, created_at FROM dashboard_audit_logs ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            return await cur.fetchall()

    async def log_audit_action(self, user_id: int, username: str, title: str, description: str, color: int = 0x8b5cf6):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO dashboard_audit_logs (user_id, username, title, description, color, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, title, description, color, int(time.time()))
            )
            await db.commit()

    # ── Home Notification Banners ───────────────────────────────

    async def add_home_notification(self, title: str, message: str, notif_type: str, color: str, created_by: int, expires_at: int = None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO home_notifications (title, message, type, color, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, message, notif_type, color, created_by, int(time.time()), expires_at)
            )
            await db.commit()

    async def get_active_home_notifications(self):
        now = int(time.time())
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT id, title, message, type, color, created_at, expires_at FROM home_notifications WHERE expires_at IS NULL OR expires_at > ? ORDER BY id DESC",
                (now,)
            )
            return await cur.fetchall()

    async def get_all_home_notifications(self, limit: int = 50):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT id, title, message, type, color, created_by, created_at, expires_at FROM home_notifications ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            return await cur.fetchall()

    async def delete_home_notification(self, notif_id: int):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("DELETE FROM home_notifications WHERE id = ?", (notif_id,))
            await db.commit()
            return cur.rowcount > 0