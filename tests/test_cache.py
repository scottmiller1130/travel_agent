"""Tests for the TTL cache module."""

import time
import pytest
from tools.cache import _global_cache, ttl_cache


def setup_function():
    _global_cache.clear()


def test_cache_miss_on_first_call():
    hit, val = _global_cache.get("no_such_key")
    assert hit is False
    assert val is None


def test_cache_set_and_get():
    _global_cache.set("k", "v", ttl=60)
    hit, val = _global_cache.get("k")
    assert hit is True
    assert val == "v"


def test_cache_expires():
    _global_cache.set("expiring", "data", ttl=0.05)
    time.sleep(0.1)
    hit, val = _global_cache.get("expiring")
    assert hit is False
    assert val is None


def test_decorator_caches_result():
    calls = []

    @ttl_cache(ttl=60)
    def expensive(x):
        calls.append(x)
        return {"status": "success", "value": x * 2}

    result1 = expensive(5)
    result2 = expensive(5)
    assert result1 == result2
    assert len(calls) == 1  # called only once


def test_decorator_does_not_cache_errors():
    calls = []

    @ttl_cache(ttl=60)
    def flaky(x):
        calls.append(x)
        return {"status": "error", "message": "oops"}

    flaky(1)
    flaky(1)
    assert len(calls) == 2  # error result not cached


def test_decorator_different_args_not_shared():
    @ttl_cache(ttl=60)
    def fn(x):
        return {"status": "success", "v": x}

    assert fn(1)["v"] == 1
    assert fn(2)["v"] == 2


def test_cache_clear():
    _global_cache.set("a", 1, ttl=60)
    _global_cache.set("b", 2, ttl=60)
    _global_cache.clear()
    assert _global_cache.get("a") == (False, None)
    assert _global_cache.get("b") == (False, None)
