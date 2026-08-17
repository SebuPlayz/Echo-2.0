<div align="center">

# 🌹 ECHO 2.0 — HIGH-PERFORMANCE MUSIC BOT & WEB DASHBOARD

> **Next-Generation Discord Audio Engine & Real-Time Studio Console Dashboard**

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Discord.py](https://img.shields.io/badge/Discord.py-v2.5.0-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discordpy.readthedocs.io)
[![Lavalink](https://img.shields.io/badge/Lavalink-v5.0-red?style=for-the-badge&logo=youtube&logoColor=white)](https://lavalink.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-v0.110.0-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

</div>

---

> [!NOTE]
> **Echo 2.0** is an enterprise-grade Discord Music Bot engineered for ultra-low latency, 320kbps high-fidelity spatial audio streaming, interactive web dashboard management, and robust owner security.

---

## 📸 Feature Highlights

```
┌─────────────────────────────────────────────────────────────────────────┐
│  🌹 ECHO 2.0 STUDIO AUDIO ARCHITECTURE                                  │
├─────────────────────────────────────────────────────────────────────────┤
│  • 320kbps Lossless Audio     : YouTube, Spotify, Apple Music & SoundCloud│
│  • 360° 8D Spatial Surround   : Binaural Left-Right Stereo Panning      │
│  • Paginated Interactive Queue: 10 tracks/page with Next/Prev/Shuffle   │
│  • Smart 1-Click Playlists    : Quick save with multi-playlist dropdown  │
│  • Real-Time Web Console      : HTML5 Stream Player + Audio Scrubber    │
│  • Stealth Web CLI Terminal   : Live command runner directly in browser │
│  • 2-Step Admin Passcode      : HMAC Cookie Hashing + IP Lockout (5 Max)│
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech Stack & Architecture

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Language Core** | Python `3.10+` | Async engine & command orchestration |
| **Discord Gateway** | `discord.py` `v2.5+` | Slash commands, events & UI components |
| **Audio Cluster** | `lavalink.py` + Lavalink v5 | Multi-node failover audio node streaming |
| **Web Server** | FastAPI + Uvicorn | Web dashboard & REST API backend |
| **Security Layer** | HMAC SHA-256 | Admin passcode signing & cookie integrity |
| **Database** | SQLite3 via `aiosqlite` | Asynchronous persistent storage |
| **Banner Engine** | Pillow (PIL) | Dynamic Now Playing canvas image cards |

---

## 💎 Feature Deep-Dive

### 🎧 1. Spatial Audio & DSP Equalizer
- **360° 8D Spatial Surround (`/filter 8d`)**: Rotates audio channels using Lavalink `Rotation` combined with `ChannelMix` stereo matrix so left/right channels do not downmix to mono in Discord.
- **7 Audio DSP Filters**:
  - `8d` — 360° Spatial Surround Panning
  - `bassboost` — Heavy Bass Equalizer Boost
  - `nightcore` — Speed & Pitch Acceleration
  - `vaporwave` — Slow Relaxed Lofi Pitch
  - `karaoke` — Vocal Suppressor Filter
  - `treble` — High-Frequency Audio Clarity Boost
  - `clear` — Reset all filters to neutral

### 📜 2. Interactive Queue & Smart Playlist System
- **Paginated Queue (`/queue` / `!q`)**: Divides large queues into 10 tracks per page with interactive buttons (`◀️ Prev`, `▶️ Next`, `🔀 Shuffle`, `🧹 Clear`, `🔄 Refresh`).
- **Smart 1-Click Track Saving (`➕ Playlist`)**:
  - `0 Playlists`: Prompts user to create a new playlist.
  - `1 Playlist`: Auto-saves the track instantly.
  - `2+ Playlists`: Displays an ephemeral dropdown menu to select target playlist.
- **Live Countdown Timestamp**: Displays Discord relative timestamp `<t:TIMESTAMP:R>` (`⏳ Ends: in 3 minutes`) updating live on client screens with 0 server overhead.

### 🔒 3. Enterprise Admin Security & Telemetry
- **2-Step Owner PIN Authentication**: `/admin` panel protected by Discord Owner check (`_is_owner`) + 6-digit Secret PIN (`ADMIN_PASSCODE`).
- **Cryptographic Cookie Hashing**: Passcode saved as SHA-256 HMAC hash using `DASHBOARD_SESSION_SECRET`.
- **IP Anti-Bruteforce Rate Limiter**: 5 consecutive invalid PIN attempts temporarily lock out client IP for 15 minutes (`HTTP 429`).
- **Intrusion Webhook Alerts**: Security alert embed dispatched to Discord Webhook on invalid access attempts.

### 🌐 4. Interactive Web Dashboard & Web CLI
- **Studio Console Player**: HTML5 & YouTube stream audio player on `index.html` with Play/Pause, Scrubber seeking, Volume slider (`0-100%`), and Mute toggle.
- **Stealth Command Line Terminal**: Built-in home page CLI terminal allowing users to run `help`, `music`, `filters`, `status`, `247`, and `ping` commands directly in the browser.

---

## 🚀 Quick Setup & Installation

### 1. Prerequisites
- **Python 3.10+** installed on your system.
- **Discord Bot Token** from [Discord Developer Portal](https://discord.com/developers/applications).
  - Enable **Message Content**, **Server Members**, and **Presence** intents.
- **Spotify API Credentials** from [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).

### 2. Clone & Install Dependencies
```bash
git clone https://github.com/SebuPlayz/Echo-2.0.git
cd echo-2.0
pip install -r requirements.txt
```

### 3. Environment Configuration (`.env`)
Create a `.env` file in the project root:
```env
# ── Discord Credentials ──
BOT_TOKEN=your_discord_bot_token_here
DISCORD_CLIENT_ID=your_discord_client_id_here
DISCORD_CLIENT_SECRET=your_discord_client_secret_here

# ── Web Dashboard Settings ──
DASHBOARD_ENABLED=true
DASHBOARD_PORT=3000
DASHBOARD_URL=http://localhost:3000
DASHBOARD_REDIRECT_URI=http://localhost:3000/auth/callback
DASHBOARD_SESSION_SECRET=a3f8e9c2d1b47a6f8e0c9d2b1a4f7e6c3d8b9a2f1e4c7d0b3a6f9e2c5d8b1a4f

# ── Security Passcode ──
ADMIN_PASSCODE=123456

# ── Spotify API Credentials ──
SPOTIFY_CLIENT_ID=your_spotify_client_id_here
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret_here
```

### 4. Launch Bot & Web Dashboard
```bash
python main.py
```
- **Web App**: `http://localhost:3000`
- **Owner Admin Panel**: `http://localhost:3000/admin` (Protected by `ADMIN_PASSCODE`)

---

## 🤖 Command Directory

<details>
<summary><b>🎵 Playback & Queue Commands (Click to expand)</b></summary>

| Command | Aliases | Description |
| :--- | :--- | :--- |
| `/play <query>` | `p`, `!play` | Search & play song/playlist from YouTube, Spotify, Apple Music |
| `/nowplaying` | `np`, `!np` | Display current track banner with live countdown timer |
| `/pause` | `!pause` | Pause audio playback |
| `/resume` | `!resume` | Resume paused audio playback |
| `/skip` | `s`, `!skip` | Skip current track |
| `/previous` | `prev` | Replay previous track |
| `/stop` | `!stop` | Stop playback and clear current queue |
| `/queue` | `q`, `!q` | Display interactive 10-track paginated queue |
| `/volume <0-100>`| `vol` | Adjust playback volume |
| `/seek <time>` | — | Seek to specific song timestamp |
| `/loop` | `repeat` | Toggle single track or queue loop mode |
| `/autoplay` | `ap` | Toggle infinite recommendation mode |
| `/filter <type>` | — | Apply audio DSP filter (`8d`, `bassboost`, `nightcore`, `clear`) |

</details>

<details>
<summary><b>🎙️ Voice 24/7 & DJ Permissions (Click to expand)</b></summary>

| Command | Aliases | Description |
| :--- | :--- | :--- |
| `/247` | `!247` | Toggle 24/7 voice channel stay mode |
| `!setdj <@role>` | `!djrole` | Restrict music playback controls to specified DJ role |
| `!cleardj` | `!removedj` | Clear DJ role requirement |

</details>

---

<div align="center">
  <b>Designed with 🌹 for Discord Communities</b>
</div>
