"""
Trip history store — saves completed and planned trips to SQLite.
Allows the agent to reference past trips and learn from patterns.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path.home() / ".travel_agent" / "trips.db"


class TripStore:
    """Persistent store for planned and completed trips."""

    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._init_db()

    def _init_db(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trips (
                id TEXT PRIMARY KEY,
                destination TEXT NOT NULL,
                start_date TEXT,
                end_date TEXT,
                status TEXT NOT NULL DEFAULT 'planned',
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def save_trip(self, trip: dict) -> str:
        """Save or update a trip. Returns the trip ID."""
        trip_id = trip.get("id") or f"TRIP{int(datetime.now().timestamp())}"
        trip["id"] = trip_id
        now = datetime.now().isoformat()

        self._conn.execute("""
            INSERT OR REPLACE INTO trips
                (id, destination, start_date, end_date, status, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, COALESCE(
                (SELECT created_at FROM trips WHERE id = ?), ?
            ), ?)
        """, (
            trip_id,
            trip.get("destination", ""),
            trip.get("start_date"),
            trip.get("end_date"),
            trip.get("status", "planned"),
            json.dumps(trip),
            trip_id, now,  # for COALESCE fallback
            now,
        ))
        self._conn.commit()
        return trip_id

    def get_trip(self, trip_id: str) -> dict | None:
        """Load a trip by ID."""
        row = self._conn.execute(
            "SELECT data FROM trips WHERE id = ?", (trip_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def get_all_trips(self, status: str | None = None) -> list[dict]:
        """Return all trips, optionally filtered by status."""
        if status:
            rows = self._conn.execute(
                "SELECT data FROM trips WHERE status = ? ORDER BY start_date DESC", (status,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT data FROM trips ORDER BY start_date DESC"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_recent_destinations(self, limit: int = 5) -> list[str]:
        """Return recently visited destinations."""
        rows = self._conn.execute(
            "SELECT DISTINCT destination FROM trips WHERE status = 'completed' ORDER BY end_date DESC LIMIT ?",
            (limit,)
        ).fetchall()
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

    def close(self):
        self._conn.close()
