"""
scripts/sim_00_title_sim_fields.py

Stage 0 of the simulation pipeline: assign the simulation-controlled fields
on every title in the catalog.

Populates:
  - fame                   log-normalized 0-1 from (popularity * log(vote_count))
  - is_platform_original   ~12% of catalog, weighted by recency + fame
  - platform_add_date      respects release_year; originals drop same year
  - platform_leaving_date  15% of non-originals get one; originals never leave
  - binge_factor           series only; formula over genres/themes/tones

Deterministic via RANDOM_SEED. Idempotent: rerun to re-assign identically.

Run:
    python -m scripts.sim_00_title_sim_fields
"""

from __future__ import annotations

import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.title import ContentType, Title


RANDOM_SEED = 42

SIM_END = date(2025, 12, 31)
SIM_START = date(2015, 1, 1)

# Target fraction of catalog flagged as platform originals
ORIGINAL_TARGET_FRAC = 0.12
# Fraction of non-originals that get a leaving_date
LEAVING_FRAC = 0.15

# For "leaving soon" queries: some titles must leave in Q4 2025 / early 2026
LEAVING_SOON_WINDOW_START = date(2025, 10, 1)
LEAVING_SOON_WINDOW_END = date(2026, 3, 31)


# ---------------------------------------------------------------------------
# fame: log-scaled blend of popularity + vote_count, then min-max to [0,1]
# ---------------------------------------------------------------------------
def compute_fame_raw(t: Title) -> float:
    """Raw fame score BEFORE catalog-wide normalization."""
    pop = max(float(t.tmdb_popularity or 0.0), 0.0)
    votes = max(int(t.tmdb_vote_count or 0), 0)
    # popularity * log(votes) — a movie with high popularity but 5 votes
    # should not be considered famous
    raw = pop * math.log(votes + 1)
    return math.log(raw + 1.0)  # compress the tail


def normalize_fame(rows: list[Title]) -> dict[int, float]:
    """Min-max normalize raw fame across the whole catalog."""
    raws = [compute_fame_raw(t) for t in rows]
    lo, hi = min(raws), max(raws)
    span = hi - lo if hi > lo else 1.0
    return {t.tmdb_id: (raws[i] - lo) / span for i, t in enumerate(rows)}


# ---------------------------------------------------------------------------
# is_platform_original: recency + fame weighted, targets ~12% of catalog
# ---------------------------------------------------------------------------
def original_weight(t: Title, fame: float) -> float:
    """Unnormalized weight — turned into a probability by the scaler."""
    recency_bonus = 1.0
    if t.release_year >= 2020:
        recency_bonus = 1.8
    elif t.release_year >= 2016:
        recency_bonus = 1.0 + (t.release_year - 2016) * 0.15
    # fame bonus: 1.0 at fame=0.5, up to 1.5 at fame=1.0
    fame_bonus = 1.0 + max(0.0, fame - 0.5) * 1.0
    return recency_bonus * fame_bonus


def assign_originals(rows: list[Title], fame_by_id: dict[int, float], rng: random.Random) -> set[int]:
    """Return the set of tmdb_ids flagged as platform originals."""
    weights = [original_weight(t, fame_by_id[t.tmdb_id]) for t in rows]
    total_weight = sum(weights)
    n_target = int(round(len(rows) * ORIGINAL_TARGET_FRAC))

    # Sample without replacement, weighted
    # Use a rank-based approach with jittered scores for reproducibility
    scored = [(w * rng.random() ** (1.0 / max(w, 1e-6)), t.tmdb_id) for t, w in zip(rows, weights)]
    scored.sort(reverse=True)
    return {tid for _, tid in scored[:n_target]}


# ---------------------------------------------------------------------------
# platform_add_date: respects release_year; originals drop within release_year
# ---------------------------------------------------------------------------
def pick_add_date(t: Title, is_original: bool, rng: random.Random) -> date:
    release_start = date(t.release_year, 1, 1)
    release_end = date(t.release_year, 12, 31)

    if is_original:
        # originals drop within their release year (pick a random day)
        days_in_year = (release_end - release_start).days
        offset = rng.randint(0, days_in_year)
        candidate = release_start + timedelta(days=offset)
    else:
        # 1 month to 2 years after start of release_year
        offset_days = rng.randint(30, 730)
        candidate = release_start + timedelta(days=offset_days)

    # Never after our sim window end
    if candidate > SIM_END:
        candidate = SIM_END
    if candidate < SIM_START:
        candidate = SIM_START
    return candidate


# ---------------------------------------------------------------------------
# platform_leaving_date: 15% of non-originals; some in "leaving soon" window
# ---------------------------------------------------------------------------
def pick_leaving_date(
    t: Title, add_date: date, is_original: bool, rng: random.Random
) -> date | None:
    if is_original:
        return None
    if rng.random() > LEAVING_FRAC:
        return None

    # 40% of leaving-titles land in the "leaving soon" window (for demo queries)
    # the rest scatter across the sim window from their add_date onward
    if rng.random() < 0.40:
        lo = max(add_date + timedelta(days=180), LEAVING_SOON_WINDOW_START)
        hi = LEAVING_SOON_WINDOW_END
        if hi <= lo:
            return None
        span = (hi - lo).days
        return lo + timedelta(days=rng.randint(0, span))
    else:
        # somewhere between (add_date + 6 months) and (sim_end + 1 year)
        lo = add_date + timedelta(days=180)
        hi = SIM_END + timedelta(days=365)
        if hi <= lo:
            return None
        span = (hi - lo).days
        return lo + timedelta(days=rng.randint(0, span))


# ---------------------------------------------------------------------------
# binge_factor: series only, formula over tags
# ---------------------------------------------------------------------------
BINGE_GENRES_UP = {"Thriller", "Mystery", "Crime", "Drama"}
BINGE_GENRES_DOWN = {"Documentary", "Reality", "Family", "Kids"}
BINGE_THEMES_UP = {"whodunit", "conspiracy", "cat-and-mouse", "betrayal", "revenge", "chosen-one", "post-apocalyptic"}
BINGE_TONES_UP = {"tense", "suspenseful", "dark"}
BINGE_TONES_DOWN = {"slow-burn"}


def compute_binge_factor(t: Title) -> float | None:
    if t.content_type != ContentType.SERIES:
        return None

    score = 0.5
    if any(g in BINGE_GENRES_UP for g in (t.genres or [])):
        score += 0.3
    if any(g in BINGE_GENRES_DOWN for g in (t.genres or [])):
        score -= 0.2
    if any(th in BINGE_THEMES_UP for th in (t.themes or [])):
        score += 0.2
    if any(tn in BINGE_TONES_UP for tn in (t.tones or [])):
        score += 0.2
    if any(tn in BINGE_TONES_DOWN for tn in (t.tones or [])):
        score -= 0.1

    return max(0.10, min(0.95, score))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    rng = random.Random(RANDOM_SEED)

    engine = create_engine(DATABASE_URL)
    with Session(engine) as session:
        rows = session.scalars(select(Title).order_by(Title.tmdb_id)).all()
        print(f"Loaded {len(rows)} titles from DB")

        # 1) Fame — needs whole-catalog normalization
        print("\n[1/5] computing fame...")
        fame_by_id = normalize_fame(rows)
        for t in rows:
            t.fame = fame_by_id[t.tmdb_id]

        # 2) is_platform_original — weighted by recency + fame
        print("[2/5] flagging platform originals...")
        original_ids = assign_originals(rows, fame_by_id, rng)
        for t in rows:
            t.is_platform_original = t.tmdb_id in original_ids

        # 3) platform_add_date — respects release_year + originals drop same year
        print("[3/5] assigning platform_add_date...")
        for t in rows:
            t.platform_add_date = pick_add_date(t, t.is_platform_original, rng)

        # 4) platform_leaving_date
        print("[4/5] assigning platform_leaving_date...")
        for t in rows:
            t.platform_leaving_date = pick_leaving_date(
                t, t.platform_add_date, t.is_platform_original, rng
            )

        # 5) binge_factor — series only
        print("[5/5] computing binge_factor (series only)...")
        for t in rows:
            t.binge_factor = compute_binge_factor(t)

        session.commit()

    # ----- Print a compact validation report -----
    n_titles = len(rows)
    n_originals = sum(1 for t in rows if t.is_platform_original)
    n_leaving = sum(1 for t in rows if t.platform_leaving_date is not None)
    n_leaving_soon = sum(
        1 for t in rows
        if t.platform_leaving_date and LEAVING_SOON_WINDOW_START <= t.platform_leaving_date <= LEAVING_SOON_WINDOW_END
    )
    n_series = sum(1 for t in rows if t.content_type == ContentType.SERIES)

    fame_vals = [t.fame for t in rows]
    fame_avg = sum(fame_vals) / len(fame_vals)
    fame_hi = sum(1 for f in fame_vals if f > 0.75)
    fame_lo = sum(1 for f in fame_vals if f < 0.25)

    binge_vals = [t.binge_factor for t in rows if t.binge_factor is not None]
    binge_avg = sum(binge_vals) / len(binge_vals) if binge_vals else 0.0

    # add_date distribution across years
    add_year_counts: dict[int, int] = {}
    for t in rows:
        y = t.platform_add_date.year
        add_year_counts[y] = add_year_counts.get(y, 0) + 1

    print("\n" + "=" * 60)
    print("STAGE 0 VALIDATION REPORT")
    print("=" * 60)
    print(f"Total titles:                    {n_titles}")
    print(f"Series (binge_factor applies):   {n_series}")
    print(f"Movies:                          {n_titles - n_series}")
    print()
    print(f"fame:  mean={fame_avg:.3f}  hi(>0.75)={fame_hi}  lo(<0.25)={fame_lo}")
    print(f"binge_factor (series): mean={binge_avg:.3f}")
    print()
    print(f"Platform originals:              {n_originals}  ({n_originals/n_titles:.1%})")
    print(f"  (target ~{ORIGINAL_TARGET_FRAC:.0%})")
    print()
    print(f"Titles with leaving_date:        {n_leaving}  ({n_leaving/n_titles:.1%})")
    print(f"  (target ~{LEAVING_FRAC * (1 - ORIGINAL_TARGET_FRAC):.0%} of catalog)")
    print(f"Leaving soon (Q4 2025 - Q1 2026): {n_leaving_soon}")
    print()
    print("platform_add_date year distribution:")
    for y in sorted(add_year_counts.keys()):
        bar = "#" * (add_year_counts[y] // 15)
        print(f"  {y}: {add_year_counts[y]:4d}  {bar}")

    print("\nDONE.")


if __name__ == "__main__":
    main()