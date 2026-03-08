"""Tests for the hotel search tool."""

import pytest
import random
from unittest.mock import patch, MagicMock
from tools.hotels import search_hotels, _estimate_price

try:
    import httpx as _httpx_lib
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

needs_httpx = pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")


# ── _estimate_price (pure function, no network) ───────────────────────────────

def test_estimate_price_premium_destination():
    rng = random.Random(42)
    price = _estimate_price("paris", stars=4, rng=rng)
    assert price > 100


def test_estimate_price_budget_destination():
    rng = random.Random(42)
    price = _estimate_price("hanoi", stars=2, rng=rng)
    assert price < 120


def test_estimate_price_higher_stars_cost_more():
    price2 = _estimate_price("barcelona", stars=2, rng=random.Random(42))
    price5 = _estimate_price("barcelona", stars=5, rng=random.Random(42))
    assert price5 > price2


def test_estimate_price_unknown_destination_returns_positive():
    rng = random.Random(42)
    price = _estimate_price("unknowncity123", stars=None, rng=rng)
    assert price > 0


# ── search_hotels — no-httpx error path ──────────────────────────────────────

def test_search_hotels_no_httpx_returns_error():
    with patch("tools.hotels._HTTPX", False):
        result = search_hotels("paris", "2026-06-01", "2026-06-03")
    assert result["status"] == "error"
    assert "httpx" in result["message"].lower()


# ── search_hotels — httpx-dependent paths (skipped if httpx not installed) ───


class _FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data


def _fake_hotels_overpass():
    return {
        "elements": [
            {
                "id": 1, "lat": 48.857, "lon": 2.352,
                "tags": {"name": "Hotel Lumière", "tourism": "hotel", "stars": "4"},
            },
            {
                "id": 2, "lat": 48.858, "lon": 2.353,
                "tags": {"name": "Budget Inn Paris", "tourism": "hotel"},
            },
            {
                "id": 3, "lat": 48.859, "lon": 2.354,
                "tags": {"name": "Le Petit Hostel", "tourism": "hostel"},
            },
        ]
    }


def _make_httpx_mock():
    mock = MagicMock()

    def get_side(url, **kwargs):
        if "nominatim" in url:
            return _FakeResponse([{
                "lat": "48.8566", "lon": "2.3522",
                "boundingbox": ["48.8", "48.9", "2.3", "2.4"],
            }])
        return _FakeResponse([])

    def post_side(url, **kwargs):
        if "overpass" in url:
            return _FakeResponse(_fake_hotels_overpass())
        return _FakeResponse({"elements": []})

    mock.get.side_effect = get_side
    mock.post.side_effect = post_side
    return mock


@needs_httpx
@patch("tools.hotels._httpx")
def test_search_hotels_basic_success(mock_httpx):
    m = _make_httpx_mock()
    mock_httpx.get.side_effect = m.get.side_effect
    mock_httpx.post.side_effect = m.post.side_effect

    result = search_hotels("paris", "2026-06-01", "2026-06-05")
    assert result["status"] == "success"
    assert len(result["results"]) > 0


@needs_httpx
@patch("tools.hotels._httpx")
def test_search_hotels_returns_expected_keys(mock_httpx):
    m = _make_httpx_mock()
    mock_httpx.get.side_effect = m.get.side_effect
    mock_httpx.post.side_effect = m.post.side_effect

    result = search_hotels("paris", "2026-06-01", "2026-06-03")
    assert result["status"] == "success"
    first = result["results"][0]
    for key in ("hotel_id", "name", "price_per_night_usd", "total_price_usd",
                "nights", "guests", "check_in", "check_out"):
        assert key in first, f"Missing key: {key}"


@needs_httpx
@patch("tools.hotels._httpx")
def test_search_hotels_sorted_by_price(mock_httpx):
    m = _make_httpx_mock()
    mock_httpx.get.side_effect = m.get.side_effect
    mock_httpx.post.side_effect = m.post.side_effect

    result = search_hotels("paris", "2026-07-01", "2026-07-04")
    assert result["status"] == "success"
    prices = [r["price_per_night_usd"] for r in result["results"]]
    assert prices == sorted(prices)


@needs_httpx
@patch("tools.hotels._httpx")
def test_search_hotels_max_results_respected(mock_httpx):
    m = _make_httpx_mock()
    mock_httpx.get.side_effect = m.get.side_effect
    mock_httpx.post.side_effect = m.post.side_effect

    result = search_hotels("paris", "2026-06-01", "2026-06-03", max_results=2)
    assert result["status"] == "success"
    assert len(result["results"]) <= 2


@needs_httpx
@patch("tools.hotels._httpx")
def test_search_hotels_total_price_matches_nights(mock_httpx):
    m = _make_httpx_mock()
    mock_httpx.get.side_effect = m.get.side_effect
    mock_httpx.post.side_effect = m.post.side_effect

    result = search_hotels("paris", "2026-06-01", "2026-06-04")  # 3 nights
    assert result["status"] == "success"
    for hotel in result["results"]:
        expected = hotel["price_per_night_usd"] * 3 * hotel["rooms"]
        assert hotel["total_price_usd"] == pytest.approx(expected, rel=0.01)


@needs_httpx
@patch("tools.hotels._httpx")
def test_search_hotels_geocode_failure_uses_price_fallback(mock_httpx):
    # Nominatim returns empty → falls back to _price_only_hotels
    mock_httpx.get.return_value = _FakeResponse([])
    mock_httpx.post.return_value = _FakeResponse({"elements": []})

    result = search_hotels("smalltown", "2026-06-01", "2026-06-03")
    assert result["status"] == "success"
    assert len(result["results"]) > 0
