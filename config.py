"""
config.py
---------
Loads environment variables and exposes validated configuration constants.
Raises clearly at startup if required variables are missing, rather than
failing silently mid-operation.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Copy .env.example to .env and fill in your values."
        )
    return value


def _optional_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise EnvironmentError(
            f"Environment variable '{key}' must be an integer, got: {raw!r}"
        )


def _optional_str(key: str, default: str | None = None) -> str | None:
    return os.getenv(key) or default


# ── Required ───────────────────────────────────────────────────────────────────

DISCORD_TOKEN: str = _require("DISCORD_TOKEN")

# ── Optional ───────────────────────────────────────────────────────────────────

DEV_GUILD_ID: int | None = (
    int(os.getenv("DEV_GUILD_ID")) if os.getenv("DEV_GUILD_ID") else None
)

IDLE_TIMEOUT: int = _optional_int("IDLE_TIMEOUT", default=300)

DEFAULT_VOLUME: int = _optional_int("DEFAULT_VOLUME", default=50)
if not (0 <= DEFAULT_VOLUME <= 100):
    raise EnvironmentError("DEFAULT_VOLUME must be between 0 and 100.")

FFMPEG_EXECUTABLE: str = _optional_str("FFMPEG_EXECUTABLE", default="ffmpeg")

# ── GIF / media ────────────────────────────────────────────────────────────────

# Local directory of .gif files to display on Now Playing embeds.
# Put any .gif files you like in this folder.
# Leave unset (or folder empty) to skip local GIFs.
MUSIC_GIF_DIR: Path = Path(
    _optional_str("MUSIC_GIF_DIR", default="assets/gifs") or "assets/gifs"
)

# Remote GIF URL fallback — used when MUSIC_GIF_DIR has no files.
# Leave unset to show no GIF at all.
MUSIC_GIF_URL: str | None = _optional_str("MUSIC_GIF_URL", default=None)
