"""Tests for SessionStore — persistence layer for sessions and share tokens."""

import pytest

from memory.sessions import SessionStore

SAMPLE_ITINERARY = {
    "destination": "Bali, Indonesia",
    "start_date": "2026-08-01",
    "end_date": "2026-08-10",
    "status": "planned",
    "days": [],
}


@pytest.fixture
def store():
    return SessionStore()


# ── Session lifecycle ────────────────────────────────────────────────────────

def test_create_and_exists(store):
    store.create("sess-exists-001")
    assert store.exists("sess-exists-001")


def test_nonexistent_session_returns_false(store):
    assert not store.exists("sess-does-not-exist-xyz-999")


def test_save_and_load_round_trip(store):
    sid = "sess-save-load-001"
    store.create(sid)
    conversation = [{"role": "user", "content": "Plan a trip to Paris"}]
    store.save(sid, conversation, SAMPLE_ITINERARY)

    data = store.load(sid)
    assert data is not None
    assert data["conversation"] == conversation
    assert data["itinerary"]["destination"] == "Bali, Indonesia"


def test_load_nonexistent_session_returns_none(store):
    assert store.load("sess-no-exist-xyz-999") is None


def test_save_itinerary_only(store):
    sid = "sess-itin-only-001"
    store.create(sid)
    store.save_itinerary(sid, {**SAMPLE_ITINERARY, "destination": "Tokyo"})

    data = store.load(sid)
    assert data["itinerary"]["destination"] == "Tokyo"


def test_clear_itinerary(store):
    sid = "sess-clear-itin-001"
    store.create(sid)
    store.save_itinerary(sid, SAMPLE_ITINERARY)
    store.clear_itinerary(sid)

    data = store.load(sid)
    assert data["itinerary"] is None


def test_delete_session(store):
    sid = "sess-delete-001"
    store.create(sid)
    assert store.exists(sid)
    store.delete(sid)
    assert not store.exists(sid)


# ── Share tokens ─────────────────────────────────────────────────────────────

def test_create_share_token_returns_token(store):
    sid = "sess-share-create-001"
    store.create(sid)
    token = store.create_share_token(sid, SAMPLE_ITINERARY)
    assert token and len(token) >= 10


def test_invalid_token_returns_none(store):
    assert store.get_session_for_token("totally-invalid-token-xyz-999") is None


def test_share_token_returns_live_itinerary(store):
    """After updating the session itinerary, the share link should reflect the change."""
    sid = "sess-share-live-001"
    store.create(sid)

    snapshot = {**SAMPLE_ITINERARY, "days": [{"items": [{"title": "Scuba", "status": "suggested"}]}]}
    token = store.create_share_token(sid, snapshot)

    # Simulate a booking confirmation updating the live session
    updated = {**SAMPLE_ITINERARY, "days": [{"items": [{"title": "Scuba", "status": "confirmed"}]}]}
    store.save_itinerary(sid, updated)

    result = store.get_session_for_token(token)
    assert result is not None
    assert result["itinerary"]["days"][0]["items"][0]["status"] == "confirmed"


def test_share_token_falls_back_to_snapshot_when_session_deleted(store):
    """If the session is deleted the share link should still work via the stored snapshot."""
    sid = "sess-share-fallback-001"
    store.create(sid)
    snapshot = {**SAMPLE_ITINERARY, "destination": "Fallback City"}
    token = store.create_share_token(sid, snapshot)

    store.delete(sid)

    result = store.get_session_for_token(token)
    assert result is not None
    assert result["itinerary"]["destination"] == "Fallback City"
