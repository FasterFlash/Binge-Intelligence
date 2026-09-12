"""
scripts/fetch_catalog.py

Fetch ~2000 movies + ~900 series from TMDB across a balanced region mix,
2015-2025, and insert into the `titles` table. Populates the FACTUAL layer
only. `themes`, `tones`, and simulation fields are left empty/default —
they are filled by subsequent scripts.

Idempotent: re-running skips titles already present (by tmdb_id).

Run:
    python -m scripts.fetch_catalog
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
import csv

# Allow running this file directly OR via -m from project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.title import (
    Base,
    ContentType,
    MaturityRating,
    SeriesStatus,
    Title,
)
from app.services.tmdb_client import get_detail, get_us_rating, iter_discover
from config.vocabularies import GENRES_SET

YEARS = list(range(2015, 2026))  # 2015-2025 inclusive
EXPORT_DIR = ROOT / "exports"
BATCH_SIZE = 50  # rows per Gemini batch file

# (region, movie_target, series_target). Sums: 2000 movies, 900 series.
REGION_TARGETS: list[tuple[str, int, int]] = [
    ("US", 900, 400),
    ("IN", 300, 135),
    ("GB", 200, 90),
    ("KR", 200, 90),
    ("JP", 200, 90),
    ("ES", 100, 45),
    ("FR", 100, 45),
    ("DE", 50, 22),
    ("MX", 25, 12),
    ("BR", 25, 11),
]


def map_us_rating(cert: str | None, kind: str) -> MaturityRating:
    """
    Map US content rating to our 4-bucket maturity scale.
    Fallback rule (no cert): defaults reasonably by content kind.
    """
    if cert is None or cert == "":
        return MaturityRating.R16 if kind == "movie" else MaturityRating.R16

    c = cert.strip().upper()

    # Movie ratings
    if c in {"G"}:
        return MaturityRating.R7
    if c in {"PG"}:
        return MaturityRating.R7
    if c in {"PG-13"}:
        return MaturityRating.R12
    if c in {"R"}:
        return MaturityRating.R16
    if c in {"NC-17"}:
        return MaturityRating.R18

    # TV ratings
    if c in {"TV-Y", "TV-Y7", "TV-G"}:
        return MaturityRating.R7
    if c in {"TV-PG"}:
        return MaturityRating.R12
    if c in {"TV-14"}:
        return MaturityRating.R12
    if c in {"TV-MA"}:
        return MaturityRating.R18

    return MaturityRating.R16  # unknown -> safe default


def parse_year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        return int(date_str[:4])
    except (ValueError, TypeError):
        return None

# TMDB genre-name -> our frozen vocab. Splits multi-tags, drops non-vocab ones.
GENRE_MAP: dict[str, list[str]] = {
    "Action": ["Action"],
    "Adventure": ["Adventure"],
    "Animation": ["Animation"],
    "Comedy": ["Comedy"],
    "Crime": ["Crime"],
    "Documentary": ["Documentary"],
    "Drama": ["Drama"],
    "Family": ["Family"],
    "Fantasy": ["Fantasy"],
    "History": ["History"],
    "Horror": ["Horror"],
    "Music": ["Music"],
    "Mystery": ["Mystery"],
    "Romance": ["Romance"],
    "Science Fiction": ["Science Fiction"],
    "Thriller": ["Thriller"],
    "War": ["War"],
    "Western": ["Western"],
    "TV Movie": [],
    "Action & Adventure": ["Action", "Adventure"],
    "Kids": ["Kids"],
    "Reality": ["Reality"],
    "Sci-Fi & Fantasy": ["Science Fiction", "Fantasy"],
    "War & Politics": ["War"],
    "News": [],
    "Talk": [],
    "Soap": [],
}


def map_tmdb_genres(tmdb_genres: list[dict]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for g in tmdb_genres or []:
        name = (g.get("name") or "").strip()
        mapped = GENRE_MAP.get(name, [])
        for vocab_tag in mapped:
            if vocab_tag not in seen and vocab_tag in GENRES_SET:
                seen.add(vocab_tag)
                out.append(vocab_tag)
    return out[:4]

def to_title_row(kind: str, discovery_item: dict, detail: dict, cert: str | None) -> Title | None:
    """Build one Title ORM object from TMDB responses. Return None if the
    row can't satisfy schema constraints (e.g. movie missing runtime)."""

    tmdb_id = detail.get("id")
    if not tmdb_id:
        return None

    if kind == "movie":
        title = detail.get("title") or ""
        runtime = detail.get("runtime")
        release_year = parse_year(detail.get("release_date"))
        episode_count = None
        season_count = None
        avg_ep_len = None
        series_status = None
        original_language = detail.get("original_language")
        # skip if the CHECK constraint would reject it
        if not title or not release_year or not runtime:
            return None
        content_type = ContentType.MOVIE
    else:  # tv/series
        title = detail.get("name") or ""
        runtime = None
        release_year = parse_year(detail.get("first_air_date"))
        episode_count = detail.get("number_of_episodes")
        season_count = detail.get("number_of_seasons")
        # TMDB gives `episode_run_time` as a list of typical runtimes
        run_times = detail.get("episode_run_time") or []
        avg_ep_len = int(sum(run_times) / len(run_times)) if run_times else None

        status_raw = (detail.get("status") or "").lower()
        if status_raw in {"returning series", "in production", "planned", "pilot"}:
            series_status = SeriesStatus.ONGOING
        elif status_raw == "canceled":
            series_status = SeriesStatus.CANCELLED
        else:
            series_status = SeriesStatus.ENDED

        original_language = detail.get("original_language")
        if not title or not release_year or not episode_count:
            return None
        content_type = ContentType.SERIES

    # country: use origin_country[0] if present, else the discovery region
    origin = detail.get("origin_country") or []
    country = origin[0] if origin else discovery_item.get("_region")

    synopsis = detail.get("overview") or ""

    return Title(
        tmdb_id=tmdb_id,
        title=title,
        content_type=content_type,
        release_year=release_year,
        runtime_minutes=runtime,
        episode_count=episode_count,
        season_count=season_count,
        avg_episode_length=avg_ep_len,
        series_status=series_status,
        country=country,
        original_language=original_language,
        tmdb_popularity=float(detail.get("popularity") or 0.0),
        tmdb_vote_count=int(detail.get("vote_count") or 0),
        synopsis=synopsis,
        genres=map_tmdb_genres(detail.get("genres") or []),
        themes=[],   # filled by Gemini pass
        tones=[],    # filled by Gemini pass
        maturity_rating=map_us_rating(cert, kind),
        fame=0.0,             # filled by sim-fields pass
        binge_factor=None,    # filled by sim-fields pass
        platform_add_date=date.today(),      # placeholder, sim-fields pass will randomize
        platform_leaving_date=None,
        is_platform_original=False,
    )


def fetch_and_insert(session: Session) -> None:
    engine_url = str(session.bind.url) if session.bind else "(unbound)"
    print(f"Target DB: {engine_url}")
    print(f"Years: {YEARS[0]}-{YEARS[-1]}")

    # pre-load existing tmdb_ids to make this idempotent
    existing_ids = set(session.scalars(select(Title.tmdb_id)).all())
    print(f"Existing rows in DB: {len(existing_ids)}")

    total_inserted = 0

    for region, movie_target, series_target in REGION_TARGETS:
        for kind, target in (("movie", movie_target), ("tv", series_target)):
            print(f"\n[{region}] {kind}: fetching up to {target}...")

            discovery = iter_discover(kind, region, YEARS, target_count=target)
            print(f"  discovered {len(discovery)} candidates")

            inserted_this_bucket = 0
            for i, item in enumerate(discovery, 1):
                tid = item.get("id")
                if not tid or tid in existing_ids:
                    continue

                try:
                    detail = get_detail(kind, tid)
                except Exception as e:
                    print(f"  detail fetch failed for {tid}: {e}")
                    continue

                cert = get_us_rating(kind, tid)
                row = to_title_row(kind, item, detail, cert)
                if row is None:
                    continue

                session.add(row)
                existing_ids.add(tid)
                inserted_this_bucket += 1
                total_inserted += 1

                if inserted_this_bucket % 25 == 0:
                    session.commit()
                    print(f"  {inserted_this_bucket}/{target} inserted (checkpoint)")

            session.commit()
            print(f"  [{region}] {kind}: {inserted_this_bucket} inserted")

    print(f"\nDONE. Total newly inserted: {total_inserted}")

def export_for_tagging(session: Session) -> None:
    """
    Emit CSV batches of untagged rows for the Gemini theme/tone pass.
    Each file = one batch you paste into one Gemini message.

    Columns exported (minimal, keeps context load low):
        tmdb_id, title, release_year, synopsis, themes, tones

    tmdb_id is the join key you'll use later to import tags back into Postgres.
    themes/tones columns are present but EMPTY — that's what Gemini fills.
    """
    EXPORT_DIR.mkdir(exist_ok=True)

    rows = session.scalars(
        select(Title).where(Title.themes == []).order_by(Title.tmdb_id)
    ).all()

    if not rows:
        print("No untagged rows to export.")
        return

    print(f"\nExporting {len(rows)} untagged rows in batches of {BATCH_SIZE}...")

    batch_num = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch_num += 1
        chunk = rows[start : start + BATCH_SIZE]
        path = EXPORT_DIR / f"to_tag_batch_{batch_num:03d}.csv"

        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["tmdb_id", "title", "release_year", "synopsis", "themes", "tones"])
            for r in chunk:
                # collapse any newlines in synopsis so one row = one CSV line
                synopsis_clean = (r.synopsis or "").replace("\n", " ").replace("\r", " ").strip()
                writer.writerow([r.tmdb_id, r.title, r.release_year, synopsis_clean, "", ""])

        print(f"  wrote {path.name} ({len(chunk)} rows)")

    print(f"\nDONE. {batch_num} batch files in {EXPORT_DIR}/")
    print("Paste each file into Gemini with the tagging prompt, save output to:")
    print(f"  {EXPORT_DIR}/tagged_batch_XXX.csv")

def main() -> None:
    engine = create_engine(DATABASE_URL)
    # ensure schema exists (safe if already there)
    Base.metadata.create_all(engine)
    started = datetime.utcnow()
    with Session(engine) as session:
        fetch_and_insert(session)
        export_for_tagging(session)
    print(f"Elapsed: {datetime.utcnow() - started}")


if __name__ == "__main__":
    main()