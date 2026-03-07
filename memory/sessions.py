"""
Session store — persists conversation history and itinerary state to SQLite.

This is the key piece that lets users return days later and pick up exactly
where they left off. Each browser sessionId maps to a full conversation
history and the latest itinerary snapshot.
"""

import json
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from memory._data_dir import data_dir


DB_PATH = data_dir() / "sessions.db"


class SessionStore:
    """Persistent store for per-session conversation history and itinerary."""

    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        # WAL mode allows concurrent reads while writes are in progress,
        # which is critical for a multi-threaded FastAPI server.
        self._write_lock = threading.Lock()
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL
        self._conn.commit()
        self._init_db()

    def _init_db(self):
        with self._write_lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id          TEXT PRIMARY KEY,
                    conversation TEXT NOT NULL DEFAULT '[]',
                    itinerary   TEXT,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS share_tokens (
                    token       TEXT PRIMARY KEY,
                    session_id  TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_share_tokens_session ON share_tokens(session_id)"
            )
            self._conn.commit()

    def create(self, session_id: str) -> None:
        """Create a new empty session row (server-generated IDs only)."""
        now = datetime.now().isoformat()
        with self._write_lock:
            self._conn.execute("""
                INSERT OR IGNORE INTO sessions (id, conversation, itinerary, created_at, updated_at)
                VALUES (?, '[]', NULL, ?, ?)
            """, (session_id, now, now))
            self._conn.commit()

    def exists(self, session_id: str) -> bool:
        """Return True if the session was created server-side."""
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row is not None

    def load(self, session_id: str) -> dict | None:
        """Return saved session data or None if not found."""
        row = self._conn.execute(
            "SELECT conversation, itinerary FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "conversation": json.loads(row[0]),
            "itinerary": json.loads(row[1]) if row[1] else None,
        }

    def save(self, session_id: str, conversation: list, itinerary=None) -> None:
        """Upsert session data."""
        now = datetime.now().isoformat()
        with self._write_lock:
            self._conn.execute("""
                INSERT INTO sessions (id, conversation, itinerary, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    conversation = excluded.conversation,
                    itinerary    = COALESCE(excluded.itinerary, sessions.itinerary),
                    updated_at   = excluded.updated_at
            """, (
                session_id,
                json.dumps(conversation),
                json.dumps(itinerary) if itinerary is not None else None,
                now,
                now,
            ))
            self._conn.commit()

    def save_itinerary(self, session_id: str, itinerary: dict) -> None:
        """Update only the itinerary for an existing session (e.g. drag-and-drop or import)."""
        now = datetime.now().isoformat()
        with self._write_lock:
            self._conn.execute("""
                INSERT INTO sessions (id, conversation, itinerary, created_at, updated_at)
                VALUES (?, '[]', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    itinerary  = excluded.itinerary,
                    updated_at = excluded.updated_at
            """, (session_id, json.dumps(itinerary), now, now))
            self._conn.commit()

    def delete(self, session_id: str) -> None:
        """Delete a session (used on conversation reset)."""
        with self._write_lock:
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    def create_share_token(self, session_id: str) -> str:
        """Generate a share token for a session and return it."""
        token = secrets.token_urlsafe(16)
        now = datetime.now().isoformat()
        with self._write_lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO share_tokens (token, session_id, created_at) VALUES (?, ?, ?)",
                (token, session_id, now),
            )
            self._conn.commit()
        return token

    def expire_old_sessions(self, days: int = 30) -> int:
        """Delete sessions not updated in `days` days. Returns count deleted."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._write_lock:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE updated_at < ?", (cutoff,)
            )
            deleted = cur.rowcount
            # Clean up orphaned share tokens
            self._conn.execute(
                "DELETE FROM share_tokens WHERE session_id NOT IN (SELECT id FROM sessions)"
            )
            self._conn.commit()
        return deleted

    def get_session_for_token(self, token: str) -> dict | None:
        """Return the itinerary for a share token, or None if invalid."""
        row = self._conn.execute(
            "SELECT s.itinerary FROM share_tokens t JOIN sessions s ON t.session_id = s.id WHERE t.token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None
        return {"itinerary": json.loads(row[0]) if row[0] else None}

    def list_sessions(self) -> list[dict]:
        """Return metadata for all sessions (for admin/debugging)."""
        rows = self._conn.execute(
            "SELECT id, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        return [{"id": r[0], "created_at": r[1], "updated_at": r[2]} for r in rows]
