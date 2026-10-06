"""
scripts/sim_01_users.py

Stage 1 — generate 3000 users + their households.

Uses the archetype config to draw per-user parameters as noisy samples from
archetype priors. Deterministic via RANDOM_SEED.

WIPES prior users/households on rerun (this is a full-catalog generation,
not incremental).

Run:
    python -m scripts.sim_01_users
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.title import Base  # shared metadata
from app.models.user import (
    AgeBand,
    Archetype,
    Household,
    HouseholdType,
    User,
)
from app.sim.archetype_config import (
    AGE_BAND_ARCHETYPE_MIX,
    AGE_BAND_MIX,
    AGE_BAND_TO_AGE_RANGE,
    AGE_TO_LIFE_CONTEXT,
    ARCHETYPE_CONFIG,
    COUNTRY_MIX,
    COUPLE_VS_ROOMMATES,
    HOUSEHOLD_MIX,
    PROACTIVE_MSG_OPT_IN_RATE,
    SIGNUP_GROWTH_CURVE,
    maturity_ceiling,
)
from config.vocabularies import GENRES, THEMES, TONES


RANDOM_SEED = 42
TARGET_USERS = 3000


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------
def weighted_choice(rng: random.Random, weights_dict: dict):
    keys = list(weights_dict.keys())
    weights = list(weights_dict.values())
    return rng.choices(keys, weights=weights, k=1)[0]


def build_affinity_vector(
    rng: random.Random,
    vocab: list[str],
    strong: list[str],
    medium: list[str],
) -> dict[str, float]:
    """
    Build a per-user affinity vector over a vocab.
      - Strong tags: N(0.75, 0.10) clamped [0.5, 1.0]
      - Medium tags: N(0.50, 0.10) clamped [0.3, 0.8]
      - Baseline:    N(0.30, 0.10) clamped [0.05, 0.55]
    """
    strong_set = set(strong)
    medium_set = set(medium)
    result: dict[str, float] = {}
    for tag in vocab:
        if tag in strong_set:
            v = rng.gauss(0.75, 0.10)
            v = max(0.5, min(1.0, v))
        elif tag in medium_set:
            v = rng.gauss(0.50, 0.10)
            v = max(0.3, min(0.8, v))
        else:
            v = rng.gauss(0.30, 0.10)
            v = max(0.05, min(0.55, v))
        result[tag] = round(v, 3)
    return result


def build_sampler_affinity(rng: random.Random, vocab: list[str]) -> dict[str, float]:
    """Broad, high-variance affinity across the whole vocab."""
    return {
        tag: round(max(0.15, min(0.90, rng.gauss(0.55, 0.20))), 3)
        for tag in vocab
    }


def build_hour_profile(peak_start: int, peak_end: int) -> list[float]:
    """
    24-hour activity profile. peak_end may exceed 23 (wraps).
    Peak hours -> 1.0, adjacent -> 0.5, distant -> 0.05.
    """
    profile = [0.05] * 24
    for h in range(peak_start, peak_end + 1):
        profile[h % 24] = 1.0
    # taper: any 0.05 next to a 1.0 becomes 0.5
    tapered = profile[:]
    for h in range(24):
        if profile[h] == 1.0:
            continue
        prev = (h - 1) % 24
        nxt = (h + 1) % 24
        if profile[prev] == 1.0 or profile[nxt] == 1.0:
            tapered[h] = 0.5
    return tapered


def draw_signup_date(rng: random.Random, current_age: int) -> tuple[date, int]:
    """
    Signup date weighted by SIGNUP_GROWTH_CURVE, constrained so user was
    at least 5 at signup. Returns (signup_date, cohort_year).
    """
    min_year = max(2015, 2025 - (current_age - 5))
    valid = {y: w for y, w in SIGNUP_GROWTH_CURVE.items() if y >= min_year}
    if not valid:
        valid = {2025: 1.0}
    year = weighted_choice(rng, valid)
    day_of_year = rng.randint(0, 364)
    d = date(year, 1, 1) + timedelta(days=day_of_year)
    if d > date(2025, 12, 31):
        d = date(2025, 12, 31)
    return d, year


def draw_user_numeric_params(
    rng: random.Random, archetype: Archetype
) -> dict:
    """Sample all numeric parameters from archetype priors with noise."""
    cfg = ARCHETYPE_CONFIG[archetype]

    def noisy(val: float, sigma: float = 0.08, lo: float = 0.0, hi: float = 1.0) -> float:
        return round(max(lo, min(hi, val + rng.gauss(0, sigma))), 3)

    weekday = [
        round(max(0.0, min(1.0, v * rng.uniform(0.85, 1.15))), 3)
        for v in cfg["weekday_activity"]
    ]

    hour_profile = build_hour_profile(cfg["peak_hours"][0], cfg["peak_hours"][1])

    concurrent_capacity = rng.randint(*cfg["concurrent_capacity_range"])

    session_int_mean = max(
        0.5,
        cfg["session_intensity_mean"]
        + rng.gauss(0, cfg["session_intensity_std"] * 0.3),
    )
    session_int_std = cfg["session_intensity_std"] * rng.uniform(0.7, 1.3)

    return {
        "weekday_activity": weekday,
        "hour_of_day_profile": hour_profile,
        "session_intensity_mean": round(session_int_mean, 2),
        "session_intensity_std": round(session_int_std, 2),
        "mainstream_susceptibility": noisy(cfg["mainstream_susceptibility"]),
        "novelty_seeking": noisy(cfg["novelty_seeking"]),
        "completion_short": noisy(cfg["completion_short"]),
        "completion_long": noisy(cfg["completion_long"]),
        "concurrent_capacity": concurrent_capacity,
        "rewatch_tendency": noisy(cfg["rewatch_tendency"]),
        "mood_variance": noisy(cfg["mood_variance"]),
        "hot_release_trigger_prob": noisy(cfg["hot_release_trigger_prob"]),
        "movie_series_ratio": noisy(cfg["movie_series_ratio"], lo=0.05, hi=0.95),
        "subtitle_tolerance": noisy(cfg["subtitle_tolerance"]),
        "sleep_dropoff": cfg["sleep_dropoff"],
    }


def pick_archetype_for_band(rng: random.Random, band: AgeBand) -> Archetype:
    return weighted_choice(rng, AGE_BAND_ARCHETYPE_MIX[band])


def pick_age_in_band(rng: random.Random, band: AgeBand) -> int:
    lo, hi = AGE_BAND_TO_AGE_RANGE[band]
    return rng.randint(lo, hi)


def build_user(
    rng: random.Random,
    country: str,
    age_band: AgeBand | None = None,
    age: int | None = None,
    archetype: Archetype | None = None,
    household_id=None,
) -> User:
    """Draw one fully-parameterized user. Any provided override wins."""
    if age_band is None:
        age_band = weighted_choice(rng, AGE_BAND_MIX)
    if age is None:
        age = pick_age_in_band(rng, age_band)
    if archetype is None:
        archetype = pick_archetype_for_band(rng, age_band)

    signup_dt, cohort_year = draw_signup_date(rng, age)

    cfg = ARCHETYPE_CONFIG[archetype]
    if archetype == Archetype.SAMPLER:
        genre_aff = build_sampler_affinity(rng, GENRES)
        theme_aff = build_sampler_affinity(rng, THEMES)
        tone_aff = build_sampler_affinity(rng, TONES)
    else:
        genre_aff = build_affinity_vector(
            rng, GENRES, cfg.get("strong_genres", []), cfg.get("medium_genres", [])
        )
        theme_aff = build_affinity_vector(
            rng, THEMES, cfg.get("strong_themes", []), []
        )
        tone_aff = build_affinity_vector(
            rng, TONES, cfg.get("strong_tones", []), []
        )

    params = draw_user_numeric_params(rng, archetype)

    return User(
        household_id=household_id,
        age=age,
        age_band=age_band,
        country=country,
        signup_date=signup_dt,
        cohort_year=cohort_year,
        maturity_ceiling=maturity_ceiling(age),
        archetype=archetype,
        proactive_msg_enabled=(rng.random() < PROACTIVE_MSG_OPT_IN_RATE),
        life_context_pattern=AGE_TO_LIFE_CONTEXT[age_band],
        genre_affinity=genre_aff,
        theme_affinity=theme_aff,
        tone_affinity=tone_aff,
        **params,
    )


# ---------------------------------------------------------------------------
# Household construction — households cluster users by country
# ---------------------------------------------------------------------------
def generate(rng: random.Random) -> tuple[list[Household], list[User]]:
    households: list[Household] = []
    users: list[User] = []

    # Users per country
    country_counts = {c: int(round(TARGET_USERS * p)) for c, p in COUNTRY_MIX.items()}
    # Absorb rounding drift into US
    country_counts["US"] += TARGET_USERS - sum(country_counts.values())

    # Household bucket sizes
    n_solo_target = int(round(TARGET_USERS * HOUSEHOLD_MIX["solo"]))
    n_2p_users_target = int(round(TARGET_USERS * HOUSEHOLD_MIX["2_person"]))
    n_3p_users_target = TARGET_USERS - n_solo_target - n_2p_users_target
    n_2p_households = n_2p_users_target // 2
    n_3p_households = n_3p_users_target // 3
    # any remainder becomes solo
    n_solo = TARGET_USERS - (n_2p_households * 2) - (n_3p_households * 3)

    print(f"Target: {TARGET_USERS} users across")
    print(f"  Solo:            {n_solo}")
    print(f"  2-person hhs:    {n_2p_households} ({n_2p_households * 2} users)")
    print(f"  3-person hhs:    {n_3p_households} ({n_3p_households * 3} users)")

    country_remaining = dict(country_counts)

    def pick_country_with_slots(n_needed: int) -> str:
        remaining = {c: n for c, n in country_remaining.items() if n >= n_needed}
        if not remaining:
            # fallback: any country with any slots
            remaining = {c: n for c, n in country_remaining.items() if n > 0}
            if not remaining:
                return "US"
        return weighted_choice(rng, remaining)

    # 3-person households first (hardest constraint)
    for _ in range(n_3p_households):
        country = pick_country_with_slots(3)
        hh = Household(country=country, household_type=HouseholdType.FAMILY_WITH_KIDS)
        households.append(hh)

        adult1 = build_user(
            rng, country,
            age_band=rng.choice([AgeBand.ADULT, AgeBand.MIDDLE_AGED]),
            household_id=hh.household_id,
        )
        adult2 = build_user(
            rng, country,
            age_band=rng.choice([AgeBand.ADULT, AgeBand.MIDDLE_AGED]),
            household_id=hh.household_id,
        )
        kid = build_user(
            rng, country,
            age_band=rng.choice([AgeBand.KID, AgeBand.TEEN]),
            household_id=hh.household_id,
        )
        users.extend([adult1, adult2, kid])
        country_remaining[country] -= 3

    # 2-person households
    for _ in range(n_2p_households):
        country = pick_country_with_slots(2)
        is_couple = rng.random() < COUPLE_VS_ROOMMATES
        hh_type = HouseholdType.COUPLE if is_couple else HouseholdType.ROOMMATES
        hh = Household(country=country, household_type=hh_type)
        households.append(hh)

        bands = (
            [AgeBand.ADULT, AgeBand.MIDDLE_AGED]
            if is_couple
            else [AgeBand.YOUNG_ADULT, AgeBand.ADULT]
        )
        u1 = build_user(rng, country, age_band=rng.choice(bands), household_id=hh.household_id)
        u2 = build_user(rng, country, age_band=rng.choice(bands), household_id=hh.household_id)
        users.extend([u1, u2])
        country_remaining[country] -= 2

    # Solo users — fill remaining slots per country
    for country, remaining in country_remaining.items():
        for _ in range(remaining):
            users.append(build_user(rng, country))

    return households, users


def main() -> None:
    rng = random.Random(RANDOM_SEED)

    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        # Full wipe (Stage 1 is not incremental)
        session.execute(User.__table__.delete())
        session.execute(Household.__table__.delete())
        session.commit()

        households, users = generate(rng)

        print(f"\nInserting {len(households)} households and {len(users)} users...")
        session.add_all(households)
        session.commit()  # commit households first for FK integrity
        session.add_all(users)
        session.commit()

    # --- Validation report ---
    print("\n" + "=" * 60)
    print("STAGE 1 VALIDATION REPORT")
    print("=" * 60)

    total = len(users)
    print(f"Users:      {total}")
    print(f"Households: {len(households)}")

    print("\nCountry distribution:")
    cc = Counter(u.country for u in users)
    for c, n in sorted(cc.items(), key=lambda x: -x[1]):
        tgt = int(COUNTRY_MIX.get(c, 0) * total)
        print(f"  {c}: {n:4d}  (target ~{tgt})")

    print("\nArchetype distribution:")
    ac = Counter(u.archetype.value for u in users)
    for a, n in sorted(ac.items(), key=lambda x: -x[1]):
        print(f"  {a:20s}: {n:4d}  ({n/total:.1%})")

    print("\nAge band distribution:")
    ab = Counter(u.age_band.value for u in users)
    for b in ["7-12", "13-17", "18-24", "25-34", "35-49", "50+"]:
        n = ab.get(b, 0)
        tgt = int(AGE_BAND_MIX.get(AgeBand(b), 0) * total)
        print(f"  {b:8s}: {n:4d}  (target ~{tgt})")

    print("\nSignup cohort distribution:")
    cy = Counter(u.cohort_year for u in users)
    for year in sorted(cy.keys()):
        tgt = int(SIGNUP_GROWTH_CURVE.get(year, 0) * total)
        bar = "#" * (cy[year] // 10)
        print(f"  {year}: {cy[year]:4d}  (target ~{tgt})  {bar}")

    print("\nHousehold type distribution:")
    ht = Counter(h.household_type.value for h in households)
    for t, n in ht.items():
        print(f"  {t:20s}: {n}")

    print(f"\nProactive-msg opt-in: {sum(1 for u in users if u.proactive_msg_enabled)} "
          f"({sum(1 for u in users if u.proactive_msg_enabled)/total:.1%})")

    print("\nSample parameters:")
    for arch in [Archetype.BINGER, Archetype.SAMPLER, Archetype.EVENT_VIEWER,
                 Archetype.WEEKEND_WARRIOR, Archetype.COMFORT_REWATCHER,
                 Archetype.NIGHT_OWL, Archetype.CASUAL_TRICKLER]:
        u = next((x for x in users if x.archetype == arch), None)
        if not u:
            continue
        top_genres = sorted(u.genre_affinity.items(), key=lambda x: -x[1])[:3]
        top_tones = sorted(u.tone_affinity.items(), key=lambda x: -x[1])[:3]
        print(f"  [{arch.value}]")
        print(f"    age={u.age} country={u.country} cohort={u.cohort_year}")
        print(f"    session_intensity: {u.session_intensity_mean:.2f} ± {u.session_intensity_std:.2f}")
        print(f"    completion_long: {u.completion_long:.2f}  concurrent: {u.concurrent_capacity}")
        print(f"    mainstream: {u.mainstream_susceptibility:.2f}  novelty: {u.novelty_seeking:.2f}")
        print(f"    top genres: {[g for g, _ in top_genres]}")
        print(f"    top tones:  {[t for t, _ in top_tones]}")

    print("\nDONE.")


if __name__ == "__main__":
    main()