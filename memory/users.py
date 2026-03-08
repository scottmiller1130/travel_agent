"""
User store — maps Clerk user IDs to local user records and tracks usage.

Tables created here:
  users            — id, email, name, plan, timestamps
  usage            — per-user per-month chat_turns + api_calls counters
  user_preferences — per-user preference overrides (same keys as global preferences)
"""

from datetime import datetime

from memory.db import get_conn

# ── Plan limits ───────────────────────────────────────────────────────────────
# -1 means unlimited
PLAN_LIMITS: dict[str, dict[str, int]] = {
    "free": {"chat_turns": 20,  "api_calls": 50},
    "pro":  {"chat_turns": -1,  "api_calls": 200},
    "team": {"chat_turns": -1,  "api_calls": 500},
}

PLAN_DISPLAY = {
    "free": {"label": "Free",  "color": "#64748b"},
    "pro":  {"label": "Pro",   "color": "#0d9488"},
    "team": {"label": "Team",  "color": "#6366f1"},
}


class UserStore:
    """Persistent store for user accounts and usage tracking."""

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
                CREATE TABLE IF NOT EXISTS users (
                    id         TEXT PRIMARY KEY,
                    email      TEXT,
                    name       TEXT,
                    plan       TEXT NOT NULL DEFAULT 'free',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usage (
                    user_id    TEXT NOT NULL,
                    month      TEXT NOT NULL,
                    chat_turns INTEGER NOT NULL DEFAULT 0,
                    api_calls  INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, month)
                )
            """)
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

    # ── User CRUD ─────────────────────────────────────────────────────────────

    def upsert(self, user_id: str, email: str = "", name: str = "") -> dict:
        """Create or update a user record. Returns the user dict."""
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO users (id, email, name, plan, created_at, updated_at)
                VALUES (%s, %s, %s, 'free', %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    email      = COALESCE(NULLIF(EXCLUDED.email, ''), users.email),
                    name       = COALESCE(NULLIF(EXCLUDED.name, ''),  users.name),
                    updated_at = EXCLUDED.updated_at
                RETURNING id, email, name, plan
            """, (user_id, email, name, now, now))
            row = cur.fetchone()
        return {"id": row[0], "email": row[1] or "", "name": row[2] or "", "plan": row[3]}

    def get(self, user_id: str) -> dict | None:
        """Return user record or None."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, email, name, plan FROM users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "email": row[1] or "", "name": row[2] or "", "plan": row[3]}

    def set_plan(self, user_id: str, plan: str) -> None:
        """Update a user's subscription plan."""
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE users SET plan = %s, updated_at = %s WHERE id = %s",
                (plan, now, user_id),
            )

    # ── Usage tracking ────────────────────────────────────────────────────────

    def get_usage(self, user_id: str) -> dict:
        """Return this month's usage for a user."""
        self._ensure_db()
        month = datetime.now().strftime("%Y-%m")
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT chat_turns, api_calls FROM usage WHERE user_id = %s AND month = %s",
                (user_id, month),
            )
            row = cur.fetchone()
        return {
            "chat_turns": row[0] if row else 0,
            "api_calls":  row[1] if row else 0,
            "month":      month,
        }

    def increment_chat(self, user_id: str) -> dict:
        """Increment chat turn count. Returns updated usage dict."""
        self._ensure_db()
        month = datetime.now().strftime("%Y-%m")
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO usage (user_id, month, chat_turns, api_calls)
                VALUES (%s, %s, 1, 0)
                ON CONFLICT (user_id, month) DO UPDATE SET
                    chat_turns = usage.chat_turns + 1
                RETURNING chat_turns, api_calls
            """, (user_id, month))
            row = cur.fetchone()
        return {"chat_turns": row[0], "api_calls": row[1], "month": month}

    def increment_api(self, user_id: str) -> dict:
        """Increment API call count (flights/hotels). Returns updated usage dict."""
        self._ensure_db()
        month = datetime.now().strftime("%Y-%m")
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO usage (user_id, month, chat_turns, api_calls)
                VALUES (%s, %s, 0, 1)
                ON CONFLICT (user_id, month) DO UPDATE SET
                    api_calls = usage.api_calls + 1
                RETURNING chat_turns, api_calls
            """, (user_id, month))
            row = cur.fetchone()
        return {"chat_turns": row[0], "api_calls": row[1], "month": month}

    def within_limit(self, user: dict, resource: str, usage: dict) -> bool:
        """Return True if user is within their plan limit for the given resource."""
        plan   = user.get("plan", "free")
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        cap    = limits.get(resource, 0)
        return cap == -1 or usage.get(resource, 0) < cap

    def limits_for(self, plan: str) -> dict:
        return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
