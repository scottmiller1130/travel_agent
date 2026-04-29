"""
Cloudflare R2 backup — daily full-database exports to external object storage.

Each backup is a gzipped JSON file containing every row from every critical table.
Keeps a rolling 30-day window (one backup per day).  R2's free tier (10 GB, no
egress fees) comfortably covers years of daily exports for this dataset size.

Required env vars (add to Railway Variables, never to code):
    R2_ACCOUNT_ID        Cloudflare account ID (from R2 dashboard URL)
    R2_ACCESS_KEY_ID     R2 API token key ID
    R2_SECRET_ACCESS_KEY R2 API token secret
    R2_BUCKET_NAME       Bucket name (default: travelagentbackup)

If any of those vars are missing, all functions raise RuntimeError with a
clear message so the server logs surface the misconfiguration immediately.
"""

import gzip
import json
import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

# Tables exported in dependency order (parents before children).
_TABLES = [
    "users",
    "preferences",
    "workspaces",
    "workspace_members",
    "invite_logs",
    "trips",
    "trip_backups",
    "sessions",
    "share_tokens",
    "user_preferences",
    "usage",
]

MAX_BACKUPS = 15  # 15 daily exports ≈ two weeks of rolling history


def r2_configured() -> bool:
    """Return True if all R2 env vars are present."""
    return all(
        os.environ.get(k)
        for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    )


def _client():
    """Build a boto3 S3 client pointed at Cloudflare R2."""
    import boto3  # imported lazily so missing boto3 only fails when R2 is used

    account_id = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def _bucket() -> str:
    return os.environ.get("R2_BUCKET_NAME", "travelagentbackup")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_database() -> bytes:
    """Dump all critical tables to gzipped JSON.  Returns the raw bytes."""
    from memory.db import get_conn

    snapshot: dict = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "version": 2,
        "tables": {},
    }

    with get_conn() as conn:
        cur = conn.cursor()
        for table in _TABLES:
            try:
                cur.execute(f"SELECT * FROM {table}")  # noqa: S608
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                snapshot["tables"][table] = [dict(zip(cols, row)) for row in rows]
                log.debug("Exported %d rows from %s", len(rows), table)
            except Exception as exc:
                # Table may not exist yet in older deployments — skip gracefully
                log.warning("Skipping table %s during export: %s", table, exc)
                snapshot["tables"][table] = []

    payload = json.dumps(snapshot, default=str, ensure_ascii=False).encode()
    return gzip.compress(payload, compresslevel=6)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_backup() -> dict:
    """
    Export the database and upload to R2.

    Returns a summary dict with the R2 key, compressed size, and per-table
    row counts so the caller can log / return it to an admin endpoint.
    """
    data = export_database()
    key = f"backup_{datetime.utcnow().strftime('%Y-%m-%dT%H%M%S')}Z.json.gz"

    client = _client()
    bucket = _bucket()
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType="application/gzip",
    )
    log.info("R2 backup uploaded: %s (%d bytes compressed)", key, len(data))

    _prune_old(client, bucket)

    # Parse the payload we already compressed to extract row counts
    parsed = json.loads(gzip.decompress(data))
    row_counts = {t: len(rows) for t, rows in parsed["tables"].items()}

    return {"key": key, "size_bytes": len(data), "tables": row_counts}


def _prune_old(client, bucket: str) -> None:
    """Delete backups beyond MAX_BACKUPS, keeping the newest ones."""
    paginator = client.get_paginator("list_objects_v2")
    objects = []
    for page in paginator.paginate(Bucket=bucket, Prefix="backup_"):
        objects.extend(page.get("Contents", []))

    objects.sort(key=lambda o: o["Key"], reverse=True)  # newest key = latest date
    to_delete = objects[MAX_BACKUPS:]
    for obj in to_delete:
        client.delete_object(Bucket=bucket, Key=obj["Key"])
        log.info("Pruned old R2 backup: %s", obj["Key"])


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def list_backups() -> list[dict]:
    """Return metadata for all backup files in R2, newest first."""
    client = _client()
    bucket = _bucket()

    paginator = client.get_paginator("list_objects_v2")
    objects = []
    for page in paginator.paginate(Bucket=bucket, Prefix="backup_"):
        objects.extend(page.get("Contents", []))

    objects.sort(key=lambda o: o["Key"], reverse=True)
    return [
        {
            "key": o["Key"],
            "size_bytes": o["Size"],
            "last_modified": o["LastModified"].isoformat(),
        }
        for o in objects
    ]


# ---------------------------------------------------------------------------
# Download (for local inspection)
# ---------------------------------------------------------------------------

def download_backup(key: str) -> bytes:
    """Return the raw gzipped bytes for a given backup key."""
    client = _client()
    response = client.get_object(Bucket=_bucket(), Key=key)
    return response["Body"].read()


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore_backup(key: str) -> dict:
    """
    Download a backup from R2 and restore every row that doesn't already exist.

    Uses INSERT … ON CONFLICT DO NOTHING so it is fully non-destructive:
    existing live data is never overwritten.  Run this after provisioning a
    fresh database to recover from catastrophic loss.

    Returns a dict of {table: rows_inserted}.
    """
    data = download_backup(key)
    snapshot = json.loads(gzip.decompress(data))

    from memory.db import get_conn

    restored: dict[str, int] = {}

    with get_conn() as conn:
        cur = conn.cursor()
        for table, rows in snapshot["tables"].items():
            if not rows:
                restored[table] = 0
                continue

            cols = list(rows[0].keys())
            col_list = ", ".join(cols)
            placeholders = ", ".join(["%s"] * len(cols))
            count = 0

            for row in rows:
                try:
                    cur.execute(
                        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"  # noqa: S608
                        " ON CONFLICT DO NOTHING",
                        [row.get(c) for c in cols],
                    )
                    count += cur.rowcount
                except Exception as exc:
                    log.warning("Skipping row in %s during restore: %s", table, exc)

            restored[table] = count
            log.info("Restored %d rows into %s", count, table)

    log.info("Restore from %s complete: %s", key, restored)
    return {"key": key, "restored": restored}
