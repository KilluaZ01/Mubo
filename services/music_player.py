"""
services/music_player.py
------------------------
Per-guild music player.

Each Discord guild that uses the bot gets exactly one MusicPlayer instance,
stored in the Music cog's _players dict. Players are created on first /play
and destroyed when the bot leaves the voice channel.

Responsibilities:
  - Maintain an asyncio.Queue of Track objects
  - Run a persistent background playback loop (asyncio.Task)
  - Resolve fresh stream URLs just before FFmpeg starts
  - Bridge FFmpeg's sync "after" callback back to the async loop
  - Manage volume, loop mode, and idle timeout

What this class does NOT do:
  - Send Discord messages (that's the cog's job)
  - Search for music (that's MusicSource's job)
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum, auto
from typing import Callable

import discord

import config
from models.track import Track
from services.music_source import MusicSource

log = logging.getLogger(__name__)


# ── FFmpeg options ─────────────────────────────────────────────────────────────

# reconnect* flags tell FFmpeg to retry the HTTP stream if it drops mid-song,
# which is common for long tracks. before_options are passed before -i (input).
FFMPEG_BEFORE_OPTIONS = (
    "-reconnect 1 " "-reconnect_streamed 1 " "-reconnect_delay_max 5"
)

FFMPEG_OPTIONS = "-vn"  # -vn = no video, audio only


# ── Loop mode ─────────────────────────────────────────────────────────────────


class LoopMode(Enum):
    NONE = auto()  # no looping
    TRACK = auto()  # repeat current track
    QUEUE = auto()  # repeat entire queue (Phase 6+)


# ── MusicPlayer ───────────────────────────────────────────────────────────────


class MusicPlayer:
    """
    Manages audio playback for a single Discord guild.

    Lifecycle:
        1. Created by the Music cog on first /play in a guild.
        2. Runs _playback_loop() as a background Task.
        3. Destroyed (and Task cancelled) when the bot leaves voice.
    """

    def __init__(
        self,
        voice_client: discord.VoiceClient,
        text_channel: discord.TextChannel,
        *,
        on_track_start: Callable[[Track], None] | None = None,
    ) -> None:
        self.voice_client: discord.VoiceClient = voice_client
        self.text_channel: discord.TextChannel = text_channel

        # Callback invoked (with the Track) whenever a new song starts.
        # The Music cog sets this to send the "Now Playing" embed.
        self._on_track_start = on_track_start

        self._queue: asyncio.Queue[Track] = asyncio.Queue()
        self._current: Track | None = None

        self._volume: float = config.DEFAULT_VOLUME / 100.0  # 0.0–1.0
        self._loop_mode: LoopMode = LoopMode.NONE

        # Event set by the FFmpeg "after" callback when a track finishes.
        self._track_finished = asyncio.Event()

        self._idle_timer_task: asyncio.Task | None = None
        self._playback_task: asyncio.Task = asyncio.get_event_loop().create_task(
            self._playback_loop()
        )

        log.info("MusicPlayer created for channel #%s", text_channel.name)

    # ── Public properties ─────────────────────────────────────────────────────

    @property
    def current(self) -> Track | None:
        return self._current

    @property
    def volume(self) -> int:
        """Volume as an integer 0–100."""
        return round(self._volume * 100)

    @volume.setter
    def volume(self, value: int) -> None:
        """Set volume (0–100). Applies immediately if something is playing."""
        self._volume = max(0, min(100, value)) / 100.0
        if self.voice_client.source and isinstance(
            self.voice_client.source, discord.PCMVolumeTransformer
        ):
            self.voice_client.source.volume = self._volume

    @property
    def loop_mode(self) -> LoopMode:
        return self._loop_mode

    @loop_mode.setter
    def loop_mode(self, mode: LoopMode) -> None:
        self._loop_mode = mode

    @property
    def is_playing(self) -> bool:
        return self.voice_client.is_playing()

    @property
    def is_paused(self) -> bool:
        return self.voice_client.is_paused()

    def queue_snapshot(self) -> list[Track]:
        """Return a shallow copy of the upcoming queue (does not consume it)."""
        return list(self._queue._queue)  # type: ignore[attr-defined]

    # ── Queue management ──────────────────────────────────────────────────────

    async def enqueue(self, track: Track) -> int:
        """
        Add a track to the queue.
        Returns the track's position in the queue (1-indexed).
        """
        await self._queue.put(track)
        position = self._queue.qsize()
        log.info("Enqueued [pos=%d]: %s", position, track)
        return position

    async def skip(self) -> Track | None:
        """
        Stop the current track. The playback loop will automatically
        advance to the next one.
        Returns the track that was skipped, or None if nothing was playing.
        """
        if not (self.voice_client.is_playing() or self.voice_client.is_paused()):
            return None
        skipped = self._current
        self.voice_client.stop()  # triggers the "after" callback → sets event
        return skipped

    async def stop(self) -> None:
        """
        Stop playback completely and drain the queue.
        The playback loop will exit cleanly on the next iteration.
        """
        # Drain the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        self._current = None

        if self.voice_client.is_playing() or self.voice_client.is_paused():
            self.voice_client.stop()

    def pause(self) -> bool:
        """Pause playback. Returns True if paused, False if not playing."""
        if self.voice_client.is_playing():
            self.voice_client.pause()
            return True
        return False

    def resume(self) -> bool:
        """Resume playback. Returns True if resumed, False if not paused."""
        if self.voice_client.is_paused():
            self.voice_client.resume()
            return True
        return False

    def shuffle(self) -> bool:
        """
        Randomly shuffle the upcoming queue.
        Returns True if there were tracks to shuffle.
        """
        import random

        items = list(self._queue._queue)  # type: ignore[attr-defined]
        if len(items) < 2:
            return False
        random.shuffle(items)
        self._queue._queue.clear()  # type: ignore[attr-defined]
        for item in items:
            self._queue._queue.append(item)  # type: ignore[attr-defined]
        return True

    def clear_queue(self) -> int:
        """
        Remove all upcoming tracks without stopping the current song.
        Returns the number of tracks removed.
        """
        count = self._queue.qsize()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        return count

    # ── Playback loop ─────────────────────────────────────────────────────────

    async def _playback_loop(self) -> None:
        """
        Background task that dequeues and plays tracks one by one.

        The loop blocks on self._queue.get() while idle (no tracks queued),
        which is cheap — it yields to the event loop rather than spinning.
        """
        log.info("Playback loop started.")

        while True:
            self._track_finished.clear()

            try:
                # Wait for a track. This is an async await — the event loop
                # can process other events freely while we wait here.
                track = await self._queue.get()
            except asyncio.CancelledError:
                log.info("Playback loop cancelled.")
                return

            self._current = track
            self._cancel_idle_timer()

            try:
                await self._play_track(track)
            except Exception as exc:
                log.error("Error playing track %r: %s", track.title, exc, exc_info=True)
                # Notify the text channel without crashing the loop
                try:
                    from utils import embeds

                    await self.text_channel.send(
                        embed=embeds.error(
                            "Playback Error",
                            f"Could not play **{track.short_title}**. Skipping.",
                        )
                    )
                except Exception:
                    pass
                continue

            # Wait for FFmpeg to signal completion (or skip/stop)
            await self._track_finished.wait()

            # Loop mode: re-enqueue current track at the front
            if self._loop_mode == LoopMode.TRACK and self._current is not None:
                # Put it back at the front by inserting into the deque directly
                self._queue._queue.appendleft(self._current)  # type: ignore[attr-defined]

            self._current = None

            # Start idle timer if queue is now empty
            if self._queue.empty():
                self._start_idle_timer()

    async def _play_track(self, track: Track) -> None:
        """
        Resolve stream URL and start FFmpeg for *track*.
        Fires the on_track_start callback once audio begins.
        """
        log.info("Resolving stream for: %r", track.title)
        stream_url = await MusicSource.resolve_stream(track)
        track.stream_url = stream_url

        source = discord.FFmpegPCMAudio(
            stream_url,
            executable=config.FFMPEG_EXECUTABLE,
            before_options=FFMPEG_BEFORE_OPTIONS,
            options=FFMPEG_OPTIONS,
        )

        # Wrap with PCMVolumeTransformer so we can adjust volume live
        volume_source = discord.PCMVolumeTransformer(source, volume=self._volume)

        # The "after" callback is called from a non-async thread when FFmpeg
        # finishes (or errors). We use call_soon_threadsafe to safely set
        # the asyncio.Event from that thread.
        loop = asyncio.get_event_loop()

        def after_playing(error: Exception | None) -> None:
            if error:
                log.error("FFmpeg error while playing %r: %s", track.title, error)
            loop.call_soon_threadsafe(self._track_finished.set)

        self.voice_client.play(volume_source, after=after_playing)
        log.info("Now playing: %s", track)

        # Fire the callback so the cog can send the "Now Playing" embed
        if self._on_track_start:
            try:
                self._on_track_start(track)
            except Exception as exc:
                log.warning("on_track_start callback error: %s", exc)

    # ── Idle timeout ──────────────────────────────────────────────────────────

    def _start_idle_timer(self) -> None:
        """Start countdown to auto-disconnect after IDLE_TIMEOUT seconds."""
        self._cancel_idle_timer()
        self._idle_timer_task = asyncio.get_event_loop().create_task(
            self._idle_timeout_coro()
        )

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer_task and not self._idle_timer_task.done():
            self._idle_timer_task.cancel()
            self._idle_timer_task = None

    async def _idle_timeout_coro(self) -> None:
        """Wait IDLE_TIMEOUT seconds then disconnect."""
        await asyncio.sleep(config.IDLE_TIMEOUT)
        log.info(
            "Idle timeout reached in #%s — disconnecting.",
            self.text_channel.name,
        )
        try:
            from utils import embeds

            await self.text_channel.send(
                embed=embeds.info(
                    "Idle Disconnect",
                    f"No music for {config.IDLE_TIMEOUT // 60} minute(s). Disconnecting.",
                )
            )
        except Exception:
            pass
        await self.destroy()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def destroy(self) -> None:
        """
        Cancel the playback loop, stop audio, and disconnect from voice.
        Always call this when removing a player — never just delete the reference.
        """
        self._cancel_idle_timer()
        self._playback_task.cancel()

        try:
            await self._playback_task
        except asyncio.CancelledError:
            pass

        if self.voice_client.is_playing() or self.voice_client.is_paused():
            self.voice_client.stop()

        if self.voice_client.is_connected():
            await self.voice_client.disconnect(force=False)

        log.info("MusicPlayer destroyed for channel #%s", self.text_channel.name)
