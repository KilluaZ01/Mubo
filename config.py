"""
config.py
---------
Loads environment variables and exposes validated configuration constants.
Raises clearly at startup if required variables are missing, rather than
failing silently mid-operation.
"""

import os
from dotenv import load_dotenv

# Load .env file if it exists (no-op in production where env is set directly)
load_dotenv()


def _require(key: str) -> str:
    """Retrieve a required environment variable or raise a helpful error."""
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Copy .env.example to .env and fill in your values."
        )
    return value


def _optional_int(key: str, default: int) -> int:
    """Retrieve an optional integer environment variable with a fallback."""
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


# ── Required ──────────────────────────────────────────────────────────────────

DISCORD_TOKEN: str = _require("DISCORD_TOKEN")

# ── Optional ──────────────────────────────────────────────────────────────────

# If set, slash commands sync instantly to this guild (dev workflow).
# Leave unset for global sync (production).
DEV_GUILD_ID: int | None = (
    int(os.getenv("DEV_GUILD_ID")) if os.getenv("DEV_GUILD_ID") else None
)

# Seconds of silence before the bot auto-disconnects from a voice channel.
IDLE_TIMEOUT: int = _optional_int("IDLE_TIMEOUT", default=300)

# Volume applied when a new MusicPlayer is created (0–100).
DEFAULT_VOLUME: int = _optional_int("DEFAULT_VOLUME", default=50)
if not (0 <= DEFAULT_VOLUME <= 100):
    raise EnvironmentError("DEFAULT_VOLUME must be between 0 and 100.")

# ── FFmpeg ─────────────────────────────────────────────────────────────────────

# Override if FFmpeg is not on PATH (e.g. Windows users who extracted manually).
FFMPEG_EXECUTABLE: str = _optional_str("FFMPEG_EXECUTABLE", default="ffmpeg")
