"""
test_search.py
--------------
Standalone test for MusicSource — run this BEFORE Phase 4 to confirm
yt-dlp search and stream resolution work on your machine.

Usage:
    python test_search.py
    python test_search.py "your search query"
    python test_search.py "https://www.youtube.com/watch?v=..."
"""

from __future__ import annotations

import asyncio
import sys

# Make sure Python can find our modules when run from project root
sys.path.insert(0, ".")

from services.music_source import MusicSource


async def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "Blinding Lights The Weeknd"

    print(f"\n{'='*60}")
    print(f"  Testing MusicSource")
    print(f"  Query: {query!r}")
    print(f"{'='*60}\n")

    # ── Step 1: Search / extract metadata ─────────────────────────────────────
    print("[ 1/2 ] Searching / extracting metadata …")
    try:
        track = await MusicSource.from_query(
            query,
            requester_id=0,
            requester_name="TestUser",
        )
    except Exception as exc:
        print(f"\n  ✗  Search failed: {exc}")
        return

    print(f"  ✓  Title     : {track.title}")
    print(f"     Uploader  : {track.uploader}")
    print(f"     Duration  : {track.duration_str}")
    print(f"     Thumbnail : {track.thumbnail or '(none)'}")
    print(f"     Page URL  : {track.webpage_url}")

    # ── Step 2: Resolve stream URL ────────────────────────────────────────────
    print("\n[ 2/2 ] Resolving stream URL …")
    try:
        stream_url = await MusicSource.resolve_stream(track)
    except Exception as exc:
        print(f"\n  ✗  Stream resolution failed: {exc}")
        return

    # Stream URLs are very long — just show enough to confirm it worked
    preview = stream_url[:80] + "…" if len(stream_url) > 80 else stream_url
    print(f"  ✓  Stream URL: {preview}")

    print(f"\n{'='*60}")
    print("  All checks passed — MusicSource is working correctly.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
