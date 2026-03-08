"""
User preference store — persists travel preferences to PostgreSQL.
The agent reads these before planning so it always personalizes recommendations.

Supports both anonymous (global) and per-user preferences.
When user_id is provided, preferences are isolated per-user.
Falls back to DEFAULTS for any key not yet set.
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
            # Global/anonymous preferences (original table)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS preferences (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # Per-user preferences — created by UserStore._init_db too, idempotent
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id    TEXT NOT NULL,
                    key        TEXT NOT NULL,
                    value      TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, key)
                )
            """)
            self._ready = True

    # ── Core get/set ──────────────────────────────────────────────────────────

    def get(self, key: str, default=None, user_id: str | None = None):
        """Get a preference value.
        Priority: user_preferences (if user_id) → DEFAULTS → `default`.
        """
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            if user_id:
                cur.execute(
                    "SELECT value FROM user_preferences WHERE user_id = %s AND key = %s",
                    (user_id, key),
                )
            else:
                cur.execute("SELECT value FROM preferences WHERE key = %s", (key,))
            row = cur.fetchone()
        if row:
            return json.loads(row[0])
        return self.DEFAULTS.get(key, default)

    def set(self, key: str, value, user_id: str | None = None) -> None:
        """Set a preference value (per-user if user_id provided, else global)."""
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            if user_id:
                cur.execute(
                    """
                    INSERT INTO user_preferences (user_id, key, value, updated_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, key) DO UPDATE SET
                        value      = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (user_id, key, json.dumps(value), now),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO preferences (key, value, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (key) DO UPDATE SET
                        value      = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (key, json.dumps(value), now),
                )

    def set_many(self, updates: dict, user_id: str | None = None) -> None:
        """Set multiple preferences at once."""
        for key, value in updates.items():
            self.set(key, value, user_id=user_id)

    def get_all(self, user_id: str | None = None) -> dict:
        """Return all preferences merged with defaults."""
        self._ensure_db()
        prefs = dict(self.DEFAULTS)
        with get_conn() as conn:
            cur = conn.cursor()
            if user_id:
                cur.execute(
                    "SELECT key, value FROM user_preferences WHERE user_id = %s",
                    (user_id,),
                )
            else:
                cur.execute("SELECT key, value FROM preferences")
            rows = cur.fetchall()
        for key, value in rows:
            prefs[key] = json.loads(value)
        return prefs

    def as_context_string(self, user_id: str | None = None) -> str:
        """Format preferences as a readable string for injection into the system prompt."""
        prefs = self.get_all(user_id=user_id)
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
