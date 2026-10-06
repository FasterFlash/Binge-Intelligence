"""
scripts/import_tags.py

Read Gemini-tagged CSV batches from ./mapped/ and update titles.themes and
titles.tones by tmdb_id.

Expected CSV format (from Gemini's output):
    title_id,tone,theme
    12345,"heartwarming, feel-good","coming-of-age, found-family"

- title_id in the CSV maps to titles.tmdb_id in the DB.
- Multi-value fields are COMMA-separated with optional whitespace.
- Every row is validated against the frozen vocab. Rejects go to a report
  file and do NOT touch the DB — no partial writes, no silent drift.

Run:
    python -m scripts.import_tags
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
from app.models.title import Title
from config.vocabularies import THEMES_SET, TONES_SET, validate_tags


MAPPED_DIR = ROOT / "mapped"
REPORT_PATH = ROOT / "exports" / "import_rejects.csv"


def split_multi(value: str) -> list[str]:
    """Comma-separated -> clean list. Strips whitespace, drops empties."""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def load_all_rows() -> list[tuple[str, dict]]:
    """Load every row from every CSV in mapped/. Returns [(source_file, row_dict), ...]."""
    if not MAPPED_DIR.exists():
        raise FileNotFoundError(f"{MAPPED_DIR} does not exist")

    files = sorted(MAPPED_DIR.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No .csv files found in {MAPPED_DIR}")

    all_rows: list[tuple[str, dict]] = []
    for path in files:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                all_rows.append((path.name, row))
    print(f"Loaded {len(all_rows)} rows from {len(files)} file(s) in {MAPPED_DIR}/")
    return all_rows


def import_tags(session: Session) -> None:
    all_rows = load_all_rows()

    # Preload existing tmdb_ids -> title_id mapping to fail-fast on unknowns
    known_tmdb_ids = set(session.scalars(select(Title.tmdb_id)).all())
    print(f"DB has {len(known_tmdb_ids)} titles.")

    updated = 0
    rejected: list[dict] = []
    unknown_id = 0
    duplicate_in_csv: set[int] = set()
    seen_ids: set[int] = set()

    for source_file, row in all_rows:
        raw_id = (row.get("title_id") or "").strip()
        if not raw_id:
            rejected.append(
                {"source": source_file, "tmdb_id": "", "reason": "missing title_id", "raw": str(row)}
            )
            continue

        try:
            tmdb_id = int(raw_id)
        except ValueError:
            rejected.append(
                {"source": source_file, "tmdb_id": raw_id, "reason": "title_id not integer", "raw": str(row)}
            )
            continue

        if tmdb_id not in known_tmdb_ids:
            unknown_id += 1
            rejected.append(
                {"source": source_file, "tmdb_id": tmdb_id, "reason": "tmdb_id not in DB", "raw": str(row)}
            )
            continue

        if tmdb_id in seen_ids:
            duplicate_in_csv.add(tmdb_id)
            rejected.append(
                {"source": source_file, "tmdb_id": tmdb_id, "reason": "duplicate tmdb_id across CSVs", "raw": str(row)}
            )
            continue
        seen_ids.add(tmdb_id)

        themes = split_multi(row.get("theme") or "")
        tones = split_multi(row.get("tone") or "")

        # dedupe within a row (Gemini sometimes repeats)
        themes = list(dict.fromkeys(themes))
        tones = list(dict.fromkeys(tones))

        # find out which specific tags are off-vocab before hitting the validator,
        # so the reject report is actionable
        bad_themes = [t for t in themes if t not in THEMES_SET]
        bad_tones = [t for t in tones if t not in TONES_SET]

        if bad_themes or bad_tones:
            rejected.append(
                {
                    "source": source_file,
                    "tmdb_id": tmdb_id,
                    "reason": f"off-vocab themes={bad_themes} tones={bad_tones}",
                    "raw": str(row),
                }
            )
            continue

        try:
            validate_tags(themes=themes, tones=tones)
        except ValueError as e:
            # e.g. count out of cap
            rejected.append(
                {"source": source_file, "tmdb_id": tmdb_id, "reason": str(e), "raw": str(row)}
            )
            continue

        # single UPDATE per row via the ORM
        title_row = session.scalars(
            select(Title).where(Title.tmdb_id == tmdb_id)
        ).one()
        title_row.themes = themes
        title_row.tones = tones
        updated += 1

        if updated % 200 == 0:
            session.commit()
            print(f"  {updated} updated (checkpoint)")

    session.commit()

    # Reject report
    REPORT_PATH.parent.mkdir(exist_ok=True)
    with REPORT_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "tmdb_id", "reason", "raw"])
        writer.writeheader()
        writer.writerows(rejected)

    print("\n=== IMPORT SUMMARY ===")
    print(f"Updated:                    {updated}")
    print(f"Rejected:                   {len(rejected)}")
    print(f"  - unknown tmdb_id:        {unknown_id}")
    print(f"  - duplicate in CSVs:      {len(duplicate_in_csv)}")
    print(f"  - off-vocab / bad count:  {len(rejected) - unknown_id - len(duplicate_in_csv)}")
    print(f"Reject report:              {REPORT_PATH}")


def main() -> None:
    engine = create_engine(DATABASE_URL)
    started = datetime.utcnow()
    with Session(engine) as session:
        import_tags(session)
    print(f"Elapsed: {datetime.utcnow() - started}")


if __name__ == "__main__":
    main()