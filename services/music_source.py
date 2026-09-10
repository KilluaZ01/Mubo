"""
services/music_source.py
------------------------
All yt-dlp interactions live here.

Public API:
    MusicSource.search(query, requester_id, requester_name)
        → Search YouTube for a query string, return the top result as a Track.

    MusicSource.extract_url(url, requester_id, requester_name)
        → Extract metadata directly from a URL, return a Track.

    MusicSource.resolve_stream(track)
        → Re-fetch a fresh stream URL for an already-queued Track.
          Call this immediately before handing the URL to FFmpeg.

All blocking yt-dlp calls run in a thread via asyncio.to_thread() so
the Discord event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.request
from typing import Any

import yt_dlp

from models.track import Track

log = logging.getLogger(__name__)


# ── yt-dlp configuration ──────────────────────────────────────────────────────

# Base options shared by all extractors.
# We request the best audio-only format to minimise bandwidth.
_YDL_BASE_OPTS: dict[str, Any] = {
    "format": "bestaudio/best",
    "noplaylist": True,  # never expand playlists unless we ask
    "quiet": True,  # suppress yt-dlp console output
    "no_warnings": True,
    "default_search": "ytsearch",  # prefix bare queries with ytsearch:
    "source_address": "0.0.0.0",  # bind to all interfaces (IPv4 fallback)
    "extract_flat": False,  # we want full metadata, not just IDs
}

# Options used when we only need metadata (no stream URL yet).
# extract_flat=True at the search stage would be faster but gives us
# less reliable duration / thumbnail data, so we do a full extract.
_YDL_SEARCH_OPTS: dict[str, Any] = {
    **_YDL_BASE_OPTS,
}

# Options used when resolving a fresh stream URL just before playback.
_YDL_STREAM_OPTS: dict[str, Any] = {
    **_YDL_BASE_OPTS,
}

_YDL_AUTOCOMPLETE_OPTS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "noplaylist": True,
    "extract_flat": True,
    "skip_download": True,
}

# URL pattern — if the user's input matches this, treat it as a URL not a query.
_URL_RE = re.compile(
    r"^(https?://)?"  # optional scheme
    r"(www\.)?"  # optional www
    r"(youtube\.com|youtu\.be|soundcloud\.com|twitch\.tv)"  # supported hosts
    r"/.+",
    re.IGNORECASE,
)


def _is_url(text: str) -> bool:
    return bool(_URL_RE.match(text.strip()))


def _build_track(
    info: dict[str, Any],
    requester_id: int,
    requester_name: str,
) -> Track:
    """
    Convert a yt-dlp info dict into a Track.
    Handles missing fields defensively — yt-dlp doesn't guarantee every key.
    """
    # yt-dlp nests playlist results under "entries"; unwrap if needed.
    if "entries" in info:
        entries = [e for e in info["entries"] if e]  # filter None entries
        if not entries:
            raise ValueError("No results found.")
        info = entries[0]

    title = info.get("title") or "Unknown Title"
    webpage_url = info.get("webpage_url") or info.get("url") or ""
    uploader = info.get("uploader") or info.get("channel") or None
    duration = info.get("duration")  # seconds (int) or None
    thumbnail = info.get("thumbnail") or None

    # Stream URL: yt-dlp may give us the direct URL in "url" field
    # after a full extract. We store it but always re-resolve before playback.
    stream_url = info.get("url") or None

    return Track(
        title=title,
        webpage_url=webpage_url,
        uploader=uploader,
        stream_url=stream_url,
        duration=int(duration) if duration is not None else None,
        thumbnail=thumbnail,
        requester_id=requester_id,
        requester_name=requester_name,
    )


def _extract_sync(query_or_url: str, opts: dict[str, Any]) -> dict[str, Any]:
    """
    Synchronous yt-dlp extraction — runs in a thread pool.
    Raises yt_dlp.DownloadError on failure.
    """
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query_or_url, download=False)
        if info is None:
            raise ValueError(f"yt-dlp returned no data for: {query_or_url!r}")
        return info


def _youtube_video_suggestions_sync(query: str) -> list[tuple[str, str]]:
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20260101.01.00",
            }
        },
        "query": query,
    }
    request = urllib.request.Request(
        "https://www.youtube.com/youtubei/v1/search?prettyPrint=false",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(request, timeout=1.5) as response:
        data = json.loads(response.read().decode("utf-8"))

    results: list[tuple[str, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            renderer = value.get("videoRenderer")
            if renderer:
                title = renderer.get("title", {}).get("runs", [{}])[0].get("text")
                if title:
                    results.append((title, title))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    return results[:5]


# ── Public API ────────────────────────────────────────────────────────────────


class MusicSource:
    """Namespace for async yt-dlp helpers."""

    _suggestion_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}
    _suggestion_cache_ttl = 60.0

    @staticmethod
    async def suggestions(query: str) -> list[tuple[str, str]]:
        """Return lightweight song-title choices for Discord autocomplete."""
        normalized_query = query.strip().lower()
        if not normalized_query:
            return []

        now = time.monotonic()
        cached = MusicSource._suggestion_cache.get(normalized_query)
        if cached and now - cached[0] < MusicSource._suggestion_cache_ttl:
            return cached[1]

        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(
                    _youtube_video_suggestions_sync,
                    normalized_query,
                ),
                timeout=1.8,
            )
        except asyncio.TimeoutError:
            log.debug("Autocomplete search timed out for %r", normalized_query)
            return []
        except Exception as exc:
            log.debug("Autocomplete search failed for %r: %s", normalized_query, exc)
            return []

        results = results[:25]
        MusicSource._suggestion_cache[normalized_query] = (now, results)
        if len(MusicSource._suggestion_cache) > 100:
            oldest_query = min(
                MusicSource._suggestion_cache,
                key=lambda key: MusicSource._suggestion_cache[key][0],
            )
            del MusicSource._suggestion_cache[oldest_query]
        return results

    @staticmethod
    async def search(
        query: str,
        requester_id: int,
        requester_name: str,
    ) -> Track:
        """
        Search YouTube for *query* and return the top result as a Track.

        Uses ytsearch1: prefix so yt-dlp returns exactly one result.
        Runs in a thread to avoid blocking the event loop.

        Raises:
            ValueError: No results found or yt-dlp returned empty data.
            yt_dlp.DownloadError: Network or extraction failure.
        """
        search_query = f"ytsearch1:{query}"
        log.info("Searching yt-dlp: %r", search_query)

        info = await asyncio.to_thread(_extract_sync, search_query, _YDL_SEARCH_OPTS)
        track = _build_track(info, requester_id, requester_name)

        log.info("Search result: %s", track)
        return track

    @staticmethod
    async def extract_url(
        url: str,
        requester_id: int,
        requester_name: str,
    ) -> Track:
        """
        Extract metadata from a direct URL and return a Track.

        Runs in a thread to avoid blocking the event loop.

        Raises:
            ValueError: yt-dlp returned no data.
            yt_dlp.DownloadError: Network or extraction failure.
        """
        log.info("Extracting URL: %r", url)

        info = await asyncio.to_thread(_extract_sync, url, _YDL_SEARCH_OPTS)
        track = _build_track(info, requester_id, requester_name)

        log.info("URL result: %s", track)
        return track

    @staticmethod
    async def from_query(
        query_or_url: str,
        requester_id: int,
        requester_name: str,
    ) -> Track:
        """
        Smart entry point: auto-detects whether input is a URL or search query
        and dispatches to the correct method.

        This is what /play will call — it doesn't need to know the difference.
        """
        if _is_url(query_or_url):
            return await MusicSource.extract_url(
                query_or_url, requester_id, requester_name
            )
        return await MusicSource.search(query_or_url, requester_id, requester_name)

    @staticmethod
    async def resolve_stream(track: Track) -> str:
        """
        Fetch a fresh, playable stream URL for *track*.

        YouTube stream URLs expire after ~6 hours, so we always re-resolve
        immediately before handing the URL to FFmpeg.

        Returns the stream URL string.

        Raises:
            ValueError: Could not extract a stream URL.
            yt_dlp.DownloadError: Network or extraction failure.
        """
        log.info("Resolving stream URL for: %r", track.title)

        info = await asyncio.to_thread(
            _extract_sync, track.webpage_url, _YDL_STREAM_OPTS
        )

        # After full extraction the direct audio URL is in "url"
        if "entries" in info:
            entries = [e for e in info["entries"] if e]
            info = entries[0] if entries else {}

        stream_url = info.get("url")
        if not stream_url:
            raise ValueError(f"No stream URL found for: {track.webpage_url}")

        log.info("Stream URL resolved for: %r", track.title)
        return stream_url
