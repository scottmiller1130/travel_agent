"""
Trip backup store — weekly snapshots of the trips table stored in PostgreSQL.

Keeps the last MAX_BACKUPS snapshots (default 8 = ~2 months of weekly runs).
Protects against accidental deletes, code bugs, or bad deploys.

The database itself (Supabase) handles infrastructure-level durability;
this layer handles application-level accidents.
"""

import json
import logging
from datetime import datetime

from memory.db import get_conn

log = logging.getLogger(__name__)

MAX_BACKUPS = 8  # Keep 2 months of weekly snapshots


class BackupStore:
    def __init__(self):
        self._ready = False
        try:
            self._init_db()
        except Exception as exc:
            log.error("BackupStore DB init failed — will retry on first use: %s", exc)

    def _ensure_db(self):
        if not self._ready:
            self._init_db()

    def _init_db(self):
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trip_backups (
                    id          SERIAL PRIMARY KEY,
                    created_at  TEXT NOT NULL,
                    trip_count  INTEGER NOT NULL,
                    snapshot    TEXT NOT NULL
                )
            """)
        self._ready = True

    def create_backup(self) -> dict:
        """
        Snapshot all trips into trip_backups, then prune older entries so only
        the most recent MAX_BACKUPS rows are kept.  Returns summary info.
        """
        self._ensure_db()
        from memory.trips import TripStore
        all_trips = TripStore().get_all_trips()
        now = datetime.now().isoformat()

        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO trip_backups (created_at, trip_count, snapshot)
                VALUES (%s, %s, %s)
                """,
                (now, len(all_trips), json.dumps(all_trips)),
            )
            # Prune: keep only the newest MAX_BACKUPS rows
            cur.execute(
                """
                DELETE FROM trip_backups
                WHERE id NOT IN (
                    SELECT id FROM trip_backups
                    ORDER BY id DESC
                    LIMIT %s
                )
                """,
                (MAX_BACKUPS,),
            )

        log.info("Trip backup created: %d trips snapshotted at %s", len(all_trips), now)
        return {"created_at": now, "trip_count": len(all_trips)}

    def list_backups(self) -> list[dict]:
        """Return metadata for all stored backups (newest first), without snapshot data."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, created_at, trip_count FROM trip_backups ORDER BY id DESC"
            )
            rows = cur.fetchall()
        return [{"id": r[0], "created_at": r[1], "trip_count": r[2]} for r in rows]

    def get_backup(self, backup_id: int) -> list[dict] | None:
        """Return the full trip snapshot for a given backup ID."""
        self._ensure_db()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT snapshot FROM trip_backups WHERE id = %s", (backup_id,)
            )
            row = cur.fetchone()
        return json.loads(row[0]) if row else None
