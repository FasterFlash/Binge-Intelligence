"""
scripts/sim_02_lifecycle.py

Stage 2 — generate every user's subscription lifecycle across 2015-2025.

For each user, iterates day-by-day from signup_date to SIM_END and emits
subscription_periods rows. Each day:
  - if subscribed:   maybe churn (modulated by year, honeymoon, life context)
  - if unsubscribed: maybe reactivate (hot release + affinity, holiday, random)

Hot-release-driven reactivations use fame + affinity match to attribute
which specific title pulled a user back in — powers "how many signups did
title X drive?" analytics queries.

Deterministic via RANDOM_SEED.

Run:
    python -m scripts.sim_02_lifecycle
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.title import Base, Title
from app.models.subscription import EndReason, SubscriptionPeriod, TriggerReason
from app.models.user import User
from app.sim.lifecycle_rules import (
    ARCHETYPE_BASE_CHURN,
    ARCHETYPE_BASE_REACTIVATION,
    HONEYMOON_DAYS,
    HONEYMOON_MULT,
    HOT_RELEASE_ACTIVATION_SCALE,
    HOT_RELEASE_AFFINITY_THRESHOLD,
    HOT_RELEASE_FAME_THRESHOLD,
    POST_HONEYMOON_DAYS,
    POST_HONEYMOON_MULT,
    SIM_END,
    STREAMING_FATIGUE_YEARS,
    YEAR_CHURN_MULTIPLIER,
    churn_life_context_mult,
    is_holiday_week,
    is_student_break,
    reactivation_life_context_mult,
    time_since_end_mult,
)


RANDOM_SEED = 42


# ---------------------------------------------------------------------------
# Hot-release calendar precompute
# ---------------------------------------------------------------------------
def build_hot_release_calendar(titles: list[Title]) -> dict[date, list[dict]]:
    """
    Returns {date: [ {title_id, fame, genres, themes, tones}, ... ]}
    for every title with fame >= HOT_RELEASE_FAME_THRESHOLD.
    """
    calendar: dict[date, list[dict]] = {}
    for t in titles:
        if t.fame is None or t.fame < HOT_RELEASE_FAME_THRESHOLD:
            continue
        if t.platform_add_date is None:
            continue
        calendar.setdefault(t.platform_add_date, []).append(
            {
                "title_id": t.title_id,
                "fame": float(t.fame),
                "genres": t.genres or [],
                "themes": t.themes or [],
                "tones": t.tones or [],
            }
        )
    return calendar


def compute_affinity_match(user: User, rel: dict) -> float:
    """Blended genre/theme/tone match score for a user × title, 0..1."""
    def avg(vec, keys):
        if not keys:
            return 0.30
        return sum(vec.get(k, 0.30) for k in keys) / len(keys)

    g = avg(user.genre_affinity, rel["genres"])
    t = avg(user.theme_affinity, rel["themes"])
    tn = avg(user.tone_affinity, rel["tones"])
    return 0.4 * g + 0.3 * t + 0.3 * tn


# ---------------------------------------------------------------------------
# Daily churn / reactivation probabilities
# ---------------------------------------------------------------------------
def churn_probability(user: User, day: date, days_in_period: int) -> float:
    base = ARCHETYPE_BASE_CHURN[user.archetype]
    year_mult = YEAR_CHURN_MULTIPLIER.get(day.year, 1.0)

    # Honeymoon protection
    if days_in_period < HONEYMOON_DAYS:
        honeymoon_mult = HONEYMOON_MULT
    elif days_in_period < POST_HONEYMOON_DAYS:
        honeymoon_mult = POST_HONEYMOON_MULT
    else:
        honeymoon_mult = 1.0

    life_mult = churn_life_context_mult(
        user.life_context_pattern.value, user.country, day
    )

    return base * year_mult * honeymoon_mult * life_mult


def pick_churn_reason(day: date) -> EndReason:
    if day.year in STREAMING_FATIGUE_YEARS:
        # 50/50 between streaming fatigue and lack of use
        return (
            EndReason.CHURN_STREAMING_FATIGUE
            if day.timetuple().tm_yday % 2 == 0
            else EndReason.CHURN_LACK_OF_USE
        )
    return EndReason.CHURN_LACK_OF_USE


# ---------------------------------------------------------------------------
# Reactivation — either hot-release-triggered OR ambient
# ---------------------------------------------------------------------------
def try_hot_release_trigger(
    user: User,
    day: date,
    hot_releases: dict[date, list[dict]],
    rng: random.Random,
):
    """
    Returns (title_id, TriggerReason.HOT_RELEASE) if triggered, else None.
    Only fires if TODAY has a matching hot release AND user's fame-scaled
    affinity match clears the probability roll.
    """
    releases = hot_releases.get(day)
    if not releases:
        return None
    for rel in releases:
        aff = compute_affinity_match(user, rel)
        if aff < HOT_RELEASE_AFFINITY_THRESHOLD:
            continue
        prob = (
            rel["fame"] * aff * user.hot_release_trigger_prob * HOT_RELEASE_ACTIVATION_SCALE
        )
        if rng.random() < prob:
            return rel["title_id"], TriggerReason.HOT_RELEASE
    return None


def try_ambient_reactivation(
    user: User, day: date, days_since_end: int, rng: random.Random
):
    """
    Returns (None, TriggerReason) if triggered ambiently (no hot release).
    """
    base = ARCHETYPE_BASE_REACTIVATION[user.archetype]
    life_mult = reactivation_life_context_mult(
        user.life_context_pattern.value, user.country, day
    )
    time_mult = time_since_end_mult(days_since_end)

    prob = base * life_mult * time_mult
    if rng.random() >= prob:
        return None

    # Attribute a specific trigger reason
    if is_holiday_week(user.country, day):
        return None, TriggerReason.LIFE_HOLIDAY
    if user.life_context_pattern.value == "student" and is_student_break(day):
        return None, TriggerReason.LIFE_HOLIDAY
    if days_since_end >= 180:
        return None, TriggerReason.RETURN_FROM_BREAK
    if life_mult > 1.5:
        return None, TriggerReason.LIFE_BOREDOM
    return None, TriggerReason.RANDOM


# ---------------------------------------------------------------------------
# Per-user lifecycle loop
# ---------------------------------------------------------------------------
def run_user_lifecycle(
    user: User,
    hot_releases: dict[date, list[dict]],
    rng: random.Random,
) -> list[SubscriptionPeriod]:
    periods: list[SubscriptionPeriod] = []
    period_num = 1

    # State
    current_start = user.signup_date
    current_trigger = TriggerReason.INITIAL_SIGNUP
    current_trigger_title = None
    is_subscribed = True
    days_in_period = 0
    days_since_end = 0

    day = user.signup_date
    while day <= SIM_END:
        if is_subscribed:
            days_in_period += 1
            if rng.random() < churn_probability(user, day, days_in_period):
                # end the period today
                end_reason = pick_churn_reason(day)
                periods.append(
                    SubscriptionPeriod(
                        user_id=user.user_id,
                        period_num=period_num,
                        started_on=current_start,
                        ended_on=day,
                        days_active=(day - current_start).days,
                        trigger_reason=current_trigger,
                        trigger_title_id=current_trigger_title,
                        end_reason=end_reason,
                    )
                )
                period_num += 1
                is_subscribed = False
                days_since_end = 0
        else:
            days_since_end += 1
            trig = try_hot_release_trigger(user, day, hot_releases, rng)
            if trig is None:
                trig = try_ambient_reactivation(user, day, days_since_end, rng)

            if trig is not None:
                title_id_maybe, trigger_reason = trig
                current_start = day
                current_trigger = trigger_reason
                current_trigger_title = title_id_maybe
                is_subscribed = True
                days_in_period = 0

        day += timedelta(days=1)

    # If subscribed at sim end, emit final open period
    if is_subscribed:
        periods.append(
            SubscriptionPeriod(
                user_id=user.user_id,
                period_num=period_num,
                started_on=current_start,
                ended_on=None,
                days_active=(SIM_END - current_start).days,
                trigger_reason=current_trigger,
                trigger_title_id=current_trigger_title,
                end_reason=EndReason.STILL_ACTIVE,
            )
        )

    return periods


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    rng = random.Random(RANDOM_SEED)

    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        # Wipe any prior lifecycle rows
        session.execute(SubscriptionPeriod.__table__.delete())
        session.commit()

        print("Loading users + titles...")
        users = session.scalars(select(User)).all()
        titles = session.scalars(select(Title)).all()
        print(f"  {len(users)} users, {len(titles)} titles")

        hot_releases = build_hot_release_calendar(titles)
        total_hot = sum(len(v) for v in hot_releases.values())
        print(
            f"  hot-release calendar: {total_hot} title-releases across "
            f"{len(hot_releases)} distinct days (fame >= {HOT_RELEASE_FAME_THRESHOLD})"
        )

        print("\nRunning lifecycle sim...")
        all_periods: list[SubscriptionPeriod] = []
        for i, user in enumerate(users, 1):
            periods = run_user_lifecycle(user, hot_releases, rng)
            all_periods.extend(periods)
            if i % 500 == 0:
                print(f"  {i}/{len(users)} users processed  ({len(all_periods)} periods so far)")

        print(f"\nInserting {len(all_periods)} subscription periods...")
        # Bulk insert in chunks to avoid one giant transaction
        CHUNK = 2000
        for k in range(0, len(all_periods), CHUNK):
            session.add_all(all_periods[k : k + CHUNK])
            session.commit()

    # --- Validation report ---
    print("\n" + "=" * 60)
    print("STAGE 2 VALIDATION REPORT")
    print("=" * 60)

    total = len(all_periods)
    per_user = total / len(users)
    print(f"Total periods:               {total}")
    print(f"Periods per user (avg):      {per_user:.2f}")

    # Per-archetype avg period count
    per_arch = Counter((p.user_id, ) for p in all_periods)
    # simpler: compute periods per user grouped by archetype
    user_arch_map = {u.user_id: u.archetype.value for u in users}
    arch_period_counts: dict[str, list[int]] = {}
    period_by_user: dict = Counter(p.user_id for p in all_periods)
    for uid, cnt in period_by_user.items():
        arch = user_arch_map.get(uid, "?")
        arch_period_counts.setdefault(arch, []).append(cnt)

    print("\nAvg periods per user by archetype:")
    for arch, counts in sorted(arch_period_counts.items(), key=lambda x: -sum(x[1]) / len(x[1])):
        avg = sum(counts) / len(counts)
        print(f"  {arch:22s}: {avg:.2f}   (n={len(counts)})")

    print("\nTrigger reason distribution:")
    tr = Counter(p.trigger_reason.value for p in all_periods)
    for reason, cnt in sorted(tr.items(), key=lambda x: -x[1]):
        print(f"  {reason:22s}: {cnt:5d}  ({cnt/total:.1%})")

    print("\nEnd reason distribution:")
    er = Counter(p.end_reason.value for p in all_periods)
    for reason, cnt in sorted(er.items(), key=lambda x: -x[1]):
        print(f"  {reason:24s}: {cnt:5d}  ({cnt/total:.1%})")

    # Currently active periods at sim end
    active_now = sum(1 for p in all_periods if p.ended_on is None)
    print(f"\nActive subscriptions at 2025-12-31: {active_now}  ({active_now/len(users):.1%} of users)")

    # Cohort effect check: 2020 signups should have longer avg first-period lengths
    print("\nAvg first-period length by cohort year:")
    first_periods_by_cohort: dict[int, list[int]] = {}
    user_cohort_map = {u.user_id: u.cohort_year for u in users}
    for p in all_periods:
        if p.period_num != 1:
            continue
        cohort = user_cohort_map.get(p.user_id)
        if cohort is None:
            continue
        first_periods_by_cohort.setdefault(cohort, []).append(p.days_active)
    for year in sorted(first_periods_by_cohort.keys()):
        days = first_periods_by_cohort[year]
        avg_days = sum(days) / len(days)
        print(f"  {year}: {avg_days:6.1f} days  (n={len(days)})")

    # Hot-release effectiveness
    hot_periods = [p for p in all_periods if p.trigger_reason == TriggerReason.HOT_RELEASE]
    if hot_periods:
        top_titles = Counter(p.trigger_title_id for p in hot_periods).most_common(10)
        title_lookup = {t.title_id: t.title for t in titles}
        print(f"\nTop 10 titles by hot-release-driven signups (of {len(hot_periods)} total):")
        for tid, cnt in top_titles:
            print(f"  {cnt:4d}  {title_lookup.get(tid, str(tid))[:60]}")

    print("\nDONE.")


if __name__ == "__main__":
    main()