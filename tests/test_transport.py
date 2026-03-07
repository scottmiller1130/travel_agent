"""Tests for the ground transportation tool."""

import pytest
from tools.transport import search_ground_transport


def test_short_route_has_all_types():
    """Paris → Brussels is short — all three modes should be available."""
    result = search_ground_transport("CDG", "BRU", "2025-09-01")
    assert result["status"] == "success"
    types = {r["type"] for r in result["results"] if r.get("available", True)}
    assert "train" in types
    assert "bus" in types
    assert "car_rental" in types


def test_long_route_car_not_recommended():
    """JFK → LAX is too far to drive — car rental should be flagged unavailable."""
    result = search_ground_transport("JFK", "LAX", "2025-08-10")
    assert result["status"] == "success"
    car = next((r for r in result["results"] if r["type"] == "car_rental"), None)
    assert car is not None
    assert car.get("available") is False


def test_results_sorted_by_price():
    result = search_ground_transport("PAR", "LYS", "2025-07-15")
    if result["status"] != "success":
        pytest.skip("Route not found")
    available = [r for r in result["results"] if r.get("available", True)]
    prices = [r.get("price_usd", 0) for r in available]
    assert prices == sorted(prices)


def test_filter_to_train_only():
    result = search_ground_transport("CDG", "AMS", "2025-10-01", transport_types=["train"])
    assert result["status"] == "success"
    for r in result["results"]:
        assert r["type"] in ("train",) or r.get("available") is False


def test_filter_to_bus_only():
    result = search_ground_transport("LHR", "MAN", "2025-11-01", transport_types=["bus"])
    assert result["status"] == "success"
    for r in result["results"]:
        assert r["type"] in ("bus",) or r.get("available") is False


def test_passengers_affects_total_price():
    r1 = search_ground_transport("CDG", "BCN", "2025-06-01", passengers=1)
    r2 = search_ground_transport("CDG", "BCN", "2025-06-01", passengers=2)
    # Train/bus per-person price × passengers should differ
    t1 = next((r for r in r1["results"] if r["type"] == "train" and r.get("available", True)), None)
    t2 = next((r for r in r2["results"] if r["type"] == "train" and r.get("available", True)), None)
    if t1 and t2:
        assert t2["price_usd"] == pytest.approx(t1["price_usd"] * 2, rel=0.01)


def test_unknown_origin_returns_error():
    result = search_ground_transport("NOWHERE_XYZ", "LHR", "2025-09-01")
    assert result["status"] == "error"


def test_unknown_destination_returns_error():
    result = search_ground_transport("LHR", "NOWHERE_XYZ", "2025-09-01")
    assert result["status"] == "error"


def test_route_info_present():
    result = search_ground_transport("FRA", "MUC", "2025-08-20")
    assert result["status"] == "success"
    ri = result["route_info"]
    assert ri["distance_km"] > 0
    assert ri["origin_city"]
    assert ri["destination_city"]
