# 🎵 Discord Music Bot

A production-quality Discord music bot built with Python, discord.py 2.x, and yt-dlp.
Supports slash commands, per-guild independent queues, and reliable audio streaming.

---

## Features

- Slash command interface (`/play`, `/skip`, `/queue`, etc.)
- Per-guild independent music queues
- Streams audio directly — no full file downloads
- Rich Discord embeds for all responses
- Auto-disconnect after configurable idle timeout
- Graceful error handling — no raw tracebacks exposed to users

---

## Prerequisites

| Dependency | Minimum version |
| ---------- | --------------- |
| Python     | 3.11            |
| FFmpeg     | 4.x             |
| pip        | 23.x            |

---

## FFmpeg Installation

### Windows

1. Download a build from https://www.gyan.dev/ffmpeg/builds/ (choose `ffmpeg-release-essentials.zip`)
2. Extract the archive
3. Copy `ffmpeg.exe`, `ffprobe.exe`, and `ffplay.exe` from the `bin/` folder to a permanent location (e.g. `C:\ffmpeg\bin\`)
4. Add that folder to your **system PATH**, or set `FFMPEG_EXECUTABLE=C:\ffmpeg\bin\ffmpeg.exe` in your `.env`

Verify: open a new terminal and run `ffmpeg -version`

### Linux (Debian/Ubuntu)

```bash
sudo apt update && sudo apt install -y ffmpeg
```

### macOS (Homebrew)

```bash
brew install ffmpeg
```

---

## Discord Developer Portal Setup

1. Go to https://discord.com/developers/applications
2. Click **New Application** → give it a name → **Create**
3. Navigate to **Bot** in the left sidebar
4. Click **Add Bot** → confirm
5. Under **Token**, click **Reset Token**, then copy the token — save it securely
6. Under **Privileged Gateway Intents**, enable:
   - **Server Members Intent** — needed for `Requested by` info
   - **Message Content Intent** — not required (we use slash commands) but harmless
7. Navigate to **OAuth2 → URL Generator**
8. Under **Scopes**, tick: `bot`, `applications.commands`
9. Under **Bot Permissions**, tick:
   - `Connect`, `Speak` (voice)
   - `Send Messages`, `Embed Links`, `Read Message History`
10. Copy the generated URL and open it in a browser to invite the bot to your server

---

## Local Setup

```bash
# 1. Clone / download the project
git clone <your-repo-url>
cd discord-music-bot

# 2. Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Open .env and paste your bot token

# 5. Run
python main.py
```

---

## Environment Variables

| Variable            | Required | Default  | Description                                        |
| ------------------- | -------- | -------- | -------------------------------------------------- |
| `DISCORD_TOKEN`     | ✅       | —        | Your bot token from the Developer Portal           |
| `DEV_GUILD_ID`      | ❌       | —        | Guild ID for instant slash command sync during dev |
| `IDLE_TIMEOUT`      | ❌       | `300`    | Seconds of idle before auto-disconnect             |
| `DEFAULT_VOLUME`    | ❌       | `50`     | Playback volume 0–100                              |
| `FFMPEG_EXECUTABLE` | ❌       | `ffmpeg` | Full path to FFmpeg binary if not on PATH          |

---

## Commands

| Command         | Description                                   |
| --------------- | --------------------------------------------- |
| `/play <query>` | Play a song by name or URL                    |
| `/pause`        | Pause current playback                        |
| `/resume`       | Resume paused playback                        |
| `/skip`         | Skip current track                            |
| `/stop`         | Stop playback and clear queue                 |
| `/queue`        | Display the current queue                     |
| `/nowplaying`   | Show currently playing song details           |
| `/volume <n>`   | Set playback volume (0–100)                   |
| `/shuffle`      | Shuffle the upcoming queue                    |
| `/clear`        | Clear upcoming queue without stopping current |

---

## Development Tips

- Set `DEV_GUILD_ID` to your test server's ID for **instant** slash command updates
  (global sync can take up to an hour to propagate)
- Run `python main.py` — logs include timestamps, level, and module name
- Test each phase before moving to the next

---

## Troubleshooting

| Problem                      | Solution                                                           |
| ---------------------------- | ------------------------------------------------------------------ |
| `Invalid DISCORD_TOKEN`      | Regenerate the token in the Developer Portal                       |
| `ffmpeg not found`           | Install FFmpeg and ensure it's on PATH, or set `FFMPEG_EXECUTABLE` |
| Slash commands don't appear  | Wait up to 1 hour for global sync, or use `DEV_GUILD_ID`           |
| Bot joins voice but no audio | Check `PyNaCl` is installed: `pip install PyNaCl`                  |
| `opus not loaded` error      | Install libopus: `sudo apt install libopus0` (Linux)               |

---

## Project Structure

```
discord-music-bot/
├── main.py           # Entry point — bot client, logging, startup
├── config.py         # Environment variable loading and validation
├── requirements.txt
├── .env.example
├── README.md
├── cogs/
│   └── music.py      # Slash command definitions and interaction handling
├── services/
│   ├── music_player.py   # Per-guild queue and playback management
│   └── music_source.py   # yt-dlp search and stream extraction
├── models/
│   └── track.py      # Track dataclass
└── utils/
    └── embeds.py     # Consistent Discord embed builders
```
