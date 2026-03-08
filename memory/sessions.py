"""
Session store — persists conversation history and itinerary state to Supabase (PostgreSQL).

This is the key piece that lets users return days later and pick up exactly
where they left off. Each browser sessionId maps to a full conversation
history and the latest itinerary snapshot.
"""

import json
import secrets
from datetime import datetime, timedelta

from memory.db import get_conn


class SessionStore:
    """Persistent store for per-session conversation history and itinerary."""

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
                CREATE TABLE IF NOT EXISTS sessions (
                    id           TEXT PRIMARY KEY,
                    conversation TEXT NOT NULL DEFAULT '[]',
                    itinerary    TEXT,
                    user_id      TEXT,
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                )
            """)
            # Add user_id column to existing deployments that don't have it yet
            cur.execute("""
                ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_id TEXT
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS share_tokens (
                    token              TEXT PRIMARY KEY,
                    session_id         TEXT NOT NULL,
                    itinerary_snapshot TEXT,
                    created_at         TEXT NOT NULL,
                    expires_at         TEXT NOT NULL
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_share_tokens_session ON share_tokens(session_id)"
            )
            # Migrate existing deployments — add columns introduced in v2 of share_tokens
            cur.execute(
                "ALTER TABLE share_tokens ADD COLUMN IF NOT EXISTS itinerary_snapshot TEXT"
            )
            cur.execute(
                "ALTER TABLE share_tokens ADD COLUMN IF NOT EXISTS expires_at TEXT NOT NULL DEFAULT '2099-12-31'"
            )
            self._ready = True

    def create(self, session_id: str, user_id: str | None = None) -> None:
        """Create a new empty session row (server-generated IDs only)."""
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO sessions (id, conversation, itinerary, user_id, created_at, updated_at)
                VALUES (%s, '[]', NULL, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (session_id, user_id, now, now))

    def exists(self, session_id: str) -> bool:
        """Return True if the session was created server-side."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM sessions WHERE id = %s", (session_id,))
            return cur.fetchone() is not None

    def load(self, session_id: str) -> dict | None:
        """Return saved session data or None if not found."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT conversation, itinerary FROM sessions WHERE id = %s",
                (session_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "conversation": json.loads(row[0]),
            "itinerary": json.loads(row[1]) if row[1] else None,
        }

    def save(self, session_id: str, conversation: list, itinerary=None) -> None:
        """Upsert session data."""
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO sessions (id, conversation, itinerary, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    conversation = EXCLUDED.conversation,
                    itinerary    = COALESCE(EXCLUDED.itinerary, sessions.itinerary),
                    updated_at   = EXCLUDED.updated_at
            """, (
                session_id,
                json.dumps(conversation),
                json.dumps(itinerary) if itinerary is not None else None,
                now,
                now,
            ))

    def save_itinerary(self, session_id: str, itinerary: dict) -> None:
        """Update only the itinerary for an existing session (e.g. drag-and-drop or import)."""
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO sessions (id, conversation, itinerary, created_at, updated_at)
                VALUES (%s, '[]', %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    itinerary  = EXCLUDED.itinerary,
                    updated_at = EXCLUDED.updated_at
            """, (session_id, json.dumps(itinerary), now, now))

    def clear_itinerary(self, session_id: str) -> None:
        """Clear only the itinerary for a session, leaving conversation intact."""
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE sessions SET itinerary = NULL, updated_at = %s WHERE id = %s
            """, (now, session_id))

    def delete(self, session_id: str) -> None:
        """Delete a session (used on conversation reset)."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM sessions WHERE id = %s", (session_id,))

    def create_share_token(self, session_id: str, itinerary: dict) -> str:
        """Snapshot the itinerary and return a permanent share token (valid 90 days)."""
        self._ensure_db()
        token = secrets.token_urlsafe(16)
        now = datetime.now()
        expires = (now + timedelta(days=90)).isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO share_tokens (token, session_id, itinerary_snapshot, created_at, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (token, session_id, json.dumps(itinerary), now.isoformat(), expires),
            )
        return token

    def expire_old_sessions(self, days: int = 30) -> int:
        """Delete sessions not updated in `days` days. Returns count deleted."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM sessions WHERE updated_at < %s", (cutoff,))
            deleted = cur.rowcount
            # Remove share tokens that have expired
            cur.execute("DELETE FROM share_tokens WHERE expires_at < %s", (now,))
        return deleted

    def get_session_for_token(self, token: str) -> dict | None:
        """Return the snapshotted itinerary for a share token, or None if invalid/expired."""
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT itinerary_snapshot, expires_at
                FROM share_tokens
                WHERE token = %s
                """,
                (token,),
            )
            row = cur.fetchone()
        if not row:
            return None
        snapshot, expires_at = row
        if expires_at < now:
            return None  # treat expired tokens as not found
        return {"itinerary": json.loads(snapshot) if snapshot else None}

    def owns(self, session_id: str, user_id: str) -> bool:
        """Return True if the session belongs to the given user."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id FROM sessions WHERE id = %s",
                (session_id,),
            )
            row = cur.fetchone()
        if not row:
            return False
        # Anonymous sessions (user_id=NULL) are accessible to anyone who knows the ID
        return row[0] is None or row[0] == user_id

    def list_sessions(self) -> list[dict]:
        """Return metadata for all sessions (for admin/debugging)."""
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, user_id, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
            )
            rows = cur.fetchall()
        return [{"id": r[0], "user_id": r[1], "created_at": r[2], "updated_at": r[3]} for r in rows]
