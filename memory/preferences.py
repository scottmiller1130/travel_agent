"""
User preference store — persists travel preferences to Supabase (PostgreSQL).
The agent reads these before planning so it always personalizes recommendations.
"""

import json
from datetime import datetime

from memory.db import get_conn


class PreferenceStore:
    """Persistent key-value store for user travel preferences."""

    # Default preferences — overridden by anything stored in DB
    DEFAULTS = {
        "preferred_airlines": [],
        "avoided_airlines": [],
        "seat_preference": "window",
        "cabin_class": "economy",
        "hotel_min_stars": 3,
        "max_budget_per_day_usd": 300,
        "dietary_restrictions": [],
        "accessibility_needs": [],
        "preferred_activities": ["culture", "food", "nature"],
        "avoided_activities": [],
        "travel_pace": "moderate",  # slow | moderate | fast
        "home_airport": "",
        "home_city": "",
        "currency": "USD",
        "name": "",
        "email": "",
    }

    def __init__(self):
        self._init_db()

    def _init_db(self):
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS preferences (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def get(self, key: str, default=None):
        """Get a preference value. Falls back to DEFAULTS then to `default`."""
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM preferences WHERE key = %s", (key,))
            row = cur.fetchone()
        if row:
            return json.loads(row[0])
        return self.DEFAULTS.get(key, default)

    def set(self, key: str, value) -> None:
        """Set a preference value."""
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO preferences (key, value, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET
                    value      = EXCLUDED.value,
                    updated_at = EXCLUDED.updated_at
                """,
                (key, json.dumps(value), datetime.now().isoformat()),
            )

    def set_many(self, updates: dict) -> None:
        """Set multiple preferences at once."""
        for key, value in updates.items():
            self.set(key, value)

    def get_all(self) -> dict:
        """Return all preferences merged with defaults."""
        prefs = dict(self.DEFAULTS)
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM preferences")
            rows = cur.fetchall()
        for key, value in rows:
            prefs[key] = json.loads(value)
        return prefs

    def as_context_string(self) -> str:
        """Format preferences as a readable string for injection into the system prompt."""
        prefs = self.get_all()
        lines = ["User Travel Preferences:"]
        for key, value in prefs.items():
            if value and value != [] and value != "":
                label = key.replace("_", " ").title()
                if isinstance(value, list):
                    value = ", ".join(str(v) for v in value) if value else "none"
                lines.append(f"  - {label}: {value}")
        return "\n".join(lines)

    def close(self):
        pass  # Connection pool is managed globally
