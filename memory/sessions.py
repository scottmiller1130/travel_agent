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
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS share_tokens (
                    token      TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_share_tokens_session ON share_tokens(session_id)"
            )
            self._ready = True

    def create(self, session_id: str) -> None:
        """Create a new empty session row (server-generated IDs only)."""
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO sessions (id, conversation, itinerary, created_at, updated_at)
                VALUES (%s, '[]', NULL, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (session_id, now, now))

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

    def delete(self, session_id: str) -> None:
        """Delete a session (used on conversation reset)."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM sessions WHERE id = %s", (session_id,))

    def create_share_token(self, session_id: str) -> str:
        """Generate a share token for a session and return it."""
        self._ensure_db()
        token = secrets.token_urlsafe(16)
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO share_tokens (token, session_id, created_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (token) DO UPDATE SET session_id = EXCLUDED.session_id
                """,
                (token, session_id, now),
            )
        return token

    def expire_old_sessions(self, days: int = 30) -> int:
        """Delete sessions not updated in `days` days. Returns count deleted."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM sessions WHERE updated_at < %s", (cutoff,))
            deleted = cur.rowcount
            cur.execute(
                "DELETE FROM share_tokens WHERE session_id NOT IN (SELECT id FROM sessions)"
            )
        return deleted

    def get_session_for_token(self, token: str) -> dict | None:
        """Return the itinerary for a share token, or None if invalid."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT s.itinerary
                FROM share_tokens t
                JOIN sessions s ON t.session_id = s.id
                WHERE t.token = %s
                """,
                (token,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {"itinerary": json.loads(row[0]) if row[0] else None}

    def list_sessions(self) -> list[dict]:
        """Return metadata for all sessions (for admin/debugging)."""
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
            )
            rows = cur.fetchall()
        return [{"id": r[0], "created_at": r[1], "updated_at": r[2]} for r in rows]
