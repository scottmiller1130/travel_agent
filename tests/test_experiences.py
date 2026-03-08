"""Tests for the experiences / activities search tool."""

import os
import pytest
from unittest.mock import patch, MagicMock
from tools.experiences import search_experiences, _viator_search, _getyourguide_search

try:
    import httpx as _httpx_lib
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

needs_httpx = pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")


# ── API stub guards ───────────────────────────────────────────────────────────

def test_viator_returns_none_without_key():
    os.environ.pop("VIATOR_API_KEY", None)
    result = _viator_search("Paris", "museum", None, 5)
    assert result is None


def test_getyourguide_returns_none_without_key():
    os.environ.pop("GETYOURGUIDE_API_KEY", None)
    result = _getyourguide_search("Paris", "tour", None, 5)
    assert result is None


# ── search_experiences — curated fallback (no external APIs) ─────────────────

def _no_api_env():
    """Patch env so no API keys are present and httpx is disabled."""
    return patch("tools.experiences._HTTPX", False)


def test_search_experiences_fallback_returns_success():
    with _no_api_env():
        result = search_experiences("Paris", category="museum")
    assert result["status"] == "success"
    assert len(result["results"]) > 0


def test_search_experiences_fallback_returns_expected_keys():
    with _no_api_env():
        result = search_experiences("Tokyo", category="food")
    first = result["results"][0]
    for key in ("experience_id", "title", "category", "duration_hours",
                "price_usd", "bookable"):
        assert key in first, f"Missing key: {key}"


def test_search_experiences_max_results_respected():
    with _no_api_env():
        result = search_experiences("London", max_results=3)
    assert result["status"] == "success"
    assert len(result["results"]) <= 3


def test_search_experiences_price_filter():
    with _no_api_env():
        # Use a high enough limit to get all results, then a tight one to filter
        all_results = search_experiences("Rome", category="museum", max_results=10)
        if not all_results["results"]:
            pytest.skip("No results to filter")
        max_price = all_results["results"][0]["price_usd"]  # cheapest price
        result = search_experiences("Rome", category="museum", max_price_usd=max_price)
    for exp in result["results"]:
        assert exp["price_usd"] <= max_price


def test_search_experiences_various_categories():
    categories = ["museum", "food", "adventure", "culture", "history",
                  "nature", "nightlife", "shopping", "sightseeing"]
    with _no_api_env():
        for cat in categories:
            result = search_experiences("Barcelona", category=cat)
            assert result["status"] == "success", f"Failed for category: {cat}"


def test_search_experiences_fallback_bookable_is_false():
    """Curated fallback results are never bookable (no real booking link)."""
    with _no_api_env():
        result = search_experiences("Amsterdam")
    for exp in result["results"]:
        assert exp["bookable"] is False


def test_search_experiences_price_usd_non_negative():
    with _no_api_env():
        result = search_experiences("Berlin", category="tour")
    for exp in result["results"]:
        assert exp["price_usd"] >= 0


def test_search_experiences_duration_positive():
    with _no_api_env():
        result = search_experiences("Lisbon")
    for exp in result["results"]:
        assert exp["duration_hours"] > 0


# ── OpenTripMap path (requires httpx) ────────────────────────────────────────


class _FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data


def _make_otm_httpx():
    mock = MagicMock()

    def get_side_effect(url, **kwargs):
        if "nominatim" in url:
            return _FakeResponse([{"lat": "48.8566", "lon": "2.3522"}])
        if "opentripmap" in url and "radius" in url:
            return _FakeResponse([
                {"name": "Musée d'Orsay", "xid": "W123", "point": {"lat": 48.86, "lon": 2.32}},
                {"name": "Louvre", "xid": "W124", "point": {"lat": 48.86, "lon": 2.33}},
                {"name": "", "xid": "W125", "point": {"lat": 48.86, "lon": 2.34}},
            ])
        if "opentripmap" in url and "xid" in url:
            return _FakeResponse({})
        return _FakeResponse([])

    mock.get.side_effect = get_side_effect
    return mock


@needs_httpx
@patch("tools.experiences._httpx")
def test_search_experiences_opentripmap_real_names(mock_httpx):
    mock_httpx.get.side_effect = _make_otm_httpx().get.side_effect

    os.environ.pop("VIATOR_API_KEY", None)
    os.environ.pop("GETYOURGUIDE_API_KEY", None)

    result = search_experiences("Paris", category="museum", max_results=5)
    assert result["status"] == "success"
    names = [r["title"] for r in result["results"]]
    assert "" not in names
    assert any("Orsay" in n or "Louvre" in n for n in names)
