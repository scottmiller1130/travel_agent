"""Tests for the weather forecast tool."""

import pytest
from unittest.mock import patch, MagicMock
from tools.weather import get_weather, _mock_forecast, _packing_for_temp

try:
    import httpx as _httpx_lib
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

needs_httpx = pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")


# ── _packing_for_temp ─────────────────────────────────────────────────────────

def test_packing_tropical():
    items = _packing_for_temp(30)
    assert any("Sunscreen" in i for i in items)


def test_packing_mediterranean():
    items = _packing_for_temp(22)
    assert len(items) > 0


def test_packing_cold():
    items = _packing_for_temp(-5)
    assert any("winter" in i.lower() or "coat" in i.lower() for i in items)


def test_packing_alpine():
    items = _packing_for_temp(6)
    assert any("coat" in i.lower() or "thermal" in i.lower() for i in items)


# ── _mock_forecast (climate fallback) ────────────────────────────────────────

def test_mock_forecast_returns_success():
    result = _mock_forecast("Paris", "2026-07-01", "2026-07-05")
    assert result["status"] == "success"


def test_mock_forecast_correct_day_count():
    result = _mock_forecast("London", "2026-08-01", "2026-08-07")
    assert len(result["daily_forecast"]) == 7


def test_mock_forecast_daily_keys():
    result = _mock_forecast("Bangkok", "2026-01-10", "2026-01-12")
    for day in result["daily_forecast"]:
        for key in ("date", "condition", "temp_high_c", "temp_low_c"):
            assert key in day, f"Missing key: {key}"


def test_mock_forecast_tropical_destination_warm():
    result = _mock_forecast("Bali", "2026-06-01", "2026-06-03")
    avg_high = sum(d["temp_high_c"] for d in result["daily_forecast"]) / len(result["daily_forecast"])
    assert avg_high > 20


def test_mock_forecast_northern_destination():
    result = _mock_forecast("London", "2026-01-01", "2026-01-03")
    assert result["status"] == "success"


def test_mock_forecast_capped_at_14_days():
    result = _mock_forecast("Tokyo", "2026-05-01", "2026-06-01")
    assert len(result["daily_forecast"]) <= 14


def test_mock_forecast_deterministic():
    r1 = _mock_forecast("Paris", "2026-07-01", "2026-07-03")
    r2 = _mock_forecast("Paris", "2026-07-01", "2026-07-03")
    assert r1["daily_forecast"] == r2["daily_forecast"]


def test_mock_forecast_includes_packing():
    result = _mock_forecast("Dubai", "2026-07-01", "2026-07-03")
    assert "packing_suggestions" in result
    assert len(result["packing_suggestions"]) > 0


# ── get_weather — fallback path (no httpx required) ──────────────────────────

def test_get_weather_future_date_uses_climate_estimate():
    """Dates beyond the 16-day forecast window always use climate estimates."""
    result = get_weather("Paris", "2030-07-01", "2030-07-05")
    assert result["status"] == "success"
    assert len(result["daily_forecast"]) > 0


@needs_httpx
def test_get_weather_invalid_date_returns_error():
    """Invalid date format returns error only when httpx is available (live path)."""
    result = get_weather("Paris", "not-a-date", "also-wrong")
    assert result["status"] == "error"


def test_get_weather_no_httpx_uses_fallback():
    with patch("tools.weather._HTTPX", False):
        result = get_weather("Rome", "2026-07-01", "2026-07-03")
    assert result["status"] == "success"
    assert len(result["daily_forecast"]) > 0


# ── get_weather — live path (requires httpx) ──────────────────────────────────


class _FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data


def _fake_geocode_response():
    return _FakeResponse({"results": [{
        "latitude": 48.8566, "longitude": 2.3522,
        "name": "Paris", "country": "France", "timezone": "Europe/Paris",
    }]})


def _fake_forecast_response():
    return _FakeResponse({
        "daily": {
            "time": ["2026-07-01", "2026-07-02", "2026-07-03"],
            "temperature_2m_max": [28.0, 26.5, 25.0],
            "temperature_2m_min": [18.0, 17.0, 16.5],
            "precipitation_sum": [0.0, 2.1, 0.0],
            "weathercode": [0, 61, 1],
            "precipitation_probability_max": [10, 70, 5],
            "windspeed_10m_max": [15, 20, 12],
            "uv_index_max": [6, 4, 7],
        }
    })


@needs_httpx
@patch("tools.weather._httpx")
def test_get_weather_live_success(mock_httpx):
    mock_httpx.get.side_effect = [
        _fake_geocode_response(),
        _fake_forecast_response(),
    ]
    result = get_weather("Paris", "2026-07-01", "2026-07-03")
    assert result["status"] == "success"
    assert len(result["daily_forecast"]) == 3


@needs_httpx
@patch("tools.weather._httpx")
def test_get_weather_daily_has_required_keys(mock_httpx):
    mock_httpx.get.side_effect = [
        _fake_geocode_response(),
        _fake_forecast_response(),
    ]
    result = get_weather("Paris", "2026-07-01", "2026-07-03")
    for day in result["daily_forecast"]:
        for key in ("date", "temp_high_c", "temp_low_c", "condition"):
            assert key in day, f"Missing key: {key}"


@needs_httpx
@patch("tools.weather._httpx")
def test_get_weather_includes_packing_list(mock_httpx):
    mock_httpx.get.side_effect = [
        _fake_geocode_response(),
        _fake_forecast_response(),
    ]
    result = get_weather("Paris", "2026-07-01", "2026-07-03")
    assert "packing_suggestions" in result
    assert isinstance(result["packing_suggestions"], list)


@needs_httpx
@patch("tools.weather._httpx")
def test_get_weather_geocode_failure_uses_fallback(mock_httpx):
    mock_httpx.get.return_value = _FakeResponse({"results": []})
    result = get_weather("Paris", "2026-07-01", "2026-07-03")
    assert result["status"] == "success"
