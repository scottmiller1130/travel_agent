"""Tests for the flight search tool."""

import pytest
from tools.cache import _global_cache
from tools.flights import search_flights, _find_airport, _haversine


def setup_function():
    _global_cache.clear()


# ── Airport lookup ─────────────────────────────────────────────────────────────

def test_find_airport_by_iata():
    result = _find_airport("JFK")
    assert result is not None
    iata, data = result
    assert iata == "JFK"
    assert data[0] == "New York"


def test_find_airport_by_city():
    result = _find_airport("London")
    assert result is not None
    iata, data = result
    assert iata == "LHR"


def test_find_airport_case_insensitive():
    result = _find_airport("paris")
    assert result is not None
    assert result[0] == "CDG"


def test_find_airport_alias_bali():
    result = _find_airport("bali")
    assert result is not None
    assert result[0] == "DPS"


def test_find_airport_unknown():
    result = _find_airport("XYZ_NOWHERE_CITY_12345")
    assert result is None


# ── Haversine distance ────────────────────────────────────────────────────────

def test_haversine_same_point():
    assert _haversine(0, 0, 0, 0) == pytest.approx(0, abs=1)


def test_haversine_jfk_to_lhr():
    # JFK: 40.6413, -73.7781  LHR: 51.4700, -0.4543
    km = _haversine(40.6413, -73.7781, 51.4700, -0.4543)
    assert 5500 < km < 5700  # real distance is ~5,540 km


# ── search_flights ────────────────────────────────────────────────────────────

def test_search_flights_basic():
    result = search_flights("JFK", "LHR", "2025-09-15")
    assert result["status"] == "success"
    assert len(result["results"]) > 0


def test_search_flights_sorted_by_price():
    result = search_flights("LAX", "CDG", "2025-08-10")
    prices = [r["price_usd"] for r in result["results"]]
    assert prices == sorted(prices)


def test_search_flights_returns_expected_keys():
    result = search_flights("SFO", "NRT", "2025-11-01")
    first = result["results"][0]
    for key in ("flight_id", "airline", "origin", "destination",
                "departure_date", "price_usd", "stops", "cabin_class"):
        assert key in first, f"Missing key: {key}"


def test_search_flights_budget_filter_removes_expensive():
    # Search without budget first to get baseline prices
    base = search_flights("BOS", "MIA", "2025-07-20")
    if not base["results"]:
        pytest.skip("No results without budget")
    cheapest = base["results"][0]["price_usd"]

    # Filter to below cheapest — should get 0 results
    result = search_flights("BOS", "MIA", "2025-07-20", max_price_usd=cheapest - 1)
    assert result["status"] == "success"
    assert len(result["results"]) == 0
    assert "budget_note" in result


def test_search_flights_budget_filter_keeps_cheap():
    base = search_flights("ORD", "DFW", "2025-06-10")
    if not base["results"]:
        pytest.skip("No results")
    max_price = base["results"][-1]["price_usd"] + 1  # above most expensive
    result = search_flights("ORD", "DFW", "2025-06-10", max_price_usd=max_price)
    assert result["status"] == "success"
    assert all(r["price_usd"] <= max_price for r in result["results"])


def test_search_flights_unknown_origin():
    result = search_flights("NOWHERE_XYZ", "LHR", "2025-09-01")
    assert result["status"] == "error"


def test_search_flights_unknown_destination():
    result = search_flights("JFK", "NOWHERE_XYZ", "2025-09-01")
    assert result["status"] == "error"


def test_search_flights_cabin_classes():
    for cabin in ("economy", "premium_economy", "business", "first"):
        result = search_flights("JFK", "LHR", "2025-10-01", cabin_class=cabin)
        assert result["status"] == "success"
        assert result["results"][0]["cabin_class"] == cabin


def test_search_flights_max_results():
    result = search_flights("LAX", "JFK", "2025-07-04", max_results=2)
    assert result["status"] == "success"
    assert len(result["results"]) <= 2


def test_search_flights_cached():
    _global_cache.clear()
    r1 = search_flights("ATL", "LAS", "2025-12-01")
    r2 = search_flights("ATL", "LAS", "2025-12-01")
    # Prices should be identical (cache hit)
    prices1 = [r["price_usd"] for r in r1["results"]]
    prices2 = [r["price_usd"] for r in r2["results"]]
    assert prices1 == prices2
