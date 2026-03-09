"""
Workspace store — collaborative trip planning spaces.

A workspace groups multiple users around a shared session/itinerary.
The owner can invite members by email; members can view and edit the itinerary.

A *group* (type='group') is a persistent membership space where all joined
members can view each other's saved trips.  Groups do not require a linked
session and are not tied to a single itinerary.

Tables:
  workspaces        — id, name, owner_id, session_id, type, created_at
  workspace_members — workspace_id, user_id, role, invited_email, joined_at
  invite_logs       — rate-limit log for outgoing invites
"""

import secrets
from datetime import datetime, timedelta

from memory.db import get_conn  # noqa: E402

ROLES = ("owner", "editor", "viewer")

# Invite rate-limit constants
INVITE_DAILY_LIMIT = 20        # max total invites per user per 24 h
INVITE_SAME_EMAIL_DAILY = 3    # max invites to the same email per user per 24 h


class WorkspaceStore:
    """Persistent store for collaborative workspaces and groups."""

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
                    type       TEXT NOT NULL DEFAULT 'workspace',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # Add type column to existing deployments that don't have it yet
            cur.execute("""
                ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS type TEXT NOT NULL DEFAULT 'workspace'
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
            # Rate-limit log: one row per invite sent
            cur.execute("""
                CREATE TABLE IF NOT EXISTS invite_logs (
                    id            SERIAL PRIMARY KEY,
                    inviter_id    TEXT NOT NULL,
                    workspace_id  TEXT NOT NULL,
                    invited_email TEXT NOT NULL,
                    created_at    TEXT NOT NULL
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ws_owner ON workspaces(owner_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ws_members_user ON workspace_members(user_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_invite_logs_inviter "
                "ON invite_logs(inviter_id, created_at)"
            )
            self._ready = True

    # ── Workspace / Group CRUD ─────────────────────────────────────────────────

    def create(
        self,
        name: str,
        owner_id: str,
        session_id: str | None = None,
        ws_type: str = "workspace",
    ) -> dict:
        """Create a new workspace or group. Owner is automatically added as a member."""
        self._ensure_db()
        ws_id = "WS" + secrets.token_urlsafe(10)
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO workspaces (id, name, owner_id, session_id, type, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (ws_id, name, owner_id, session_id, ws_type, now, now))
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
                "SELECT id, name, owner_id, session_id, type, created_at FROM workspaces WHERE id = %s",
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
            "session_id": row[3], "type": row[4], "created_at": row[5],
            "members": members, "member_count": len(members),
        }

    def list_for_user(self, user_id: str, ws_type: str | None = None) -> list[dict]:
        """Return all workspaces/groups the user is a joined member of."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            if ws_type:
                cur.execute("""
                    SELECT w.id, w.name, w.owner_id, w.session_id, w.type, w.created_at,
                           wm.role,
                           (SELECT COUNT(*) FROM workspace_members WHERE workspace_id = w.id) AS member_count
                    FROM workspaces w
                    JOIN workspace_members wm ON wm.workspace_id = w.id
                    WHERE wm.user_id = %s AND w.type = %s
                    ORDER BY w.updated_at DESC
                """, (user_id, ws_type))
            else:
                cur.execute("""
                    SELECT w.id, w.name, w.owner_id, w.session_id, w.type, w.created_at,
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
                "session_id": r[3], "type": r[4], "created_at": r[5],
                "my_role": r[6], "member_count": r[7],
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
                VALUES (%s, NULL, %s, %s, NULL)
                ON CONFLICT (workspace_id, invited_email) DO UPDATE SET role = EXCLUDED.role
            """, (workspace_id, invited_email, role))
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

    # ── Group-specific methods ─────────────────────────────────────────────────

    def check_and_log_invite(
        self, inviter_id: str, workspace_id: str, invited_email: str
    ) -> None:
        """Check invite rate limits then record the invite.

        Raises ValueError if the user has exceeded their daily invite quota.
        Limits:
          - INVITE_DAILY_LIMIT  total invites per 24 h
          - INVITE_SAME_EMAIL_DAILY  invites to the same address per 24 h
        """
        self._ensure_db()
        cutoff = (datetime.now() - timedelta(days=1)).isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            # Total invites in the last 24 hours
            cur.execute(
                "SELECT COUNT(*) FROM invite_logs WHERE inviter_id = %s AND created_at > %s",
                (inviter_id, cutoff),
            )
            total = cur.fetchone()[0]
            if total >= INVITE_DAILY_LIMIT:
                raise ValueError(
                    f"Daily invite limit reached ({INVITE_DAILY_LIMIT}/day). Try again tomorrow."
                )
            # Invites to this specific address in the last 24 hours
            cur.execute(
                "SELECT COUNT(*) FROM invite_logs "
                "WHERE inviter_id = %s AND invited_email = %s AND created_at > %s",
                (inviter_id, invited_email, cutoff),
            )
            same_email_count = cur.fetchone()[0]
            if same_email_count >= INVITE_SAME_EMAIL_DAILY:
                raise ValueError(
                    f"You've already sent {INVITE_SAME_EMAIL_DAILY} invites to this address today."
                )
            # Record the invite
            cur.execute(
                "INSERT INTO invite_logs (inviter_id, workspace_id, invited_email, created_at) "
                "VALUES (%s, %s, %s, %s)",
                (inviter_id, workspace_id, invited_email, datetime.now().isoformat()),
            )

    def get_pending_invites_for_email(self, email: str) -> list[dict]:
        """Return groups/workspaces where this email has a pending (un-joined) invite."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT w.id, w.name, w.type, w.owner_id, wm.role, w.created_at
                FROM workspace_members wm
                JOIN workspaces w ON w.id = wm.workspace_id
                WHERE wm.invited_email = %s AND wm.user_id IS NULL
                ORDER BY w.created_at DESC
            """, (email,))
            rows = cur.fetchall()
        return [
            {
                "id": r[0], "name": r[1], "type": r[2],
                "owner_id": r[3], "role": r[4], "created_at": r[5],
            }
            for r in rows
        ]

    def join(self, workspace_id: str, user_id: str, user_email: str) -> bool:
        """Claim a pending invite by matching the user's email.

        Returns True if successfully joined, False if no matching invite exists.
        """
        self._ensure_db()
        now = datetime.now().isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE workspace_members
                SET user_id = %s, joined_at = %s
                WHERE workspace_id = %s AND invited_email = %s AND user_id IS NULL
            """, (user_id, now, workspace_id, user_email))
            return cur.rowcount > 0
