"""Tests for TripStore — persistence layer for saved trips."""

import pytest

from memory.trips import TripStore

SAMPLE_TRIP = {
    "destination": "Paris, France",
    "start_date": "2026-06-01",
    "end_date": "2026-06-07",
    "status": "planned",
    "budget": {"flights": 800, "hotels": 600},
}


@pytest.fixture
def store():
    return TripStore()


# ── Save & retrieve ──────────────────────────────────────────────────────────

def test_save_generates_id(store):
    trip_id = store.save_trip({**SAMPLE_TRIP})
    assert trip_id.startswith("TRIP")


def test_save_and_get_round_trip(store):
    trip = {**SAMPLE_TRIP, "destination": "Rome, Italy"}
    trip_id = store.save_trip(trip)
    fetched = store.get_trip(trip_id)
    assert fetched is not None
    assert fetched["destination"] == "Rome, Italy"


def test_save_with_explicit_id(store):
    trip = {**SAMPLE_TRIP, "id": "TRIP_EXPLICIT_001", "destination": "Berlin"}
    trip_id = store.save_trip(trip)
    assert trip_id == "TRIP_EXPLICIT_001"
    assert store.get_trip("TRIP_EXPLICIT_001") is not None


def test_upsert_updates_existing_trip(store):
    trip_id = "TRIP_UPSERT_001"
    store.save_trip({**SAMPLE_TRIP, "id": trip_id, "destination": "Before"})
    store.save_trip({**SAMPLE_TRIP, "id": trip_id, "destination": "After"})
    assert store.get_trip(trip_id)["destination"] == "After"


def test_get_nonexistent_trip_returns_none(store):
    assert store.get_trip("TRIP_DOES_NOT_EXIST_XYZ") is None


# ── Timestamps ───────────────────────────────────────────────────────────────

def test_get_all_trips_includes_db_timestamps(store):
    store.save_trip({**SAMPLE_TRIP, "destination": "Timestamp Test"}, user_id="user-ts-001")
    trips = store.get_all_trips(user_id="user-ts-001")
    assert len(trips) >= 1
    assert "updated_at" in trips[0]
    assert "created_at" in trips[0]
    assert trips[0]["updated_at"]  # non-empty


def test_get_trips_for_users_includes_timestamps(store):
    store.save_trip({**SAMPLE_TRIP, "destination": "Group Trip A"}, user_id="group-member-a")
    trips = store.get_trips_for_users(["group-member-a"])
    assert len(trips) >= 1
    assert "updated_at" in trips[0]
    assert "_member_user_id" in trips[0]


# ── Filtering & sorting ──────────────────────────────────────────────────────

def test_get_all_trips_user_isolation(store):
    # Use explicit IDs to avoid same-second timestamp collisions
    store.save_trip({**SAMPLE_TRIP, "id": "TRIP_ISO_U1", "destination": "User1Only"}, user_id="iso-user-1")
    store.save_trip({**SAMPLE_TRIP, "id": "TRIP_ISO_U2", "destination": "User2Only"}, user_id="iso-user-2")

    user1_destinations = [t["destination"] for t in store.get_all_trips(user_id="iso-user-1")]
    assert "User1Only" in user1_destinations
    assert "User2Only" not in user1_destinations


def test_get_all_trips_status_filter(store):
    uid = "filter-status-user"
    store.save_trip({**SAMPLE_TRIP, "id": "TRIP_FILTER_P", "destination": "Planned", "status": "planned"}, user_id=uid)
    store.save_trip({**SAMPLE_TRIP, "id": "TRIP_FILTER_C", "destination": "Completed", "status": "completed"}, user_id=uid)

    planned = store.get_all_trips(status="planned", user_id=uid)
    assert all(t["status"] == "planned" for t in planned)
    assert any(t["destination"] == "Planned" for t in planned)


def test_get_trips_for_users_empty_list(store):
    assert store.get_trips_for_users([]) == []


# ── Delete ───────────────────────────────────────────────────────────────────

def test_delete_trip(store):
    trip_id = store.save_trip({**SAMPLE_TRIP, "destination": "To Delete"})
    assert store.get_trip(trip_id) is not None
    store.delete_trip(trip_id)
    assert store.get_trip(trip_id) is None


# ── Context string ───────────────────────────────────────────────────────────

def test_as_context_string_no_trips(store):
    result = store.as_context_string(user_id="user-with-no-trips-xyz")
    assert "No previous trips" in result


def test_as_context_string_with_trips(store):
    uid = "ctx-user-001"
    store.save_trip({**SAMPLE_TRIP, "destination": "Tokyo, Japan"}, user_id=uid)
    result = store.as_context_string(user_id=uid)
    assert "Tokyo" in result
