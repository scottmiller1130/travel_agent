# Critical User Journeys

This document defines the core flows that must work correctly at all times.
Every journey has a corresponding test in `tests/test_critical_journeys.py`
and a memory-layer test in `tests/test_memory_trips.py` or
`tests/test_memory_sessions.py`.

---

## J1 — Trip Lifecycle

**What it covers:** A user saves the current itinerary as a named trip, sees it in their saved-trips sidebar, and can delete it.

**Steps:**
1. Session exists and has a loaded itinerary.
2. User clicks **Export / Import → Save Trip** and gives it a name.
3. The trip appears in the **Saved Trips** sidebar with a name, date range, status badge, and **Modified X ago** timestamp.
4. Trips are sorted by most-recently-modified first.
5. User can click a saved trip to load it onto the board.
6. User can delete a saved trip via the trash icon.

**Invariants:**
- `updated_at` is always present and non-empty on every returned trip.
- Deleting a trip removes it from the list immediately.
- Saving a trip on an unknown/non-existent session returns `403`.
- Saving a trip when there is no active itinerary returns `404`.

**Tests:** `test_j1_*` in `tests/test_critical_journeys.py`

---

## J2 — Share Link

**What it covers:** A user shares a read-only link to their itinerary. Recipients see the current state of the itinerary at all times — including any booking confirmations made after the link was created.

**Steps:**
1. User has an active itinerary in their session.
2. User clicks **Share** — a unique URL (`/s/<token>`) is generated and copied.
3. Anyone with the link opens it in a browser and sees a rich HTML view.
4. If the user later updates the itinerary (e.g., confirms a booking), the shared link automatically reflects the change — **no new link needed**.
5. If the session is reset/deleted, the link falls back to the itinerary snapshot taken at link creation time rather than breaking.

**Invariants:**
- Share link URL never changes after creation.
- The rendered HTML always contains the destination name.
- An invalid/expired token returns `404` HTML (not a 500 error).
- Sharing when no itinerary exists returns `404`.

**Tests:** `test_j2_*` in `tests/test_critical_journeys.py`, `test_share_token_*` in `tests/test_memory_sessions.py`

---

## J3 — Booking Confirmation

**What it covers:** The agent presents a booking for user confirmation. The user confirms or cancels via the UI.

**Steps:**
1. Agent plans a trip and a tool call returns `awaiting_confirmation: true`.
2. A confirmation prompt appears in the UI.
3. User clicks **Confirm** → `POST /api/booking/confirm/{session_id}` → agent proceeds.
4. User clicks **Cancel** → `POST /api/booking/cancel/{session_id}` → agent skips the booking.
5. After confirmation, the item status in the itinerary changes to `confirmed`.

**Invariants:**
- Both endpoints always return `200` (no pending confirmation is a logged warning, not an error).
- The confirmed status is visible in the shared link without regenerating it (covered by J2).

**Note:** Full end-to-end booking flow requires a live agent session. Unit tests verify the endpoints are reachable; agent integration is tested manually.

**Tests:** `test_j3_*` in `tests/test_critical_journeys.py`

---

## J4 — User Preferences

**What it covers:** A user sets travel preferences (home airport, cabin class, dietary restrictions, etc.) that the agent incorporates into all future planning in that session.

**Steps:**
1. User opens **Preferences** and sets one or more values.
2. Preferences are persisted immediately.
3. On the next planning request the agent's system prompt includes the updated preferences.
4. Preferences are scoped per authenticated user (or per session for anonymous users).

**Invariants:**
- Multiple preferences can be set independently and all are read back correctly.
- Invalid / unknown preference keys are rejected with `400` or `422`.
- An empty preferences response is a `{}` dict, not an error.

**Tests:** `test_j4_*` in `tests/test_critical_journeys.py`

---

## J5 — Session Management

**What it covers:** Creating a new session, clearing the trip board, and resetting the conversation.

**Steps — New Session:**
1. User opens the app for the first time (or clicks **New Trip**).
2. `POST /api/session/new` returns a `session_id`.
3. All subsequent requests use that session ID.

**Steps — Clear Board:**
1. User clicks **Clear Board** to remove the current itinerary without resetting the conversation.
2. The board shows the empty-state placeholder.
3. Saved trips are unaffected.

**Steps — Reset Conversation:**
1. User clicks **Reset Conversation**.
2. Chat history is cleared; trips and preferences are preserved.

**Invariants:**
- `POST /api/session/new` always returns a non-empty `session_id`.
- Clearing the itinerary returns the board to its empty state.
- Reset returns `{ "status": "ok" }`.

**Tests:** `test_j5_*` in `tests/test_critical_journeys.py`

---

## Regression Checklist for New PRs

When a PR touches any of the files below, re-run the full test suite locally
and confirm all critical journey tests still pass:

| File changed | Journeys at risk |
|---|---|
| `memory/trips.py` | J1 |
| `memory/sessions.py` | J2, J5 |
| `server.py` (trips endpoints) | J1 |
| `server.py` (share endpoints) | J2 |
| `server.py` (booking endpoints) | J3 |
| `server.py` (preferences endpoints) | J4 |
| `server.py` (session endpoints) | J5 |
| `static/index.html` | J1 (trip card rendering), J2 (share modal), J3 (confirm UI) |
| `agent/core.py` | J3 (booking confirmation flow) |

---

## Running the Tests Locally

```bash
# All tests
pytest

# Critical journeys only
pytest tests/test_critical_journeys.py -v

# Memory layer only
pytest tests/test_memory_trips.py tests/test_memory_sessions.py -v

# With coverage report
pytest --cov=. --cov-report=term-missing
```
