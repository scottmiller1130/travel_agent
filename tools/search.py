"""
Web search tool — research destinations, visa requirements, travel advisories, etc.
Uses mock responses; swap in Brave Search or Serper API when key is set.
"""

import os
import random


MOCK_KNOWLEDGE = {
    "visa": {
        "result": "Visa requirements vary by nationality. US citizens typically get 90-day visa-free access to EU Schengen countries. Always verify with the official embassy website before traveling.",
        "sources": ["travel.state.gov", "iatatravelcentre.com"],
    },
    "currency": {
        "result": "It's recommended to use a mix of card (Wise or Revolut for low FX fees) and local cash. ATMs are widely available in major cities. Avoid airport exchange booths.",
        "sources": ["wise.com", "xe.com"],
    },
    "safety": {
        "result": "Check current travel advisories from your government's foreign affairs department. Most tourist destinations are safe; stay aware of petty theft in crowded areas.",
        "sources": ["travel.state.gov", "gov.uk/foreign-travel-advice"],
    },
    "food": {
        "result": "Local cuisine varies widely. Research top local dishes before you go. Food tours are a great way to explore local flavors safely.",
        "sources": ["tripadvisor.com", "eater.com"],
    },
    "transport": {
        "result": "Most cities have metro/subway systems. Ride-share apps (Uber, Bolt, Grab) work in many destinations. Consider multi-day transit passes for savings.",
        "sources": ["rome2rio.com", "seat61.com"],
    },
    "default": {
        "result": "Based on current travel information, this destination is popular with tourists and has good infrastructure for visitors. Check recent traveler reviews for up-to-date tips.",
        "sources": ["tripadvisor.com", "lonelyplanet.com"],
    },
}


def web_search(query: str, num_results: int = 3) -> dict:
    """Search the web for travel-related information."""
    if os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("SERPER_API_KEY"):
        raise NotImplementedError("Real web search integration not yet wired up")

    query_lower = query.lower()
    matched = None
    for keyword, data in MOCK_KNOWLEDGE.items():
        if keyword in query_lower:
            matched = data
            break
    if not matched:
        matched = MOCK_KNOWLEDGE["default"]

    destination = _extract_destination(query)
    result_text = matched["result"]
    if destination:
        result_text = f"For {destination}: {result_text}"

    return {
        "status": "success",
        "query": query,
        "results": [
            {
                "title": f"Travel Guide: {query[:60]}",
                "snippet": result_text,
                "url": f"https://{matched['sources'][0]}/travel/{destination or 'guide'}",
            }
        ] + [
            {
                "title": f"Additional resource on {query[:40]}",
                "snippet": f"See {src} for more details.",
                "url": f"https://{src}",
            }
            for src in matched["sources"][1:]
        ],
        "note": "Mock search — connect Brave Search or Serper API for real results",
    }


def _extract_destination(query: str) -> str | None:
    known = ["lisbon", "paris", "rome", "barcelona", "london", "bali", "tokyo",
             "new york", "dubai", "amsterdam", "prague", "vienna", "istanbul", "cancun"]
    q = query.lower()
    for dest in known:
        if dest in q:
            return dest.title()
    return None
