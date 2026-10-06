"""
scripts/sim_09_rotate_audit.py

Rotate old `agent_events` out of Postgres into MinIO so the hot DB stays
small as the chatbot runs over time.

For every day older than --days-old (default 30), the script:
  1. Fetches that day's events (ordered by created_at)
  2. Serializes to NDJSON, gzips it
  3. Uploads to MinIO at: audit/<YYYY-MM-DD>.jsonl.gz
  4. Deletes the rows from Postgres (ONLY after the upload succeeds)

Idempotent via "already-uploaded" check (skips days whose archive already
exists in MinIO unless --force).

Usage:
    python -m scripts.sim_09_rotate_audit                   # default: >30 days
    python -m scripts.sim_09_rotate_audit --days-old 7      # tighter window
    python -m scripts.sim_09_rotate_audit --dry-run         # show what would move
    python -m scripts.sim_09_rotate_audit --force           # redo already-archived days

Cron / Task Scheduler suggestion: run nightly at 03:00 local time.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.storage.minio_client import exists as minio_exists, put_bytes, public_url


def list_candidate_days(db: Session, cutoff: datetime) -> list[date]:
    """Distinct event-days older than cutoff that still have rows in Postgres."""
    rows = db.execute(
        text(
            """
            SELECT DISTINCT DATE(created_at) AS d
            FROM agent_events
            WHERE created_at < :cutoff
            ORDER BY d ASC
            """
        ),
        {"cutoff": cutoff},
    ).all()
    return [r[0] for r in rows]


def fetch_day(db: Session, d: date) -> list[dict]:
    """All events for a single UTC day. Preserves order by created_at."""
    rows = db.execute(
        text(
            """
            SELECT event_id, session_id, message_id,
                   event_type::text AS event_type,
                   details, created_at
            FROM agent_events
            WHERE created_at >= :start AND created_at < :end
            ORDER BY created_at ASC
            """
        ),
        {
            "start": datetime.combine(d, datetime.min.time()),
            "end":   datetime.combine(d + timedelta(days=1), datetime.min.time()),
        },
    ).mappings().all()
    return [dict(r) for r in rows]


def _serialize_row(row: dict) -> str:
    """One JSONL line. UUID + datetime → ISO strings."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif hasattr(v, "hex"):   # UUID
            out[k] = str(v)
        else:
            out[k] = v
    return json.dumps(out, default=str)


def rotate_one_day(db: Session, d: date, *, dry_run: bool, force: bool) -> dict:
    """Returns a summary dict. Pure stats on dry runs."""
    key = f"audit/{d.isoformat()}.jsonl.gz"

    if not force and minio_exists(key):
        # Archive exists but DB still has rows → safe to delete them
        rows_still = db.execute(
            text(
                """
                SELECT COUNT(*) FROM agent_events
                WHERE created_at >= :start AND created_at < :end
                """
            ),
            {
                "start": datetime.combine(d, datetime.min.time()),
                "end":   datetime.combine(d + timedelta(days=1), datetime.min.time()),
            },
        ).scalar() or 0
        if dry_run:
            return {"day": d.isoformat(), "status": "archived_exists",
                    "db_rows_remaining": int(rows_still)}
        if rows_still:
            db.execute(
                text(
                    """
                    DELETE FROM agent_events
                    WHERE created_at >= :start AND created_at < :end
                    """
                ),
                {
                    "start": datetime.combine(d, datetime.min.time()),
                    "end":   datetime.combine(d + timedelta(days=1), datetime.min.time()),
                },
            )
            db.commit()
        return {"day": d.isoformat(), "status": "already_archived",
                "deleted_from_db": int(rows_still)}

    rows = fetch_day(db, d)
    if not rows:
        return {"day": d.isoformat(), "status": "empty"}

    # Serialize + gzip in memory
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for row in rows:
            gz.write(_serialize_row(row).encode("utf-8"))
            gz.write(b"\n")
    blob = buf.getvalue()

    if dry_run:
        return {
            "day": d.isoformat(),
            "status": "would_upload",
            "row_count": len(rows),
            "bytes_gz": len(blob),
            "would_delete": len(rows),
        }

    url = put_bytes(key, blob, content_type="application/gzip")

    # Only delete after upload succeeded
    db.execute(
        text(
            """
            DELETE FROM agent_events
            WHERE created_at >= :start AND created_at < :end
            """
        ),
        {
            "start": datetime.combine(d, datetime.min.time()),
            "end":   datetime.combine(d + timedelta(days=1), datetime.min.time()),
        },
    )
    db.commit()

    return {
        "day": d.isoformat(),
        "status": "rotated",
        "row_count": len(rows),
        "bytes_gz": len(blob),
        "minio_url": url,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-old", type=int, default=30,
                    help="Rotate days OLDER than this many days ago (default 30)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would happen without touching anything")
    ap.add_argument("--force", action="store_true",
                    help="Re-upload (overwrite) days whose archive already exists")
    args = ap.parse_args()

    cutoff = datetime.utcnow() - timedelta(days=args.days_old)
    print(f"[sim_09] rotating events older than {cutoff.isoformat()}  "
          f"(dry_run={args.dry_run}, force={args.force})")

    engine = create_engine(DATABASE_URL)
    with Session(engine) as db:
        days = list_candidate_days(db, cutoff)
        if not days:
            print("[sim_09] nothing to rotate — DB has no events older than cutoff")
            return
        print(f"[sim_09] {len(days)} day(s) to process: "
              f"{days[0]} ... {days[-1]}")

        total_rows = total_bytes = 0
        for d in days:
            summary = rotate_one_day(db, d, dry_run=args.dry_run, force=args.force)
            status = summary["status"]
            icon = {
                "rotated":           "✓",
                "would_upload":      "~",
                "already_archived":  "-",
                "archived_exists":   "-",
                "empty":             "·",
            }.get(status, "?")
            print(f"  {icon} {summary}")
            total_rows  += summary.get("row_count", 0)
            total_bytes += summary.get("bytes_gz", 0)

        verb = "would move" if args.dry_run else "moved"
        print(f"[sim_09] done — {verb} {total_rows:,} rows "
              f"({total_bytes/1024:.1f} KiB gzipped) to MinIO")


if __name__ == "__main__":
    main()
