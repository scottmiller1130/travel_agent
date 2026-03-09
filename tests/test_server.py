"""Smoke tests for the FastAPI server endpoints."""

import os

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

# Set required env vars before importing the app
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-ci")
os.environ.setdefault("DATABASE_URL", "")

from fastapi.testclient import TestClient

from server import app

client = TestClient(app, raise_server_exceptions=False)


# ── Static / UI ───────────────────────────────────────────────────────────────

def test_root_returns_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


# ── Health / reachability ─────────────────────────────────────────────────────

def test_unknown_route_returns_404():
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404


# ── Preferences ───────────────────────────────────────────────────────────────

def test_get_preferences_returns_json():
    response = client.get("/api/preferences/test-session-ci")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)


def test_set_preference_and_read_back():
    session = "ci-pref-test"
    client.post(f"/api/preferences/{session}",
                json={"key": "traveler_profile", "value": "adventure"})
    response = client.get(f"/api/preferences/{session}")
    assert response.status_code == 200
    prefs = response.json()
    assert prefs.get("traveler_profile") == "adventure"


# ── Trips ─────────────────────────────────────────────────────────────────────

def test_get_trips_returns_list():
    response = client.get("/api/trips/ci-session-no-trips")
    assert response.status_code == 200
    data = response.json()
    assert "trips" in data
    assert isinstance(data["trips"], list)


# ── Reset ─────────────────────────────────────────────────────────────────────

def test_reset_session_returns_ok():
    response = client.post("/api/reset/ci-reset-test")
    assert response.status_code == 200


# ── Share ─────────────────────────────────────────────────────────────────────

def test_share_requires_body():
    response = client.post("/api/share", json={})
    # Either 422 (validation) or 200 with error — not a server crash (500)
    assert response.status_code != 500


# ── Itinerary ────────────────────────────────────────────────────────────────

def test_get_itinerary_empty_session():
    response = client.get("/api/itinerary/ci-empty-session")
    assert response.status_code in (200, 404)
