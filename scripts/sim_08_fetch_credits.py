"""
scripts/sim_08_fetch_credits.py

For every title in the catalog:
  1. Pull details + credits + keywords + external_ids from TMDB (one call via
     `append_to_response`)
  2. Upsert the top-N cast members and top crew (directors/writers/creators)
     into `people` / `title_cast` / `title_crew`
  3. Download headshots for new people into MinIO at cast/<person_id>.jpg
  4. Write tagline / imdb_id / tmdb_rating / keywords back onto `titles`

Idempotent + resumable:
  - skips titles that already have tagline populated unless --force
  - skips people headshots already in MinIO
  - uses UPSERT (ON CONFLICT DO NOTHING) on cast/crew associations

Usage:
    python -m scripts.sim_08_fetch_credits
    python -m scripts.sim_08_fetch_credits --limit 50       # test run
    python -m scripts.sim_08_fetch_credits --force          # re-fetch all
    python -m scripts.sim_08_fetch_credits --no-headshots   # metadata only
    python -m scripts.sim_08_fetch_credits --cast-top 10    # how many cast per title
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import DATABASE_URL, TMDB_IMAGE_BASE
from app.storage.minio_client import exists as minio_exists, public_url, put_bytes
from app.tmdb.client import TmdbClient, TmdbError

HEADSHOT_SIZE = "w185"
WANTED_CREW_JOBS = {
    "Director", "Writer", "Screenplay", "Creator", "Executive Producer",
    "Showrunner", "Original Story",
}


def fetch_titles_needing_credits(
    db: Session, force: bool, limit: int | None
) -> list[dict]:
    sql = """
        SELECT title_id, tmdb_id, content_type::text AS content_type,
               title, release_year
        FROM titles
    """
    if not force:
        sql += " WHERE tagline IS NULL AND imdb_id IS NULL"
    sql += " ORDER BY tmdb_popularity DESC NULLS LAST"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in db.execute(text(sql)).mappings().all()]


def upsert_person(db: Session, p: dict) -> str:
    """
    Insert the person if new; always return their internal person_id (UUID).
    Idempotent on tmdb_person_id.

    NOTE: `person_id` has a Python-side default on the ORM model, but raw
    `text(...)` inserts don't invoke that — we generate a UUID in Python
    here, then let ON CONFLICT return the pre-existing one when the
    tmdb_person_id is already seen. The RETURNING clause gives us the
    right ID either way.
    """
    tmdb_pid = p.get("id")
    if not tmdb_pid:
        return ""
    row = db.execute(
        text(
            """
            INSERT INTO people (person_id, tmdb_person_id, name,
                                known_for_department, popularity, created_at)
            VALUES (:person_id, :pid, :name, :dept, :pop, :created_at)
            ON CONFLICT (tmdb_person_id) DO UPDATE
              SET name = EXCLUDED.name,
                  popularity = EXCLUDED.popularity
            RETURNING person_id
            """
        ),
        {
            "person_id": str(uuid.uuid4()),
            "pid": tmdb_pid,
            "name": p.get("name") or "",
            "dept": p.get("known_for_department"),
            "pop": p.get("popularity") or 0.0,
            "created_at": datetime.utcnow(),
        },
    ).scalar_one()
    return str(row)


def insert_cast(
    db: Session, title_id, person_id: str, character: str, order: int
) -> None:
    db.execute(
        text(
            """
            INSERT INTO title_cast (cast_id, title_id, person_id,
                                    character_name, billing_order)
            VALUES (:cast_id, :tid, :pid, :ch, :ord)
            ON CONFLICT (title_id, person_id, character_name) DO NOTHING
            """
        ),
        {
            "cast_id": str(uuid.uuid4()),
            "tid": title_id, "pid": person_id,
            "ch": character or "", "ord": order,
        },
    )


def insert_crew(
    db: Session, title_id, person_id: str, job: str, department: str
) -> None:
    db.execute(
        text(
            """
            INSERT INTO title_crew (crew_id, title_id, person_id, job, department)
            VALUES (:crew_id, :tid, :pid, :job, :dept)
            ON CONFLICT (title_id, person_id, job) DO NOTHING
            """
        ),
        {
            "crew_id": str(uuid.uuid4()),
            "tid": title_id, "pid": person_id,
            "job": job, "dept": department,
        },
    )


def fetch_and_upload_headshot(
    tmdb: TmdbClient, person_id: str, profile_path: str | None
) -> str | None:
    """Download + upload a cast headshot. Returns public URL or None."""
    if not profile_path:
        return None
    key = f"cast/{person_id}.jpg"
    if minio_exists(key):
        return public_url(key)
    try:
        data = tmdb.download_image(profile_path, HEADSHOT_SIZE)
        return put_bytes(key, data, content_type="image/jpeg")
    except Exception as e:
        print(f"  headshot upload failed for {person_id}: {e}")
        return None


def process_one(
    db: Session, tmdb: TmdbClient, row: dict,
    *, cast_top: int, want_headshots: bool,
) -> dict:
    tmdb_id = row["tmdb_id"]
    content_type = row["content_type"]
    title_id = row["title_id"]

    # One API call: details + credits + keywords + external_ids
    try:
        rec = tmdb.details(
            tmdb_id, content_type,
            append="credits,keywords,external_ids",
        )
    except TmdbError as e:
        print(f"  tmdb error for {row['title']!r}: {e}")
        return {}
    if not rec:
        return {}

    # ---- Title-level fields ----
    tagline = rec.get("tagline") or ""
    tmdb_rating = rec.get("vote_average")
    external_ids = rec.get("external_ids") or {}
    imdb_id = external_ids.get("imdb_id")

    # Keywords (shape differs between movies and TV)
    kw_block = rec.get("keywords") or {}
    kw_list = kw_block.get("keywords") or kw_block.get("results") or []
    keywords = [k.get("name") for k in kw_list if k.get("name")]

    db.execute(
        text(
            """
            UPDATE titles
            SET tagline     = :tag,
                imdb_id     = :imdb,
                tmdb_rating = :rating,
                keywords    = :kw
            WHERE title_id = :tid
            """
        ),
        {
            "tag": tagline, "imdb": imdb_id,
            "rating": tmdb_rating, "kw": keywords,
            "tid": title_id,
        },
    )

    # ---- Credits ----
    credits = rec.get("credits") or {}
    cast = credits.get("cast") or []
    crew = credits.get("crew") or []

    counts = {"cast": 0, "crew": 0, "people_new": 0}

    # Cast — top N by billing order
    for actor in cast[:cast_top]:
        pid = upsert_person(db, actor)
        if not pid:
            continue
        insert_cast(
            db, title_id, pid,
            actor.get("character") or "",
            actor.get("order") or 999,
        )
        if want_headshots:
            fetch_and_upload_headshot(tmdb, pid, actor.get("profile_path"))
            # URL get written on first upload only; we re-UPDATE here so even
            # a resumed run eventually fills photo_url
            if actor.get("profile_path"):
                db.execute(
                    text(
                        "UPDATE people SET photo_url = :u WHERE person_id = :pid"
                    ),
                    {"u": public_url(f"cast/{pid}.jpg"), "pid": pid},
                )
        counts["cast"] += 1

    # Crew — only the key creative roles
    for member in crew:
        job = member.get("job") or ""
        if job not in WANTED_CREW_JOBS:
            continue
        pid = upsert_person(db, member)
        if not pid:
            continue
        insert_crew(
            db, title_id, pid, job,
            member.get("department") or "",
        )
        counts["crew"] += 1

    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Only process the first N titles")
    ap.add_argument("--force", action="store_true",
                    help="Re-fetch even if tagline/imdb_id already set")
    ap.add_argument("--no-headshots", action="store_true",
                    help="Skip cast headshot downloads (metadata only)")
    ap.add_argument("--cast-top", type=int, default=10,
                    help="How many cast members to store per title (billing order)")
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL)
    # Ensure Phase-B schema is in place
    from app.models.people import ensure_cast_tables
    ensure_cast_tables(engine)

    with Session(engine) as db, TmdbClient() as tmdb:
        rows = fetch_titles_needing_credits(db, force=args.force, limit=args.limit)
        print(f"[sim_08] {len(rows)} titles to process "
              f"(force={args.force}, headshots={not args.no_headshots}, "
              f"cast_top={args.cast_top})")

        total_cast = total_crew = 0
        start = time.time()

        for i, row in enumerate(rows, 1):
            counts = process_one(
                db, tmdb, row,
                cast_top=args.cast_top,
                want_headshots=not args.no_headshots,
            )
            total_cast += counts.get("cast", 0)
            total_crew += counts.get("crew", 0)

            if i % 20 == 0:
                db.commit()
                elapsed = time.time() - start
                rate = i / elapsed if elapsed else 0
                eta = (len(rows) - i) / rate if rate else 0
                print(f"  [{i}/{len(rows)}]  cast+={total_cast}  crew+={total_crew}  "
                      f"{rate:.1f}/s  ETA {eta/60:.1f}min")

        db.commit()
        elapsed = time.time() - start
        print(f"[sim_08] done in {elapsed/60:.1f}min — "
              f"cast rows={total_cast}, crew rows={total_crew}")


if __name__ == "__main__":
    main()
