"""
cogs/music.py
-------------
All Discord slash commands for the music bot.

Responsibilities:
  - Define and register slash commands
  - Validate user context (in a voice channel? correct permissions?)
  - Delegate work to MusicPlayer and MusicSource
  - Send Discord embeds

Does NOT contain queue logic, streaming, or yt-dlp calls.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import yt_dlp

from models.track import Track
from services.music_player import MusicPlayer
from services.music_source import MusicSource
from utils import embeds

log = logging.getLogger(__name__)


# ── Voice connection helpers ───────────────────────────────────────────────────


async def _get_user_voice_channel(
    interaction: discord.Interaction,
) -> discord.VoiceChannel | None:
    """Return the user's current voice channel, or send an error and return None."""
    member = interaction.user
    if not isinstance(member, discord.Member) or member.voice is None:
        await interaction.response.send_message(
            embed=embeds.error(
                "Not in a Voice Channel",
                "You need to join a voice channel first.",
            ),
            ephemeral=True,
        )
        return None

    channel = member.voice.channel
    if not isinstance(channel, discord.VoiceChannel):
        await interaction.response.send_message(
            embed=embeds.error(
                "Unsupported Channel Type",
                "Please join a standard voice channel (not a Stage channel).",
            ),
            ephemeral=True,
        )
        return None

    return channel


async def _check_bot_permissions(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
) -> bool:
    """Verify Connect + Speak permissions. Sends error and returns False if missing."""
    me = interaction.guild.me
    perms = channel.permissions_for(me)

    missing: list[str] = []
    if not perms.connect:
        missing.append("`Connect`")
    if not perms.speak:
        missing.append("`Speak`")

    if missing:
        await interaction.response.send_message(
            embed=embeds.error(
                "Missing Permissions",
                f"I need {' and '.join(missing)} permission(s) in **{channel.name}**.",
            ),
            ephemeral=True,
        )
        return False

    return True


async def _join_channel(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
) -> discord.VoiceClient | None:
    """Connect to or move to a voice channel. Returns VoiceClient or None on failure."""
    guild_vc: discord.VoiceClient | None = interaction.guild.voice_client  # type: ignore[assignment]

    try:
        if guild_vc is None:
            voice_client = await channel.connect(timeout=10.0, reconnect=True)
            log.info("Connected to #%s in '%s'", channel.name, interaction.guild.name)
        elif guild_vc.channel.id == channel.id:
            return guild_vc
        else:
            await guild_vc.move_to(channel)
            log.info("Moved to #%s in '%s'", channel.name, interaction.guild.name)
            voice_client = guild_vc

    except discord.ClientException as exc:
        log.error("Voice connection error: %s", exc)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=embeds.error(
                    "Connection Failed", "Could not connect to the voice channel."
                ),
                ephemeral=True,
            )
        return None

    except TimeoutError:
        log.error("Voice connection timed out in '%s'", interaction.guild.name)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=embeds.error(
                    "Connection Timed Out",
                    "Took too long to join. Check my permissions.",
                ),
                ephemeral=True,
            )
        return None

    return voice_client


# ── Cog ───────────────────────────────────────────────────────────────────────


class Music(commands.Cog):
    """Music playback slash commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # One MusicPlayer per guild, keyed by guild ID
        self._players: dict[int, MusicPlayer] = {}

    # ── Player management ─────────────────────────────────────────────────────

    def get_player(self, guild_id: int) -> MusicPlayer | None:
        """Return the active MusicPlayer for a guild, or None."""
        return self._players.get(guild_id)

    async def get_or_create_player(
        self,
        interaction: discord.Interaction,
        voice_client: discord.VoiceClient,
    ) -> MusicPlayer:
        """
        Return an existing MusicPlayer for the guild, or create a new one.
        Sets up the on_track_start callback to send Now Playing embeds.
        """
        guild_id = interaction.guild_id
        player = self._players.get(guild_id)

        if player is None or not player.voice_client.is_connected():
            # Capture text_channel for the callback closure
            text_channel = interaction.channel

            def on_track_start(track: Track) -> None:
                """Called by MusicPlayer when a new track starts."""
                asyncio.get_event_loop().create_task(
                    text_channel.send(embed=embeds.now_playing(track))
                )

            player = MusicPlayer(
                voice_client=voice_client,
                text_channel=text_channel,
                on_track_start=on_track_start,
            )
            self._players[guild_id] = player
            log.info("Created MusicPlayer for guild %d", guild_id)

        return player

    async def destroy_player(self, guild_id: int) -> None:
        """Destroy and remove the MusicPlayer for a guild."""
        player = self._players.pop(guild_id, None)
        if player:
            await player.destroy()

    # ── Event listeners ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """
        Clean up the player when the bot is disconnected from voice externally
        (e.g. kicked from the channel by an admin).
        """
        if member.id != self.bot.user.id:
            return
        # Bot left a channel
        if before.channel is not None and after.channel is None:
            guild_id = member.guild.id
            player = self._players.pop(guild_id, None)
            if player:
                player._playback_task.cancel()
                log.info(
                    "Player cleaned up after external disconnect in guild %d", guild_id
                )

    # ── /play ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="play", description="Play a song by name or URL.")
    @app_commands.describe(query="Song name or URL to play")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        """Search for a song and add it to the queue."""

        # 1. Validate voice channel
        channel = await _get_user_voice_channel(interaction)
        if channel is None:
            return

        if not await _check_bot_permissions(interaction, channel):
            return

        # 2. Defer immediately — search can take a few seconds
        await interaction.response.defer()

        # 3. Search / extract track metadata
        try:
            track = await MusicSource.from_query(
                query,
                requester_id=interaction.user.id,
                requester_name=interaction.user.display_name,
            )
        except yt_dlp.utils.DownloadError as exc:
            log.warning("yt-dlp DownloadError for %r: %s", query, exc)
            await interaction.followup.send(
                embed=embeds.error(
                    "Could Not Find Song",
                    "That video is unavailable, private, or geo-restricted.\n"
                    "Try a different search or URL.",
                )
            )
            return
        except ValueError as exc:
            await interaction.followup.send(embed=embeds.error("No Results", str(exc)))
            return
        except Exception as exc:
            log.error("Unexpected search error for %r: %s", query, exc, exc_info=True)
            await interaction.followup.send(
                embed=embeds.error(
                    "Search Failed",
                    "Something went wrong while searching. Please try again.",
                )
            )
            return

        # 4. Connect to voice (or reuse existing connection)
        voice_client = await _join_channel(interaction, channel)
        if voice_client is None:
            return

        # 5. Get or create the guild's MusicPlayer
        player = await self.get_or_create_player(interaction, voice_client)

        # 6. Enqueue the track
        position = await player.enqueue(track)

        # 7. Respond — if something is already playing, show "Added to Queue"
        #    If the queue was empty, the playback loop will pick it up and
        #    fire the "Now Playing" embed automatically via on_track_start.
        if player.is_playing or player.is_paused:
            await interaction.followup.send(
                embed=embeds.added_to_queue(track, position)
            )
        else:
            # Playback loop will send Now Playing embed when it starts
            await interaction.followup.send(
                embed=embeds.info(
                    "Loading…",
                    f"Preparing to play **{track.short_title}**.",
                )
            )

    # ── /join ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="join", description="Join your current voice channel.")
    async def join(self, interaction: discord.Interaction) -> None:
        channel = await _get_user_voice_channel(interaction)
        if channel is None:
            return
        if not await _check_bot_permissions(interaction, channel):
            return

        voice_client = await _join_channel(interaction, channel)
        if voice_client is None:
            return

        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=embeds.joined_channel(channel.name)
            )
        else:
            await interaction.followup.send(
                embed=embeds.info(
                    "Already Connected", f"Already in **{channel.name}**."
                )
            )

    # ── /leave ────────────────────────────────────────────────────────────────

    @app_commands.command(name="leave", description="Stop playback and disconnect.")
    async def leave(self, interaction: discord.Interaction) -> None:
        guild_vc: discord.VoiceClient | None = interaction.guild.voice_client  # type: ignore[assignment]

        if guild_vc is None or not guild_vc.is_connected():
            await interaction.response.send_message(
                embed=embeds.error("Not Connected", "I'm not in a voice channel."),
                ephemeral=True,
            )
            return

        channel_name = guild_vc.channel.name
        await self.destroy_player(interaction.guild_id)

        # destroy() disconnects, but if no player existed, disconnect manually
        if guild_vc.is_connected():
            await guild_vc.disconnect(force=False)

        await interaction.response.send_message(embed=embeds.left_channel(channel_name))

    # ── /pause ────────────────────────────────────────────────────────────────

    @app_commands.command(name="pause", description="Pause the current track.")
    async def pause(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild_id)

        if player is None or not (player.is_playing or player.is_paused):
            await interaction.response.send_message(
                embed=embeds.error("Nothing Playing", "There's nothing to pause."),
                ephemeral=True,
            )
            return

        if player.pause():
            await interaction.response.send_message(
                embed=embeds.success(
                    "Paused", f"Paused **{player.current.short_title}**."
                )
            )
        else:
            await interaction.response.send_message(
                embed=embeds.warning("Already Paused", "Playback is already paused."),
                ephemeral=True,
            )

    # ── /resume ───────────────────────────────────────────────────────────────

    @app_commands.command(name="resume", description="Resume paused playback.")
    async def resume(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild_id)

        if player is None:
            await interaction.response.send_message(
                embed=embeds.error("Nothing Playing", "There's nothing to resume."),
                ephemeral=True,
            )
            return

        if player.resume():
            await interaction.response.send_message(
                embed=embeds.success(
                    "Resumed", f"Resumed **{player.current.short_title}**."
                )
            )
        else:
            await interaction.response.send_message(
                embed=embeds.warning("Not Paused", "Playback isn't paused right now."),
                ephemeral=True,
            )

    # ── /skip ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="skip", description="Skip the current track.")
    async def skip(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild_id)

        if player is None or player.current is None:
            await interaction.response.send_message(
                embed=embeds.error("Nothing Playing", "There's nothing to skip."),
                ephemeral=True,
            )
            return

        skipped = await player.skip()
        await interaction.response.send_message(
            embed=embeds.success(
                "Skipped",
                f"Skipped **{skipped.short_title}**." if skipped else "Skipped.",
            )
        )

    # ── /stop ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="stop", description="Stop playback, clear queue, and disconnect."
    )
    async def stop(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild_id)

        if player is None:
            await interaction.response.send_message(
                embed=embeds.error("Nothing Playing", "I'm not playing anything."),
                ephemeral=True,
            )
            return

        await self.destroy_player(interaction.guild_id)
        await interaction.response.send_message(
            embed=embeds.success("Stopped", "Playback stopped and queue cleared.")
        )

    # ── /queue ────────────────────────────────────────────────────────────────

    @app_commands.command(name="queue", description="Show the current music queue.")
    @app_commands.describe(page="Page number for long queues")
    async def queue(self, interaction: discord.Interaction, page: int = 1) -> None:
        player = self.get_player(interaction.guild_id)

        current = player.current if player else None
        upcoming = player.queue_snapshot() if player else []

        await interaction.response.send_message(
            embed=embeds.queue_list(current, upcoming, page=page)
        )

    # ── /nowplaying ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="nowplaying", description="Show the currently playing song."
    )
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild_id)

        if player is None or player.current is None:
            await interaction.response.send_message(
                embed=embeds.error("Nothing Playing", "No song is currently playing."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=embeds.now_playing_status(player.current)
        )

    # ── /volume ───────────────────────────────────────────────────────────────

    @app_commands.command(name="volume", description="Set playback volume (0–100).")
    @app_commands.describe(level="Volume level between 0 and 100")
    async def volume(self, interaction: discord.Interaction, level: int) -> None:
        if not (0 <= level <= 100):
            await interaction.response.send_message(
                embed=embeds.error(
                    "Invalid Volume", "Volume must be between 0 and 100."
                ),
                ephemeral=True,
            )
            return

        player = self.get_player(interaction.guild_id)
        if player is None:
            await interaction.response.send_message(
                embed=embeds.error(
                    "Nothing Playing", "No active player in this server."
                ),
                ephemeral=True,
            )
            return

        player.volume = level
        await interaction.response.send_message(
            embed=embeds.success("Volume Updated", f"Volume set to **{level}%**.")
        )

    # ── /shuffle ──────────────────────────────────────────────────────────────

    @app_commands.command(name="shuffle", description="Shuffle the upcoming queue.")
    async def shuffle(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild_id)

        if player is None or player.queue_snapshot() == []:
            await interaction.response.send_message(
                embed=embeds.error(
                    "Empty Queue", "There are no upcoming tracks to shuffle."
                ),
                ephemeral=True,
            )
            return

        if player.shuffle():
            await interaction.response.send_message(
                embed=embeds.success("Shuffled", "The queue has been shuffled. 🔀")
            )
        else:
            await interaction.response.send_message(
                embed=embeds.warning(
                    "Not Enough Tracks", "Need at least 2 tracks to shuffle."
                ),
                ephemeral=True,
            )

    # ── /clear ────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="clear", description="Clear upcoming tracks without stopping current song."
    )
    async def clear(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild_id)

        if player is None:
            await interaction.response.send_message(
                embed=embeds.error(
                    "Nothing Playing", "No active player in this server."
                ),
                ephemeral=True,
            )
            return

        count = player.clear_queue()
        await interaction.response.send_message(
            embed=embeds.success(
                "Queue Cleared",
                f"Removed **{count}** track(s) from the queue.",
            )
        )

    # ── /ping (dev utility) ───────────────────────────────────────────────────

    @app_commands.command(name="ping", description="Check bot latency.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            f"🏓 Pong! Latency: **{latency_ms}ms**", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
    log.info("Music cog loaded.")
