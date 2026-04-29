"""
Critical user journey tests.

Exercises the full HTTP stack (FastAPI TestClient → memory layer) for each
journey a real user would take.  These tests intentionally cross module
boundaries — if one fails it means a user-facing flow is broken.

Journeys covered:
  J1  Trip lifecycle      — save, list (with timestamps), delete
  J2  Share link          — create, render, live-sync with itinerary updates
  J3  Booking signals     — confirm / cancel endpoints are reachable
  J4  Preferences         — set multiple prefs, read back, reject invalid keys
  J5  Session management  — create session, clear board, reset conversation
"""

import os

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-ci")
os.environ.setdefault("DATABASE_URL", "")

from fastapi.testclient import TestClient  # noqa: E402

from memory.sessions import SessionStore  # noqa: E402
from server import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=False)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_ITINERARY = {
    "destination": "Playa Ocotal, Costa Rica",
    "start_date": "2026-05-23",
    "end_date": "2026-05-30",
    "status": "planned",
    "travelers": 4,
    "budget": {"flights": 1200, "hotels": 800, "activities": 500},
    "days": [
        {
            "date": "2026-05-23",
            "label": "Arrival Day",
            "items": [
                {
                    "id": "item-scuba",
                    "type": "activity",
                    "title": "Scuba Diving",
                    "status": "suggested",
                    "price_usd": 120,
                }
            ],
        }
    ],
}


def _create_session(session_id: str) -> str:
    """Directly create a session via the store so API endpoints accept it."""
    SessionStore().create(session_id)
    return session_id


def _seed_itinerary(session_id: str, itinerary: dict | None = None) -> None:
    client.post(
        f"/api/itinerary/{session_id}",
        json={"itinerary": itinerary or SAMPLE_ITINERARY},
    )


# ---------------------------------------------------------------------------
# J1 — Trip lifecycle
# ---------------------------------------------------------------------------

def test_j1_save_trip_appears_in_list():
    sid = _create_session("j1-save-list-001")
    _seed_itinerary(sid)

    r = client.post(f"/api/trips/{sid}", json={"name": "Costa Rica Family"})
    assert r.status_code == 200, r.text
    trip_id = r.json()["trip_id"]

    trips = client.get(f"/api/trips/{sid}").json()["trips"]
    assert any(t["id"] == trip_id for t in trips)


def test_j1_saved_trip_carries_name():
    sid = _create_session("j1-name-001")
    _seed_itinerary(sid)

    client.post(f"/api/trips/{sid}", json={"name": "My Named Trip"})
    trips = client.get(f"/api/trips/{sid}").json()["trips"]
    names = [t.get("name") for t in trips]
    assert "My Named Trip" in names


def test_j1_saved_trip_has_updated_at():
    sid = _create_session("j1-updated-at-001")
    _seed_itinerary(sid)
    client.post(f"/api/trips/{sid}", json={"name": "Timestamp Check"})

    trips = client.get(f"/api/trips/{sid}").json()["trips"]
    assert len(trips) >= 1
    assert trips[0].get("updated_at"), "updated_at should be non-empty"


def test_j1_delete_trip_removes_it():
    sid = _create_session("j1-delete-001")
    _seed_itinerary(sid)

    r = client.post(f"/api/trips/{sid}", json={"name": "To Delete"})
    trip_id = r.json()["trip_id"]

    del_r = client.delete(f"/api/trips/{sid}/{trip_id}")
    assert del_r.status_code == 200

    trips = client.get(f"/api/trips/{sid}").json()["trips"]
    assert not any(t["id"] == trip_id for t in trips)


def test_j1_save_trip_without_itinerary_returns_404():
    sid = _create_session("j1-no-itin-001")
    r = client.post(f"/api/trips/{sid}", json={"name": "Should Fail"})
    assert r.status_code == 404


def test_j1_save_trip_on_unknown_session_returns_403():
    r = client.post("/api/trips/session-does-not-exist-xyz", json={"name": "Should Fail"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# J2 — Share link lifecycle
# ---------------------------------------------------------------------------

def test_j2_share_link_created():
    sid = _create_session("j2-create-001")
    _seed_itinerary(sid)

    r = client.post(f"/api/share/{sid}")
    assert r.status_code == 200
    data = r.json()
    assert "share_url" in data
    assert "/s/" in data["share_url"]


def test_j2_shared_link_renders_html():
    sid = _create_session("j2-html-001")
    _seed_itinerary(sid)

    share_r = client.post(f"/api/share/{sid}")
    token = share_r.json()["share_url"].split("/s/")[-1]

    view_r = client.get(f"/s/{token}")
    assert view_r.status_code == 200
    assert "text/html" in view_r.headers.get("content-type", "")


def test_j2_shared_link_shows_destination():
    sid = _create_session("j2-content-001")
    _seed_itinerary(sid)

    token = client.post(f"/api/share/{sid}").json()["share_url"].split("/s/")[-1]
    html = client.get(f"/s/{token}").text
    assert "Costa Rica" in html


def test_j2_shared_link_reflects_live_itinerary_update():
    """Updating the itinerary after link creation should show in the share view."""
    sid = _create_session("j2-live-001")
    _seed_itinerary(sid)
    token = client.post(f"/api/share/{sid}").json()["share_url"].split("/s/")[-1]

    # Update item status from suggested → confirmed
    updated = {
        **SAMPLE_ITINERARY,
        "days": [
            {
                "date": "2026-05-23",
                "label": "Arrival Day",
                "items": [
                    {
                        "id": "item-scuba",
                        "type": "activity",
                        "title": "Scuba Diving",
                        "status": "confirmed",
                        "price_usd": 120,
                    }
                ],
            }
        ],
    }
    client.post(f"/api/itinerary/{sid}", json={"itinerary": updated})

    html = client.get(f"/s/{token}").text
    assert "confirmed" in html.lower()


def test_j2_invalid_share_token_returns_404():
    r = client.get("/s/totally-invalid-token-xyz-999")
    assert r.status_code == 404


def test_j2_share_without_itinerary_returns_404():
    sid = _create_session("j2-empty-001")
    r = client.post(f"/api/share/{sid}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# J3 — Booking signals
# ---------------------------------------------------------------------------

def test_j3_confirm_endpoint_reachable():
    """Endpoint must return 200 (no pending confirmation is a warning, not a crash)."""
    r = client.post("/api/booking/confirm/any-session")
    assert r.status_code == 200


def test_j3_cancel_endpoint_reachable():
    r = client.post("/api/booking/cancel/any-session")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# J4 — Preferences
# ---------------------------------------------------------------------------

def test_j4_preferences_persist_across_requests():
    sid = "j4-prefs-001"
    client.post(f"/api/preferences/{sid}", json={"key": "home_airport", "value": "SFO"})
    client.post(f"/api/preferences/{sid}", json={"key": "cabin_class", "value": "business"})

    prefs = client.get(f"/api/preferences/{sid}").json()
    assert prefs.get("home_airport") == "SFO"
    assert prefs.get("cabin_class") == "business"


def test_j4_invalid_preference_key_rejected():
    r = client.post("/api/preferences/j4-invalid", json={"key": "malicious__key", "value": "bad"})
    assert r.status_code in (400, 422)


def test_j4_get_preferences_empty_session_returns_dict():
    r = client.get("/api/preferences/j4-empty-session-xyz")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


# ---------------------------------------------------------------------------
# J5 — Session management
# ---------------------------------------------------------------------------

def test_j5_new_session_returns_session_id():
    r = client.post("/api/session/new")
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data
    assert data["session_id"]


def test_j5_clear_itinerary():
    sid = _create_session("j5-clear-001")
    _seed_itinerary(sid)

    r = client.delete(f"/api/itinerary/{sid}")
    assert r.status_code == 200

    itin_r = client.get(f"/api/itinerary/{sid}")
    assert itin_r.json().get("itinerary") is None


def test_j5_reset_conversation_returns_ok():
    sid = _create_session("j5-reset-001")
    r = client.post(f"/api/reset/{sid}")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"
