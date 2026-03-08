"""Shared pytest fixtures for the travel agent test suite."""

import re
import sqlite3
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from tools.cache import _global_cache

# ---------------------------------------------------------------------------
# Stub psycopg2 before any module imports it so the import itself doesn't fail
# in environments where the driver isn't installed.
# ---------------------------------------------------------------------------
sys.modules.setdefault("psycopg2", MagicMock())
sys.modules.setdefault("psycopg2.pool", MagicMock())
sys.modules.setdefault("psycopg2.extras", MagicMock())

# ---------------------------------------------------------------------------
# Replace memory.db.get_conn with a SQLite-backed implementation.
#
# The stores use two PostgreSQL-isms that need translating:
#   • %s  →  ?   (parameter placeholder)
#   • INSERT … ON CONFLICT (k) DO UPDATE SET …  →  INSERT OR REPLACE INTO …
#   • INSERT … ON CONFLICT (k) DO NOTHING       →  INSERT OR IGNORE  INTO …
#   • ALTER TABLE t ADD COLUMN IF NOT EXISTS … → strip IF NOT EXISTS and
#     silently swallow the "duplicate column" error
# ---------------------------------------------------------------------------
import memory.db as _memory_db  # noqa: E402 (must come after psycopg2 stub)

_sqlite = sqlite3.connect(":memory:", check_same_thread=False)


def _pg_to_sqlite(sql: str) -> str:
    sql = sql.replace("%s", "?")

    # ALTER TABLE … ADD COLUMN IF NOT EXISTS  → strip the IF NOT EXISTS
    sql = re.sub(
        r"\bADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\b",
        "ADD COLUMN",
        sql,
        flags=re.IGNORECASE,
    )

    # INSERT … ON CONFLICT … DO NOTHING  →  INSERT OR IGNORE INTO …
    if re.search(r"\bON\s+CONFLICT\b.*\bDO\s+NOTHING\b", sql, re.IGNORECASE | re.DOTALL):
        sql = re.sub(r"\bINSERT\s+INTO\b", "INSERT OR IGNORE INTO", sql, flags=re.IGNORECASE)
        sql = re.sub(
            r"\s+ON\s+CONFLICT\b.*",
            "",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
    # INSERT … ON CONFLICT … DO UPDATE SET …  →  INSERT OR REPLACE INTO …
    elif re.search(r"\bON\s+CONFLICT\b", sql, re.IGNORECASE):
        sql = re.sub(r"\bINSERT\s+INTO\b", "INSERT OR REPLACE INTO", sql, flags=re.IGNORECASE)
        sql = re.sub(
            r"\s+ON\s+CONFLICT\b.*",
            "",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )

    return sql


class _Cursor:
    def __init__(self):
        self._cur = _sqlite.cursor()

    def execute(self, sql, params=()):
        translated = _pg_to_sqlite(sql)
        try:
            self._cur.execute(translated, params)
        except sqlite3.OperationalError as exc:
            # Swallow "duplicate column name" from ADD COLUMN on existing tables
            if "duplicate column" in str(exc).lower():
                return
            raise

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class _Conn:
    def cursor(self):
        return _Cursor()

    def commit(self):
        _sqlite.commit()

    def rollback(self):
        _sqlite.rollback()


@contextmanager
def _get_conn():
    yield _Conn()


# Patch memory.db itself
_memory_db.get_conn = _get_conn

# Also patch modules already imported via memory/__init__.py before we had
# a chance to replace get_conn in memory.db (each did `from memory.db import
# get_conn`, capturing a local reference to the original function).
import memory.preferences as _mem_prefs  # noqa: E402
import memory.sessions as _mem_sessions  # noqa: E402
import memory.trips as _mem_trips  # noqa: E402

_mem_prefs.get_conn = _get_conn
_mem_sessions.get_conn = _get_conn
_mem_trips.get_conn = _get_conn


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the TTL cache before every test to prevent cross-test pollution."""
    _global_cache.clear()
    yield
    _global_cache.clear()
