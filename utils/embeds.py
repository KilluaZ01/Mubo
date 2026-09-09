"""
utils/embeds.py
---------------
Centralised Discord embed builders.

Every command response goes through one of these functions so the bot
has a consistent visual identity. Colours, icons, and layout are defined
in one place — change them here and the whole bot updates.
"""

from __future__ import annotations

import discord
from models.track import Track

# ── Colour palette ─────────────────────────────────────────────────────────────


class Colour:
    SUCCESS = discord.Colour.from_str("#57F287")  # green
    ERROR = discord.Colour.from_str("#ED4245")  # red
    INFO = discord.Colour.from_str("#5865F2")  # blurple
    WARNING = discord.Colour.from_str("#FEE75C")  # yellow
    MUSIC = discord.Colour.from_str("#1DB954")  # Spotify green


# ── Generic builders ───────────────────────────────────────────────────────────


def success(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(
        title=f"✅  {title}",
        description=description or discord.utils.MISSING,
        colour=Colour.SUCCESS,
    )


def error(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(
        title=f"❌  {title}",
        description=description or discord.utils.MISSING,
        colour=Colour.ERROR,
    )


def info(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(
        title=f"ℹ️  {title}",
        description=description or discord.utils.MISSING,
        colour=Colour.INFO,
    )


def warning(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(
        title=f"⚠️  {title}",
        description=description or discord.utils.MISSING,
        colour=Colour.WARNING,
    )


# ── Voice embeds ───────────────────────────────────────────────────────────────


def joined_channel(channel_name: str) -> discord.Embed:
    return success(
        "Joined Voice Channel",
        f"Connected to **{channel_name}**.",
    )


def left_channel(channel_name: str) -> discord.Embed:
    return info(
        "Left Voice Channel",
        f"Disconnected from **{channel_name}**.",
    )


# ── Music embeds ───────────────────────────────────────────────────────────────


def now_playing(track: Track) -> discord.Embed:
    """Rich embed shown when a track begins playing."""
    embed = discord.Embed(
        title="🎵  Now Playing",
        colour=Colour.MUSIC,
    )

    embed.add_field(name="Song", value=track.short_title, inline=False)
    embed.add_field(
        name="Artist",
        value=track.uploader or "Unknown",
        inline=True,
    )
    embed.add_field(name="Duration", value=track.duration_str, inline=True)
    embed.add_field(
        name="Requested by",
        value=track.requester_name,
        inline=True,
    )

    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)

    embed.set_footer(text=f"🔗  {track.webpage_url}")
    return embed


def added_to_queue(track: Track, position: int) -> discord.Embed:
    """Embed shown when a track is added to a non-empty queue."""
    embed = discord.Embed(
        title="➕  Added to Queue",
        colour=Colour.INFO,
    )

    embed.add_field(name="Song", value=track.short_title, inline=False)
    embed.add_field(
        name="Artist",
        value=track.uploader or "Unknown",
        inline=True,
    )
    embed.add_field(name="Duration", value=track.duration_str, inline=True)
    embed.add_field(name="Position in Queue", value=str(position), inline=True)

    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)

    return embed


def queue_list(
    current: Track | None,
    upcoming: list[Track],
    page: int = 1,
    page_size: int = 10,
) -> discord.Embed:
    """
    Paginated queue embed.

    Shows the currently playing track at the top, then the upcoming
    tracks for the requested page.
    """
    embed = discord.Embed(title="🎶  Music Queue", colour=Colour.MUSIC)

    # ── Now playing ───────────────────────────────────────────────────────────
    if current:
        embed.add_field(
            name="Now Playing",
            value=f"▶  **{current.short_title}** — {current.uploader or 'Unknown'} `{current.duration_str}`",
            inline=False,
        )
    else:
        embed.add_field(name="Now Playing", value="Nothing playing.", inline=False)

    # ── Upcoming tracks ───────────────────────────────────────────────────────
    if not upcoming:
        embed.add_field(name="Up Next", value="Queue is empty.", inline=False)
        return embed

    total_pages = max(1, (len(upcoming) + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    slice_ = upcoming[start : start + page_size]

    lines = [
        f"`{start + i + 1}.` **{t.short_title}** — {t.uploader or 'Unknown'} `{t.duration_str}`"
        for i, t in enumerate(slice_)
    ]

    total_duration = sum(t.duration or 0 for t in upcoming)
    hours, remainder = divmod(total_duration, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        duration_str = f"{hours}h {minutes}m {seconds}s"
    else:
        duration_str = f"{minutes}m {seconds}s"

    embed.add_field(
        name=f"Up Next  •  {len(upcoming)} track(s)  •  {duration_str} total",
        value="\n".join(lines),
        inline=False,
    )

    if total_pages > 1:
        embed.set_footer(text=f"Page {page}/{total_pages}  •  /queue page:<number>")

    return embed


def now_playing_status(track: Track) -> discord.Embed:
    """/nowplaying command embed — more detailed than the auto now-playing."""
    embed = discord.Embed(
        title="🎵  Now Playing",
        colour=Colour.MUSIC,
    )
    embed.add_field(name="Song", value=track.title, inline=False)
    embed.add_field(name="Artist", value=track.uploader or "Unknown", inline=True)
    embed.add_field(name="Duration", value=track.duration_str, inline=True)
    embed.add_field(name="Requested by", value=track.requester_name, inline=True)
    embed.add_field(name="URL", value=track.webpage_url, inline=False)

    if track.thumbnail:
        embed.set_image(url=track.thumbnail)

    return embed
