"""
Trip history store — saves completed and planned trips to Supabase (PostgreSQL).
Allows the agent to reference past trips and learn from patterns.
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
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)
            self._ready = True

    def save_trip(self, trip: dict) -> str:
        self._ensure_db()
        """Save or update a trip. Returns the trip ID."""
        trip_id = trip.get("id") or f"TRIP{int(datetime.now().timestamp())}"
        trip["id"] = trip_id
        now = datetime.now().isoformat()

        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO trips
                    (id, destination, start_date, end_date, status, data, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    destination = EXCLUDED.destination,
                    start_date  = EXCLUDED.start_date,
                    end_date    = EXCLUDED.end_date,
                    status      = EXCLUDED.status,
                    data        = EXCLUDED.data,
                    created_at  = COALESCE(trips.created_at, EXCLUDED.created_at),
                    updated_at  = EXCLUDED.updated_at
            """, (
                trip_id,
                trip.get("destination", ""),
                trip.get("start_date"),
                trip.get("end_date"),
                trip.get("status", "planned"),
                json.dumps(trip),
                now,
                now,
            ))
        return trip_id

    def get_trip(self, trip_id: str) -> dict | None:
        self._ensure_db()
        """Load a trip by ID."""
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT data FROM trips WHERE id = %s", (trip_id,))
            row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def get_all_trips(self, status: str | None = None) -> list[dict]:
        self._ensure_db()
        """Return all trips, optionally filtered by status."""
        with get_conn() as conn:
            cur = conn.cursor()
            if status:
                cur.execute(
                    "SELECT data FROM trips WHERE status = %s ORDER BY start_date DESC",
                    (status,),
                )
            else:
                cur.execute("SELECT data FROM trips ORDER BY start_date DESC")
            rows = cur.fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_recent_destinations(self, limit: int = 5) -> list[str]:
        self._ensure_db()
        """Return recently visited destinations."""
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT destination FROM trips
                WHERE status = 'completed'
                ORDER BY end_date DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        return [row[0] for row in rows]

    def as_context_string(self) -> str:
        """Format recent trips as context for the agent."""
        trips = self.get_all_trips()
        if not trips:
            return "No previous trips on record."
        lines = ["Recent Trips:"]
        for t in trips[:5]:
            dest = t.get("destination", "Unknown")
            dates = f"{t.get('start_date', '?')} → {t.get('end_date', '?')}"
            status = t.get("status", "planned")
            lines.append(f"  - {dest} ({dates}) [{status}]")
        return "\n".join(lines)

    def delete_trip(self, trip_id: str) -> None:
        self._ensure_db()
        """Delete a trip by ID."""
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM trips WHERE id = %s", (trip_id,))

    def close(self):
        pass  # Connection pool is managed globally
