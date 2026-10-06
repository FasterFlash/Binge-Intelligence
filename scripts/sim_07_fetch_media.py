"""
scripts/sim_07_fetch_media.py

For every title in the catalog that doesn't already have a poster, fetch its
poster + backdrop from TMDB (by tmdb_id, which was seeded at catalog creation),
upload to MinIO under:
    posters/<title_id>.jpg
    backdrops/<title_id>.jpg
and write the public URLs back to titles.poster_url / titles.backdrop_url.

Idempotent: resuming after a crash skips anything that's already in MinIO.

Usage:
    python -m scripts.sim_07_fetch_media
    python -m scripts.sim_07_fetch_media --limit 100         # test run
    python -m scripts.sim_07_fetch_media --force             # re-download even if present
    python -m scripts.sim_07_fetch_media --no-backdrops      # posters only
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import DATABASE_URL, TMDB_BACKDROP_SIZE, TMDB_POSTER_SIZE
from app.storage.minio_client import exists as minio_exists, public_url, put_bytes
from app.tmdb.client import TmdbClient, TmdbError


def fetch_titles_needing_media(
    db: Session, force: bool, limit: int | None
) -> list[dict]:
    """Return title rows that still need poster_url populated (or all, if --force)."""
    sql = """
        SELECT title_id, tmdb_id, content_type::text AS content_type,
               title, release_year, poster_url, backdrop_url
        FROM titles
    """
    if not force:
        sql += " WHERE poster_url IS NULL"
    sql += " ORDER BY tmdb_popularity DESC NULLS LAST"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in db.execute(text(sql)).mappings().all()]


def process_one(
    tmdb: TmdbClient,
    row: dict,
    *,
    want_poster: bool,
    want_backdrop: bool,
    force: bool,
) -> dict:
    """Fetch + upload. Returns {'poster_url': ..., 'backdrop_url': ...} or {} on skip."""
    out: dict[str, str | None] = {}
    tmdb_id = row["tmdb_id"]
    title_id = str(row["title_id"])
    content_type = row["content_type"]

    poster_key = f"posters/{title_id}.jpg"
    backdrop_key = f"backdrops/{title_id}.jpg"

    # Short-circuit: if MinIO already holds the object, just record the URL.
    skip_poster = (
        want_poster and not force and minio_exists(poster_key)
    )
    skip_backdrop = (
        want_backdrop and not force and minio_exists(backdrop_key)
    )

    if skip_poster:
        out["poster_url"] = public_url(poster_key)
        want_poster = False
    if skip_backdrop:
        out["backdrop_url"] = public_url(backdrop_key)
        want_backdrop = False

    if not want_poster and not want_backdrop:
        return out  # nothing to fetch from TMDB

    # Need at least one from TMDB → look up image paths.
    try:
        imgs = tmdb.images(tmdb_id, content_type)
    except TmdbError as e:
        print(f"  tmdb error for id={tmdb_id} ({row['title']!r}): {e}")
        return out

    # Poster
    if want_poster and imgs.poster_path:
        try:
            data = tmdb.download_image(imgs.poster_path, TMDB_POSTER_SIZE)
            url = put_bytes(poster_key, data, content_type="image/jpeg")
            out["poster_url"] = url
        except Exception as e:
            print(f"  poster upload failed for {row['title']!r}: {e}")

    # Backdrop
    if want_backdrop and imgs.backdrop_path:
        try:
            data = tmdb.download_image(imgs.backdrop_path, TMDB_BACKDROP_SIZE)
            url = put_bytes(backdrop_key, data, content_type="image/jpeg")
            out["backdrop_url"] = url
        except Exception as e:
            print(f"  backdrop upload failed for {row['title']!r}: {e}")

    return out


def write_urls(db: Session, title_id, urls: dict) -> None:
    if not urls:
        return
    sets = ", ".join(f"{k} = :{k}" for k in urls.keys())
    db.execute(
        text(f"UPDATE titles SET {sets} WHERE title_id = :tid"),
        {**urls, "tid": title_id},
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Only process the first N titles (useful for test runs)")
    ap.add_argument("--force", action="store_true",
                    help="Re-download even if poster is already set")
    ap.add_argument("--no-backdrops", action="store_true",
                    help="Skip backdrops; posters only")
    args = ap.parse_args()

    # Ensure the schema has the Layer-4 media columns.
    engine = create_engine(DATABASE_URL)
    from app.models.title import Base, ensure_media_columns  # noqa: F401
    ensure_media_columns(engine)

    want_backdrops = not args.no_backdrops

    with Session(engine) as db, TmdbClient() as tmdb:
        rows = fetch_titles_needing_media(db, force=args.force, limit=args.limit)
        print(f"[sim_07] {len(rows)} titles to process "
              f"(force={args.force}, backdrops={want_backdrops})")

        done_poster = done_backdrop = 0
        start = time.time()

        for i, row in enumerate(rows, 1):
            urls = process_one(
                tmdb, row,
                want_poster=True,
                want_backdrop=want_backdrops,
                force=args.force,
            )
            if urls:
                write_urls(db, row["title_id"], urls)
                if "poster_url" in urls:   done_poster += 1
                if "backdrop_url" in urls: done_backdrop += 1

            if i % 25 == 0:
                db.commit()
                elapsed = time.time() - start
                rate = i / elapsed if elapsed else 0
                eta = (len(rows) - i) / rate if rate else 0
                print(f"  [{i}/{len(rows)}]  "
                      f"posters={done_poster}  backdrops={done_backdrop}  "
                      f"{rate:.1f}/s  ETA {eta/60:.1f}min")

        db.commit()
        elapsed = time.time() - start
        print(f"[sim_07] done in {elapsed/60:.1f}min — "
              f"posters={done_poster}, backdrops={done_backdrop}")


if __name__ == "__main__":
    main()
