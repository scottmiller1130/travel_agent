"""
Web search — Wikipedia REST API (no key, no registration required).
Optionally upgrades to Brave Search if BRAVE_SEARCH_API_KEY is set
(free tier: https://brave.com/search/api/ — 2,000 queries/month).
"""

import os
import urllib.parse

try:
    import httpx as _httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False

WIKI_OPENSEARCH = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY    = "https://en.wikipedia.org/api/rest_v1/page/summary"
BRAVE_URL       = "https://api.search.brave.com/res/v1/web/search"


def _wikipedia(query: str, num_results: int) -> dict:
    r = _httpx.get(WIKI_OPENSEARCH, params={
        "action": "opensearch", "search": query,
        "limit": num_results, "format": "json", "origin": "*",
    }, timeout=8)
    r.raise_for_status()
    _, titles, snippets, urls = r.json()

    results = [
        {"title": t, "snippet": s or f"Wikipedia article: {t}", "url": u}
        for t, s, u in zip(titles, snippets, urls)
    ]

    # Enrich top result with full page extract
    if titles:
        try:
            slug = urllib.parse.quote(titles[0].replace(" ", "_"))
            sr = _httpx.get(f"{WIKI_SUMMARY}/{slug}", timeout=8)
            if sr.status_code == 200 and results:
                results[0]["snippet"] = sr.json().get("extract", results[0]["snippet"])[:1200]
        except Exception:
            pass

    return {"status": "success", "query": query, "results": results,
            "source": "Wikipedia (live — no API key required)"}


def _brave(query: str, num_results: int, key: str) -> dict:
    r = _httpx.get(
        BRAVE_URL,
        headers={"X-Subscription-Token": key, "Accept": "application/json", "Accept-Encoding": "gzip"},
        params={"q": query, "count": min(num_results, 10), "search_lang": "en", "safesearch": "moderate"},
        timeout=10,
    )
    r.raise_for_status()
    items = r.json().get("web", {}).get("results", [])
    return {
        "status": "success", "query": query,
        "results": [{"title": i.get("title", ""), "snippet": i.get("description", ""), "url": i.get("url", "")}
                    for i in items],
        "source": "Brave Search (live)",
    }


def web_search(query: str, num_results: int = 5) -> dict:
    """
    Search for travel information.
    Uses Wikipedia by default (no key). Set BRAVE_SEARCH_API_KEY for full web results.
    """
    if not _HTTPX:
        return {"status": "error", "message": "httpx not installed. Run: pip install httpx"}
    key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    try:
        return _brave(query, num_results, key) if key else _wikipedia(query, num_results)
    except Exception as exc:
        if key:
            try:
                res = _wikipedia(query, num_results)
                res["source"] += f" (Brave unavailable: {exc})"
                return res
            except Exception:
                pass
        return {"status": "error", "message": f"Search failed: {exc}"}
