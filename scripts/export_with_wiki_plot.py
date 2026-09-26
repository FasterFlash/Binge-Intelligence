"""
scripts/export_with_wiki_plot.py

Rebuild the export CSVs for the retag pass, with Wikipedia plot text
appended to the TMDB synopsis wherever we can find it.

Output:
  exports/to_tag_batch_001.csv, 002.csv, ...  (50 rows each)
  exports/wiki_fetch_report.csv               (which rows got wiki plot vs fell back)

Idempotent-ish: overwrites the export/ batch files on each run. Delete the
old `mapped/` outputs manually before starting the fresh retag session.

Run:
    python -m scripts.export_with_wiki_plot
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.title import ContentType, Title
from app.services.wikipedia_client import get_plot


EXPORT_DIR = ROOT / "exports"
BATCH_SIZE = 50
REPORT_PATH = EXPORT_DIR / "wiki_fetch_report.csv"

# Wikipedia plot text can be long. Truncate so a batch of 50 fits comfortably
# in a Gemini message.
MAX_PLOT_CHARS = 2500


def build_enriched_synopsis(tmdb_synopsis: str, wiki_plot: str | None) -> str:
    tmdb_synopsis = (tmdb_synopsis or "").strip()
    if not wiki_plot:
        return tmdb_synopsis
    plot = wiki_plot.strip()
    if len(plot) > MAX_PLOT_CHARS:
        plot = plot[:MAX_PLOT_CHARS].rsplit(" ", 1)[0] + "..."
    if not tmdb_synopsis:
        return f"[PLOT]\n{plot}"
    return f"{tmdb_synopsis}\n\n[PLOT]\n{plot}"


def export(session: Session) -> None:
    EXPORT_DIR.mkdir(exist_ok=True)

    # ALL rows -- this is the "retag everything" run
    rows = session.scalars(
        select(Title).order_by(Title.tmdb_id)
    ).all()

    if not rows:
        print("No rows in DB.")
        return

    print(f"Retagging pass: {len(rows)} rows to enrich + export")
    print(f"Wikipedia fetch is ~2 req/s -> expected ~{len(rows) / 120:.0f} minutes for fetch pass")

    # ----- Wikipedia fetch phase -----
    print("\n[phase 1] fetching Wikipedia plots...")

    enriched: list[dict] = []
    report_rows: list[dict] = []
    hits = 0
    misses = 0

    for i, r in enumerate(rows, 1):
        kind = "movie" if r.content_type == ContentType.MOVIE else "tv"
        plot, page = get_plot(r.title, r.release_year, kind)

        if plot:
            hits += 1
            source = "wikipedia"
        else:
            misses += 1
            source = "tmdb_only"

        enriched_synopsis = build_enriched_synopsis(r.synopsis, plot)
        # collapse newlines/carriage returns for CSV cleanliness
        enriched_synopsis = (
            enriched_synopsis.replace("\r", " ").replace("\n", " ").strip()
        )

        enriched.append(
            {
                "tmdb_id": r.tmdb_id,
                "title": r.title,
                "release_year": r.release_year,
                "synopsis": enriched_synopsis,
                "themes": "",
                "tones": "",
            }
        )
        report_rows.append(
            {
                "tmdb_id": r.tmdb_id,
                "title": r.title,
                "release_year": r.release_year,
                "kind": kind,
                "wiki_source": source,
                "wiki_page": page or "",
                "plot_chars": len(plot) if plot else 0,
            }
        )

        if i % 100 == 0:
            pct_hit = hits / (hits + misses) * 100
            print(f"  {i}/{len(rows)}  hits={hits} misses={misses}  ({pct_hit:.0f}% coverage)")

    total = hits + misses
    pct_hit = hits / total * 100 if total else 0
    print(f"\n[phase 1 done] wiki_hits={hits}  wiki_misses={misses}  ({pct_hit:.1f}% coverage)")

    # ----- Write batch CSVs -----
    print(f"\n[phase 2] writing batch CSVs (BATCH_SIZE={BATCH_SIZE})...")

    # clear any existing to_tag_batch_* files so we don't leave stale ones
    for old in EXPORT_DIR.glob("to_tag_batch_*.csv"):
        old.unlink()

    batch_num = 0
    for start in range(0, len(enriched), BATCH_SIZE):
        batch_num += 1
        chunk = enriched[start : start + BATCH_SIZE]
        path = EXPORT_DIR / f"to_tag_batch_{batch_num:03d}.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["tmdb_id", "title", "release_year", "synopsis", "themes", "tones"],
                quoting=csv.QUOTE_ALL,
            )
            writer.writeheader()
            writer.writerows(chunk)

    print(f"  wrote {batch_num} batch files to {EXPORT_DIR}/")

    # ----- Write report -----
    with REPORT_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["tmdb_id", "title", "release_year", "kind", "wiki_source", "wiki_page", "plot_chars"],
            quoting=csv.QUOTE_ALL,
        )
        writer.writeheader()
        writer.writerows(report_rows)

    print(f"  wrote {REPORT_PATH}")
    print(f"\nDONE. Ready for Gemini retag pass ({batch_num} batches).")


def main() -> None:
    engine = create_engine(DATABASE_URL)
    started = datetime.utcnow()
    with Session(engine) as session:
        export(session)
    print(f"Elapsed: {datetime.utcnow() - started}")


if __name__ == "__main__":
    main()