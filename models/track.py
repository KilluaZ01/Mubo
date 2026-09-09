"""
models/track.py
---------------
Immutable data model for a single audio track.

A Track is created once when a user requests a song and carried through
the queue until playback begins. At that point, music_source.py fetches
a fresh stream_url (since YouTube stream URLs expire in ~6 hours).

All fields except stream_url are populated at search/enqueue time.
stream_url is populated just before playback starts.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Track:
    """Represents a single queued audio track."""

    # ── Identity ──────────────────────────────────────────────────────────────

    title: str
    """Human-readable song title."""

    webpage_url: str
    """Permanent page URL (e.g. https://www.youtube.com/watch?v=...).
    Used to re-fetch a fresh stream_url at playback time."""

    uploader: str | None
    """Channel or artist name as reported by yt-dlp."""

    # ── Playback ──────────────────────────────────────────────────────────────

    stream_url: str | None
    """Direct audio stream URL. May be None if not yet resolved.
    Populated by MusicSource.resolve_stream() just before FFmpeg starts."""

    duration: int | None
    """Track length in seconds, or None if unknown."""

    # ── Display ───────────────────────────────────────────────────────────────

    thumbnail: str | None
    """Thumbnail image URL for embed display."""

    # ── Request metadata ─────────────────────────────────────────────────────

    requester_id: int
    """Discord user ID of whoever queued this track."""

    requester_name: str
    """Display name of the requester at time of request."""

    # ── Optional extras (populated later / by future features) ───────────────

    playlist_index: int | None = field(default=None)
    """Position in a playlist, if this track came from one."""

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def duration_str(self) -> str:
        """Return duration formatted as M:SS or H:MM:SS, or '?:??' if unknown."""
        if self.duration is None:
            return "?:??"
        seconds = int(self.duration)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    @property
    def short_title(self) -> str:
        """Title truncated to 60 characters for compact embed display."""
        if len(self.title) <= 60:
            return self.title
        return self.title[:57] + "…"

    def __str__(self) -> str:
        uploader = self.uploader or "Unknown"
        return f"{self.title} — {uploader} [{self.duration_str}]"
