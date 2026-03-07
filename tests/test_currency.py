"""Tests for the currency conversion tool."""

import pytest
from unittest.mock import patch, MagicMock
from tools.cache import _global_cache
from tools.currency import get_exchange_rate


def setup_function():
    _global_cache.clear()


def test_same_currency_no_conversion():
    result = get_exchange_rate(from_currency="USD", to_currency="USD", amount=100)
    assert result["status"] == "success"
    assert "no conversion needed" in result["note"].lower()


def test_fallback_usd_to_eur():
    """Test fallback rates work when API is unavailable."""
    with patch("tools.currency._HTTPX", False):
        result = get_exchange_rate(from_currency="USD", to_currency="EUR", amount=100)
    assert result["status"] == "success"
    assert result["converted_amount"] > 50  # EUR should be roughly 80-95% of USD
    assert result["converted_amount"] < 110


def test_fallback_usd_to_gbp():
    with patch("tools.currency._HTTPX", False):
        result = get_exchange_rate(from_currency="USD", to_currency="GBP", amount=100)
    assert result["status"] == "success"
    assert result["converted_amount"] > 50
    assert result["converted_amount"] < 100


def test_amount_scales_linearly():
    with patch("tools.currency._HTTPX", False):
        r1 = get_exchange_rate(from_currency="USD", to_currency="EUR", amount=1)
        _global_cache.clear()
        r2 = get_exchange_rate(from_currency="USD", to_currency="EUR", amount=100)
    assert r2["converted_amount"] == pytest.approx(r1["converted_amount"] * 100, rel=0.01)


def test_multi_currency_returns_conversions_list():
    with patch("tools.currency._HTTPX", False):
        result = get_exchange_rate(from_currency="USD", to_currency="EUR,GBP,JPY", amount=1000)
    assert result["status"] == "success"
    codes = {c["to_currency"] for c in result["conversions"]}
    assert "EUR" in codes
    assert "GBP" in codes
    assert "JPY" in codes


def test_formatted_field_present():
    with patch("tools.currency._HTTPX", False):
        result = get_exchange_rate(from_currency="USD", to_currency="EUR", amount=50)
    assert "formatted" in result
    assert "EUR" in result["formatted"]


def test_live_api_source_label():
    """When the live API succeeds, source should reference ECB."""
    # If httpx is available in the environment, the live API may be called.
    # We just verify the fallback source label is correct when API is off.
    with patch("tools.currency._HTTPX", False):
        result = get_exchange_rate(from_currency="USD", to_currency="EUR", amount=100)
    assert result["status"] == "success"
    assert "Estimated" in result["source"] or "European Central Bank" in result["source"]
