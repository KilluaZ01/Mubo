"""
cogs/music.py
-------------
All Discord slash commands for the music bot.
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


# ── Now Playing interactive view ───────────────────────────────────────────────


class NowPlayingView(discord.ui.View):
    """Interactive controls attached to the automatic Now Playing message."""

    def __init__(
        self,
        music_cog: "Music",
        guild_id: int,
        gif_name: str | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.gif_name = gif_name

    def _player(self) -> MusicPlayer | None:
        return self.music_cog.get_player(self.guild_id)

    async def _refresh_panel(self, interaction: discord.Interaction) -> None:
        """Re-render the Now Playing embed in-place after a state change."""
        player = self._player()
        if player is None or player.current is None:
            # Playback ended — update embed to reflect stopped state
            await interaction.response.edit_message(
                embed=embeds.success("Playback Ended", "The queue is now empty."),
                view=None,
                attachments=[],
            )
            self.stop()
            return
        await interaction.response.edit_message(
            embed=embeds.now_playing(
                player.current,
                paused=player.is_paused,
                gif_name=self.gif_name,
            ),
            view=self,
        )

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary)
    async def pause_resume(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        player = self._player()
        if player is None or player.current is None:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
            return
        if player.is_paused:
            player.resume()
        elif player.is_playing:
            player.pause()
        await self._refresh_panel(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def skip(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        player = self._player()
        if player is None or player.current is None:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
            return
        skipped = await player.skip()
        await interaction.response.send_message(
            f"Skipped **{skipped.short_title}**." if skipped else "Skipped.",
            ephemeral=True,
        )

    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.secondary)
    async def show_queue(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        player = self._player()
        current = player.current if player else None
        upcoming = player.queue_snapshot() if player else []
        await interaction.response.send_message(
            embed=embeds.queue_list(current, upcoming), ephemeral=True
        )

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_playback(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self._player() is None:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
            return
        await self.music_cog.destroy_player(self.guild_id)
        self.stop()
        await interaction.response.edit_message(
            embed=embeds.success("Stopped", "Playback stopped and queue cleared."),
            view=None,
            attachments=[],
        )


# ── Voice connection helpers ───────────────────────────────────────────────────


async def _get_user_voice_channel(
    interaction: discord.Interaction,
) -> discord.VoiceChannel | None:
    member = interaction.user
    if not isinstance(member, discord.Member) or member.voice is None:
        await interaction.response.send_message(
            embed=embeds.error(
                "Not in a Voice Channel", "You need to join a voice channel first."
            ),
            ephemeral=True,
        )
        return None
    channel = member.voice.channel
    if not isinstance(channel, discord.VoiceChannel):
        await interaction.response.send_message(
            embed=embeds.error(
                "Unsupported Channel Type", "Please join a standard voice channel."
            ),
            ephemeral=True,
        )
        return None
    return channel


async def _check_bot_permissions(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
) -> bool:
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
                f"I need {' and '.join(missing)} in **{channel.name}**.",
            ),
            ephemeral=True,
        )
        return False
    return True


async def _join_channel(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
) -> discord.VoiceClient | None:
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
                    "Connection Timed Out", "Check my permissions and try again."
                ),
                ephemeral=True,
            )
        return None
    return voice_client


# ── Cog ───────────────────────────────────────────────────────────────────────


class Music(commands.Cog):
    """Music playback commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._players: dict[int, MusicPlayer] = {}

    # ── Player management ─────────────────────────────────────────────────────

    def get_player(self, guild_id: int) -> MusicPlayer | None:
        return self._players.get(guild_id)

    async def get_or_create_player(
        self,
        interaction: discord.Interaction,
        voice_client: discord.VoiceClient,
    ) -> MusicPlayer:
        guild_id = interaction.guild_id
        player = self._players.get(guild_id)

        if player is None or not player.voice_client.is_connected():
            text_channel = interaction.channel

            def on_track_start(track: Track) -> None:
                current_player = self._players.get(guild_id)
                if current_player is None:
                    return
                gif_path = embeds.random_music_gif()
                gif_name = gif_path.name if gif_path else None
                asyncio.get_event_loop().create_task(
                    self._send_now_playing(
                        text_channel, guild_id, track, gif_path, gif_name
                    )
                )

            player = MusicPlayer(
                voice_client=voice_client,
                text_channel=text_channel,
                on_track_start=on_track_start,
            )
            self._players[guild_id] = player
            log.info("Created MusicPlayer for guild %d", guild_id)

        return player

    async def _send_now_playing(
        self,
        channel: discord.abc.Messageable,
        guild_id: int,
        track: Track,
        gif_path,
        gif_name: str | None,
    ) -> None:
        file = discord.File(str(gif_path), filename=gif_name) if gif_path else None
        await channel.send(
            embed=embeds.now_playing(track, gif_name=gif_name),
            view=NowPlayingView(self, guild_id, gif_name),
            file=file,
        )

    async def destroy_player(self, guild_id: int) -> None:
        player = self._players.pop(guild_id, None)
        if player:
            await player.destroy()

    # ── Events ────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.id != self.bot.user.id:
            return
        if before.channel is not None and after.channel is None:
            guild_id = member.guild.id
            player = self._players.pop(guild_id, None)
            if player:
                player._playback_task.cancel()
            log.info("Cleaned up after external disconnect in guild %d", guild_id)

    # ── Autocomplete ──────────────────────────────────────────────────────────

    async def _play_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete callback — must be an instance method on the cog."""
        if not current.strip():
            return []
        suggestions = await MusicSource.suggestions(current)
        return [
            app_commands.Choice(name=name[:100], value=value[:100])
            for name, value in suggestions
        ]

    # ── /play ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="play", description="Play a song by name or URL.")
    @app_commands.describe(query="Song name or URL to play")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        channel = await _get_user_voice_channel(interaction)
        if channel is None:
            return
        if not await _check_bot_permissions(interaction, channel):
            return

        await interaction.response.defer()

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
                    "That video is unavailable, private, or geo-restricted.",
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
                    "Search Failed", "Something went wrong. Please try again."
                )
            )
            return

        voice_client = await _join_channel(interaction, channel)
        if voice_client is None:
            return

        player = await self.get_or_create_player(interaction, voice_client)
        position = await player.enqueue(track)

        if player.is_playing or player.is_paused:
            await interaction.followup.send(
                embed=embeds.added_to_queue(track, position)
            )
        else:
            await interaction.followup.send(
                embed=embeds.info(
                    "Loading…", f"Preparing to play **{track.short_title}**."
                )
            )

    # Wire up autocomplete after the method exists
    play.autocomplete("query")(_play_autocomplete)

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
                embed=embeds.warning("Not Paused", "Playback isn't paused."),
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
                embed=embeds.error("Nothing Playing", "No active player."),
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
        if player is None or not player.queue_snapshot():
            await interaction.response.send_message(
                embed=embeds.error("Empty Queue", "No upcoming tracks to shuffle."),
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
                embed=embeds.error("Nothing Playing", "No active player."),
                ephemeral=True,
            )
            return
        count = player.clear_queue()
        await interaction.response.send_message(
            embed=embeds.success(
                "Queue Cleared", f"Removed **{count}** track(s) from the queue."
            )
        )

    # ── /ping ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="ping", description="Check bot latency.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            f"🏓 Pong! Latency: **{latency_ms}ms**", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
    log.info("Music cog loaded.")
