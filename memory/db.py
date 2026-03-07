"""
Shared PostgreSQL connection pool for all memory stores.

Reads DATABASE_URL from the environment (set this to your Supabase
connection string in Railway Variables or .env).
"""

import os
from contextlib import contextmanager

import psycopg2
from psycopg2.pool import ThreadedConnectionPool

_pool: ThreadedConnectionPool | None = None


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ["DATABASE_URL"]
        _pool = ThreadedConnectionPool(1, 10, url)
    return _pool


@contextmanager
def get_conn():
    """Context manager that checks out a connection and returns it to the pool."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
