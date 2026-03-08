"""
Workspace store — collaborative trip planning spaces.

A workspace groups multiple users around a shared session/itinerary.
The owner can invite members by email; members can view and edit the itinerary.

Tables:
  workspaces        — id, name, owner_id, session_id, created_at
  workspace_members — workspace_id, user_id, role, invited_email, joined_at
"""

import secrets
from datetime import datetime

from memory.db import get_conn  # noqa: E402

ROLES = ("owner", "editor", "viewer")


class WorkspaceStore:
    """Persistent store for collaborative workspaces."""

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
                CREATE TABLE IF NOT EXISTS workspaces (
                    id         TEXT PRIMARY KEY,
                    name       TEXT NOT NULL,
                    owner_id   TEXT NOT NULL,
                    session_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS workspace_members (
                    workspace_id   TEXT NOT NULL,
                    user_id        TEXT,
                    invited_email  TEXT NOT NULL,
                    role           TEXT NOT NULL DEFAULT 'editor',
                    joined_at      TEXT,
                    PRIMARY KEY (workspace_id, invited_email)
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ws_owner ON workspaces(owner_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ws_members_user ON workspace_members(user_id)"
            )
            self._ready = True

    # ── Workspace CRUD ────────────────────────────────────────────────────────

    def create(self, name: str, owner_id: str, session_id: str | None = None) -> dict:
        """Create a new workspace. Owner is automatically added as a member."""
        self._ensure_db()
        ws_id = "WS" + secrets.token_urlsafe(10)
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO workspaces (id, name, owner_id, session_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (ws_id, name, owner_id, session_id, now, now))
            # Add owner as a member
            cur.execute("""
                INSERT INTO workspace_members
                    (workspace_id, user_id, invited_email, role, joined_at)
                VALUES (%s, %s, %s, 'owner', %s)
                ON CONFLICT (workspace_id, invited_email) DO NOTHING
            """, (ws_id, owner_id, owner_id, now))
        return self.get(ws_id)

    def get(self, workspace_id: str) -> dict | None:
        """Return workspace with members list."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, owner_id, session_id, created_at FROM workspaces WHERE id = %s",
                (workspace_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                "SELECT user_id, invited_email, role, joined_at FROM workspace_members WHERE workspace_id = %s",
                (workspace_id,),
            )
            members = [
                {"user_id": r[0], "email": r[1], "role": r[2], "joined_at": r[3]}
                for r in cur.fetchall()
            ]
        return {
            "id": row[0], "name": row[1], "owner_id": row[2],
            "session_id": row[3], "created_at": row[4],
            "members": members, "member_count": len(members),
        }

    def list_for_user(self, user_id: str) -> list[dict]:
        """Return all workspaces the user is a member of."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT w.id, w.name, w.owner_id, w.session_id, w.created_at,
                       wm.role,
                       (SELECT COUNT(*) FROM workspace_members WHERE workspace_id = w.id) AS member_count
                FROM workspaces w
                JOIN workspace_members wm ON wm.workspace_id = w.id
                WHERE wm.user_id = %s
                ORDER BY w.updated_at DESC
            """, (user_id,))
            rows = cur.fetchall()
        return [
            {
                "id": r[0], "name": r[1], "owner_id": r[2],
                "session_id": r[3], "created_at": r[4],
                "my_role": r[5], "member_count": r[6],
            }
            for r in rows
        ]

    def add_member(self, workspace_id: str, invited_email: str, role: str = "editor") -> dict:
        """Invite a member by email. Returns membership record."""
        if role not in ROLES or role == "owner":
            raise ValueError(f"Invalid role: {role!r}. Must be 'editor' or 'viewer'.")
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO workspace_members (workspace_id, user_id, invited_email, role, joined_at)
                VALUES (%s, NULL, %s, %s, %s)
                ON CONFLICT (workspace_id, invited_email) DO UPDATE SET role = EXCLUDED.role
            """, (workspace_id, invited_email, role, now))
        return {"workspace_id": workspace_id, "invited_email": invited_email, "role": role}

    def remove_member(self, workspace_id: str, email: str, requesting_user_id: str) -> bool:
        """Remove a member. Only the owner can remove others."""
        self._ensure_db()
        ws = self.get(workspace_id)
        if not ws or ws["owner_id"] != requesting_user_id:
            return False
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM workspace_members WHERE workspace_id = %s AND invited_email = %s AND role != 'owner'",
                (workspace_id, email),
            )
        return True

    def user_role(self, workspace_id: str, user_id: str) -> str | None:
        """Return the user's role in the workspace, or None if not a member."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT role FROM workspace_members WHERE workspace_id = %s AND user_id = %s",
                (workspace_id, user_id),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def link_session(self, workspace_id: str, session_id: str) -> None:
        """Attach a planning session to a workspace."""
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE workspaces SET session_id = %s, updated_at = %s WHERE id = %s",
                (session_id, now, workspace_id),
            )

    def delete(self, workspace_id: str, owner_id: str) -> bool:
        """Delete a workspace. Only the owner can delete it."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM workspaces WHERE id = %s AND owner_id = %s",
                (workspace_id, owner_id),
            )
            return cur.rowcount > 0
