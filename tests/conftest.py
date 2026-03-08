"""Shared pytest fixtures for the travel agent test suite."""

import pytest
from tools.cache import _global_cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the TTL cache before every test to prevent cross-test pollution."""
    _global_cache.clear()
    yield
    _global_cache.clear()
