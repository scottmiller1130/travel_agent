"""
Trip inspiration import — extract travel ideas from any URL or pasted text.

Inspired by Mindtrip's "Start Anywhere" feature. Takes a blog post, article,
YouTube description, social caption, or free-form text and extracts actionable
trip planning data: destinations, trip styles, must-see activities, and an
initial itinerary framework the agent can use to start planning immediately.

Usage: user pastes a URL like a travel blog, TripAdvisor article, YouTube
video description, or their own notes — this tool structures it for the agent.
"""

import os

try:
    import httpx as _httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False


def _is_safe_url(url: str) -> bool:
    """Return False if the URL targets a private/loopback address (SSRF guard)."""
    try:
        from urllib.parse import urlparse
        import ipaddress
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        # Block bare localhost and .local domains
        if host in ("localhost",) or host.endswith(".local"):
            return False
        # Block numeric IPs that resolve to private/loopback ranges
        try:
            addr = ipaddress.ip_address(host)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return False
        except ValueError:
            pass  # Not a numeric IP — hostname is fine
        return True
    except Exception:
        return False


def _fetch_url(url: str) -> str | None:
    """Fetch and extract readable text from a URL."""
    if not _HTTPX:
        return None
    if not _is_safe_url(url):
        return None
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = _httpx.get(url, headers=headers, timeout=12, follow_redirects=False)
        if r.status_code != 200:
            return None
        content_type = r.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            return None
        text = r.text
    except Exception:
        return None

    # Strip HTML tags to get readable text
    try:
        import re
        # Remove scripts, styles, and nav elements (common noise)
        text = re.sub(r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>",
                      "", text, flags=re.DOTALL | re.IGNORECASE)
        # Strip all remaining HTML tags
        text = re.sub(r"<[^>]+>", " ", text)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        # Return first 8000 chars (enough for a blog post summary)
        return text[:8000]
    except Exception:
        return text[:8000] if text else None


def get_inspiration(
    source: str,
    trip_type: str | None = None,
) -> dict:
    """
    Extract travel inspiration from a URL, blog post, article, or free-form text.

    Accepts:
    - A URL to a travel blog, article, YouTube video, or TripAdvisor page
    - A pasted block of text (notes, copied content, social captions)
    - A description like "10 days in Japan — rice fields, Tokyo nightlife, Kyoto temples"

    Returns structured trip ideas: destinations, suggested activities, trip style,
    duration estimate, and budget tier — ready for the agent to use as a planning seed.

    trip_type hints: "luxury", "adventure", "road_trip", "backpacker", "family",
                     "honeymoon", "solo", "group", "wellness", "foodie"
    """
    # Determine if source is a URL or free text
    is_url = source.strip().startswith(("http://", "https://"))

    raw_content = None
    source_label = "pasted text"

    if is_url:
        raw_content = _fetch_url(source.strip())
        if not raw_content:
            return {
                "status": "error",
                "message": (
                    f"Could not fetch content from '{source}'. "
                    "Try pasting the text directly instead."
                ),
            }
        source_label = source.strip()
    else:
        raw_content = source.strip()
        source_label = "pasted inspiration"

    if not raw_content or len(raw_content) < 30:
        return {
            "status": "error",
            "message": "Not enough content to extract trip ideas from. Please provide more detail.",
        }

    # Return the raw content + metadata for the agent to process
    # The Claude agent is better at NLP extraction than hardcoded regexes,
    # so we return the content and let the agent do the semantic parsing.
    result = {
        "status": "success",
        "source": source_label,
        "content_length": len(raw_content),
        "content": raw_content[:6000],  # Cap at 6k chars to stay within context
        "instructions": (
            "The above content was fetched from the user's inspiration source. "
            "Extract from it: (1) destination(s) and specific places mentioned, "
            "(2) activities and experiences highlighted, (3) recommended trip duration, "
            "(4) budget tier implied (budget/mid-range/luxury), "
            "(5) best travel season mentioned. "
            "Then immediately start planning the trip based on these insights — "
            "ask the user to confirm destinations and dates, then search flights and hotels."
        ),
    }

    if trip_type:
        result["trip_type_hint"] = trip_type

    return result
