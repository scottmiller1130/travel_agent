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

    # Default preferences — overridden by anything stored in DB.
    # Intentionally sparse: profile-sensitive fields (stars, budget, cabin class) are
    # left null so the agent uses the traveler_profile to set appropriate expectations
    # rather than silently assuming a 3-star/$300 budget worldview for everyone.
    DEFAULTS = {
        "preferred_airlines": [],
        "avoided_airlines": [],
        "seat_preference": "window",
        "cabin_class": None,           # null = infer from profile (economy for adventure, business for luxury)
        "hotel_min_stars": None,       # null = infer from profile (no filter for adventure, 4+ for luxury)
        "max_budget_per_day_usd": None, # null = infer from profile; set explicitly via setup or conversation
        "dietary_restrictions": [],
        "accessibility_needs": [],
        "preferred_activities": ["culture", "food", "nature"],
        "avoided_activities": [],
        "travel_pace": None,           # null = infer from profile (slow for adventure, moderate/fast for luxury)
        "home_airport": "",
        "home_city": "",
        "currency": "USD",
        "name": "",
        "email": "",
        # Traveler profile — the anchor for all profile-sensitive defaults
        "traveler_profile": None,      # adventure | luxury | mid_range | null (unknown)
        "travel_style": "",            # budget | mid-range | luxury (legacy; prefer traveler_profile)
        "values": [],                  # adventure | wellness | culture | relaxation | food | nature
        "companion_profile": "",       # solo | couple | family | group
        "trip_type": "",               # leisure | adventure | honeymoon | wellness | backpacking
        "accommodation_preference": "", # hotel | hostel | guesthouse | dorm | mixed
    }

    # Profile-driven defaults: what the agent should assume for each profile
    # when the explicit preference is null/unset.
    PROFILE_DEFAULTS = {
        "adventure": {
            "cabin_class": "economy",
            "hotel_min_stars": None,      # no star filter — hostel/dorm by default
            "max_budget_per_day_usd": 80,
            "travel_pace": "slow",
            "accommodation_preference": "hostel",
            "travel_style": "budget",
        },
        "mid_range": {
            "cabin_class": "economy",
            "hotel_min_stars": 3,
            "max_budget_per_day_usd": 150,
            "travel_pace": "moderate",
            "accommodation_preference": "hotel",
            "travel_style": "mid-range",
        },
        "luxury": {
            "cabin_class": "business",
            "hotel_min_stars": 4,
            "max_budget_per_day_usd": None,  # no cap — show best options
            "travel_pace": "moderate",
            "accommodation_preference": "hotel",
            "travel_style": "luxury",
        },
    }

    def get_profile_default(self, key: str, user_id: str | None = None):
        """Return profile-driven default for a key when the stored value is null/empty."""
        profile = self.get("traveler_profile", user_id=user_id)
        if not profile:
            return self.DEFAULTS.get(key)
        return self.PROFILE_DEFAULTS.get(profile, {}).get(key, self.DEFAULTS.get(key))

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
        """Set multiple preferences at once in a single DB round-trip."""
        if not updates:
            return
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            if user_id:
                values = [(user_id, k, json.dumps(v), now) for k, v in updates.items()]
                cur.executemany(
                    """
                    INSERT INTO user_preferences (user_id, key, value, updated_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, key) DO UPDATE SET
                        value      = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at
                    """,
                    values,
                )
            else:
                values = [(k, json.dumps(v), now) for k, v in updates.items()]
                cur.executemany(
                    """
                    INSERT INTO preferences (key, value, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (key) DO UPDATE SET
                        value      = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at
                    """,
                    values,
                )

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
        profile = prefs.get("traveler_profile")
        lines = ["User Travel Preferences:"]

        # Profile-sensitive fields: resolve null to profile default and annotate
        profile_sensitive = {"cabin_class", "hotel_min_stars", "max_budget_per_day_usd",
                             "travel_pace", "accommodation_preference"}
        profile_defaults = self.PROFILE_DEFAULTS.get(profile, {}) if profile else {}

        for key, value in prefs.items():
            label = key.replace("_", " ").title()
            if key in profile_sensitive and (value is None or value == ""):
                # Resolve via profile default
                resolved = profile_defaults.get(key)
                if resolved is not None:
                    lines.append(f"  - {label}: {resolved} (from {profile} profile)")
                else:
                    lines.append(f"  - {label}: not set (ask user or infer from context)")
                continue
            if value and value != [] and value != "":
                if isinstance(value, list):
                    value = ", ".join(str(v) for v in value)
                lines.append(f"  - {label}: {value}")

        if not profile:
            lines.append("  - Traveler Profile: UNKNOWN — ask 'Are you planning an adventure/backpacker trip, a mid-range trip, or a luxury experience?' before making budget assumptions.")

        return "\n".join(lines)

    def close(self):
        pass  # Connection pool is managed globally
