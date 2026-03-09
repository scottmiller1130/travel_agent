"""
Trip history store — saves completed and planned trips to PostgreSQL.
Allows the agent to reference past trips and learn from patterns.

Supports per-user isolation: pass user_id to scope all queries.
"""

import json
from datetime import datetime

from memory.db import get_conn


class TripStore:
    """Persistent store for planned and completed trips."""

    def __init__(self):
        self._ready = False
        try:
            self._init_db()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                "DB unavailable at startup — will retry on first request. Error: %s", exc
            )

    def _ensure_db(self):
        if not self._ready:
            self._init_db()

    def _init_db(self):
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trips (
                    id          TEXT PRIMARY KEY,
                    destination TEXT NOT NULL,
                    start_date  TEXT,
                    end_date    TEXT,
                    status      TEXT NOT NULL DEFAULT 'planned',
                    data        TEXT NOT NULL,
                    user_id     TEXT,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)
            # Add user_id column to existing deployments that don't have it yet
            cur.execute("""
                ALTER TABLE trips ADD COLUMN IF NOT EXISTS user_id TEXT
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_trips_user_id ON trips(user_id)"
            )
            self._ready = True

    def save_trip(self, trip: dict, user_id: str | None = None) -> str:
        self._ensure_db()
        """Save or update a trip. Returns the trip ID."""
        trip_id = trip.get("id") or f"TRIP{int(datetime.now().timestamp())}"
        trip["id"] = trip_id
        now = datetime.now().isoformat()

        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO trips
                    (id, destination, start_date, end_date, status, data, user_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    destination = EXCLUDED.destination,
                    start_date  = EXCLUDED.start_date,
                    end_date    = EXCLUDED.end_date,
                    status      = EXCLUDED.status,
                    data        = EXCLUDED.data,
                    user_id     = COALESCE(trips.user_id, EXCLUDED.user_id),
                    created_at  = COALESCE(trips.created_at, EXCLUDED.created_at),
                    updated_at  = EXCLUDED.updated_at
            """, (
                trip_id,
                trip.get("destination", ""),
                trip.get("start_date"),
                trip.get("end_date"),
                trip.get("status", "planned"),
                json.dumps(trip),
                user_id,
                now,
                now,
            ))
        return trip_id

    def get_trip(self, trip_id: str, user_id: str | None = None) -> dict | None:
        self._ensure_db()
        """Load a trip by ID. If user_id provided, only returns trip owned by that user."""
        with get_conn() as conn:
            cur = conn.cursor()
            if user_id:
                cur.execute(
                    "SELECT data FROM trips WHERE id = %s AND (user_id = %s OR user_id IS NULL)",
                    (trip_id, user_id),
                )
            else:
                cur.execute("SELECT data FROM trips WHERE id = %s", (trip_id,))
            row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def get_all_trips(self, status: str | None = None, user_id: str | None = None) -> list[dict]:
        self._ensure_db()
        """Return all trips for a user, optionally filtered by status."""
        with get_conn() as conn:
            cur = conn.cursor()
            if user_id and status:
                cur.execute(
                    "SELECT data FROM trips WHERE user_id = %s AND status = %s ORDER BY updated_at DESC",
                    (user_id, status),
                )
            elif user_id:
                cur.execute(
                    "SELECT data FROM trips WHERE user_id = %s ORDER BY updated_at DESC",
                    (user_id,),
                )
            elif status:
                cur.execute(
                    "SELECT data FROM trips WHERE user_id IS NULL AND status = %s ORDER BY updated_at DESC",
                    (status,),
                )
            else:
                cur.execute(
                    "SELECT data FROM trips WHERE user_id IS NULL ORDER BY updated_at DESC"
                )
            rows = cur.fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_recent_destinations(self, limit: int = 5, user_id: str | None = None) -> list[str]:
        self._ensure_db()
        """Return recently visited destinations for a user."""
        with get_conn() as conn:
            cur = conn.cursor()
            if user_id:
                cur.execute(
                    """
                    SELECT DISTINCT destination FROM trips
                    WHERE status = 'completed' AND user_id = %s
                    ORDER BY end_date DESC LIMIT %s
                    """,
                    (user_id, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT DISTINCT destination FROM trips
                    WHERE status = 'completed' AND user_id IS NULL
                    ORDER BY end_date DESC LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
        return [row[0] for row in rows]

    def as_context_string(self, user_id: str | None = None) -> str:
        """Format recent trips as context for the agent."""
        trips = self.get_all_trips(user_id=user_id)
        if not trips:
            return "No previous trips on record."
        lines = ["Recent Trips:"]
        for t in trips[:5]:
            dest   = t.get("destination", "Unknown")
            dates  = f"{t.get('start_date', '?')} → {t.get('end_date', '?')}"
            status = t.get("status", "planned")
            lines.append(f"  - {dest} ({dates}) [{status}]")
        return "\n".join(lines)

    def delete_trip(self, trip_id: str, user_id: str | None = None) -> None:
        self._ensure_db()
        """Delete a trip by ID. If user_id provided, only deletes trips owned by that user."""
        with get_conn() as conn:
            cur = conn.cursor()
            if user_id:
                cur.execute(
                    "DELETE FROM trips WHERE id = %s AND (user_id = %s OR user_id IS NULL)",
                    (trip_id, user_id),
                )
            else:
                cur.execute("DELETE FROM trips WHERE id = %s", (trip_id,))

    def get_all_admin(self) -> list[dict]:
        """Return all trips across all users (admin view, no user filter)."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, destination, start_date, end_date, status, user_id, created_at, updated_at "
                "FROM trips ORDER BY updated_at DESC"
            )
            rows = cur.fetchall()
        return [
            {
                "id":          r[0],
                "destination": r[1],
                "start_date":  r[2],
                "end_date":    r[3],
                "status":      r[4],
                "user_id":     r[5],
                "created_at":  r[6],
                "updated_at":  r[7],
            }
            for r in rows
        ]

    def admin_delete(self, trip_id: str) -> None:
        """Delete any trip regardless of owner (admin only)."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM trips WHERE id = %s", (trip_id,))

    def close(self):
        pass  # Connection pool is managed globally
