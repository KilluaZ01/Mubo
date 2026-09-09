"""
main.py
-------
Entry point for the Discord Music Bot.

Responsibilities:
  - Configure logging
  - Instantiate the bot client
  - Load cog extensions
  - Sync slash commands
  - Connect to Discord

Nothing domain-specific lives here. Business logic belongs in cogs/ and services/.
"""

import asyncio
import logging
import sys
from pathlib import Path

import discord
from discord.ext import commands

import config

# ── Logging ───────────────────────────────────────────────────────────────────


def setup_logging() -> None:
    """Configure root logger with a timestamp-inclusive format."""
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt=datefmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Quiet down noisy third-party loggers
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)


log = logging.getLogger("bot")

# ── Bot client ────────────────────────────────────────────────────────────────


class MusicBot(commands.Bot):
    """
    Subclass of commands.Bot so we can override setup_hook for async
    initialisation (loading cogs, syncing commands) without blocking __init__.
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        # voice_states intent is required to detect which voice channel a user is in.
        intents.voice_states = True
        # message_content is NOT needed because we use slash commands only.

        super().__init__(
            command_prefix=commands.when_mentioned,  # prefix unused; slash only
            intents=intents,
            help_command=None,  # we'll provide our own UX
        )

    async def setup_hook(self) -> None:
        """
        Called by discord.py once after login, before the gateway connection.
        This is the correct place for async setup work.
        """
        await self._load_cogs()
        await self._sync_commands()

    async def _load_cogs(self) -> None:
        """Discover and load all cog modules inside the cogs/ directory."""
        cogs_dir = Path(__file__).parent / "cogs"
        for path in sorted(cogs_dir.glob("*.py")):
            if path.stem.startswith("_"):
                continue  # skip __init__.py and private files
            extension = f"cogs.{path.stem}"
            try:
                await self.load_extension(extension)
                log.info("Loaded extension: %s", extension)
            except Exception as exc:
                log.error(
                    "Failed to load extension %s: %s", extension, exc, exc_info=True
                )

    async def _sync_commands(self) -> None:
        """
        Sync slash commands.

        During development, sync to a specific guild for instant updates.
        For production, sync globally (can take up to an hour to propagate).
        """
        if config.DEV_GUILD_ID:
            guild = discord.Object(id=config.DEV_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info(
                "Synced %d command(s) to dev guild %d",
                len(synced),
                config.DEV_GUILD_ID,
            )
        else:
            synced = await self.tree.sync()
            log.info("Synced %d command(s) globally", len(synced))

    async def on_ready(self) -> None:
        log.info(
            "Bot connected as %s (ID: %d) | discord.py %s",
            self.user,
            self.user.id,
            discord.__version__,
        )
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="/play",
            )
        )

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        # Prefix commands are not used, but log unexpected errors just in case.
        log.warning("Unexpected prefix command error: %s", error)


# ── Entry point ───────────────────────────────────────────────────────────────


async def main() -> None:
    setup_logging()
    log.info("Starting Discord Music Bot…")

    bot = MusicBot()

    try:
        await bot.start(config.DISCORD_TOKEN)
    except discord.LoginFailure:
        log.critical(
            "Invalid DISCORD_TOKEN. Double-check your .env file and regenerate "
            "the token at https://discord.com/developers/applications if needed."
        )
        sys.exit(1)
    except KeyboardInterrupt:
        log.info("Shutdown requested.")
    finally:
        if not bot.is_closed():
            await bot.close()
        log.info("Bot shut down cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
