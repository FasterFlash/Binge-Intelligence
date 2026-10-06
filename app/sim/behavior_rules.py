"""
Behavior rules — helpers used by Stage 3's sim loop.

Kept separate from the loop itself so tuning constants + math is inspectable
and the main sim script stays readable.
"""

from __future__ import annotations

import math
import random
from datetime import date

from app.models.behavior import DiscoverySource, SessionType
from app.models.user import Archetype
from app.sim.lifecycle_rules import (
    HONEYMOON_DAYS,
    HONEYMOON_MULT,
    is_holiday_week,
    is_student_break,
)


# ---------------------------------------------------------------------------
# Session type mix per archetype
# ---------------------------------------------------------------------------
SESSION_TYPE_MIX = {
    Archetype.BINGER: {
        SessionType.PLANNED_BINGE: 0.55,
        SessionType.TRICKLE: 0.20,
        SessionType.DISCOVERY_BROWSE: 0.15,
        SessionType.BACKGROUND: 0.05,
        SessionType.REWATCH: 0.05,
    },
    Archetype.CASUAL_TRICKLER: {
        SessionType.TRICKLE: 0.65,
        SessionType.DISCOVERY_BROWSE: 0.15,
        SessionType.PLANNED_BINGE: 0.10,
        SessionType.BACKGROUND: 0.05,
        SessionType.REWATCH: 0.05,
    },
    Archetype.SAMPLER: {
        SessionType.DISCOVERY_BROWSE: 0.50,
        SessionType.TRICKLE: 0.30,
        SessionType.PLANNED_BINGE: 0.15,
        SessionType.BACKGROUND: 0.03,
        SessionType.REWATCH: 0.02,
    },
    Archetype.COMFORT_REWATCHER: {
        SessionType.REWATCH: 0.55,
        SessionType.TRICKLE: 0.20,
        SessionType.BACKGROUND: 0.15,
        SessionType.DISCOVERY_BROWSE: 0.07,
        SessionType.PLANNED_BINGE: 0.03,
    },
    Archetype.WEEKEND_WARRIOR: {
        SessionType.PLANNED_BINGE: 0.40,
        SessionType.TRICKLE: 0.25,
        SessionType.DISCOVERY_BROWSE: 0.20,
        SessionType.BACKGROUND: 0.10,
        SessionType.REWATCH: 0.05,
    },
    Archetype.EVENT_VIEWER: {
        SessionType.PLANNED_BINGE: 0.45,
        SessionType.DISCOVERY_BROWSE: 0.30,
        SessionType.TRICKLE: 0.15,
        SessionType.BACKGROUND: 0.05,
        SessionType.REWATCH: 0.05,
    },
    Archetype.NIGHT_OWL: {
        SessionType.PLANNED_BINGE: 0.30,
        SessionType.BACKGROUND: 0.25,
        SessionType.TRICKLE: 0.20,
        SessionType.DISCOVERY_BROWSE: 0.15,
        SessionType.REWATCH: 0.10,
    },
}


def pick_session_type(archetype: Archetype, rng: random.Random) -> SessionType:
    mix = SESSION_TYPE_MIX[archetype]
    return rng.choices(list(mix.keys()), weights=list(mix.values()), k=1)[0]


# ---------------------------------------------------------------------------
# Daily activity multiplier — combines life-context + seasonal + honeymoon
# ---------------------------------------------------------------------------
def daily_activity_multiplier(
    user_life_context: str,
    country: str,
    day: date,
    days_in_period: int,
) -> float:
    """
    Returns a multiplier on the user's weekday_activity[dow] baseline.
    Ranges roughly 0.5 to 3.0.
    """
    mult = 1.0

    # Honeymoon boost
    if days_in_period < HONEYMOON_DAYS:
        mult *= 2.0

    # Life context
    on_holiday = is_holiday_week(country, day)
    if user_life_context == "student":
        if is_student_break(day) or on_holiday:
            mult *= 1.8
        else:
            mult *= 0.7
    elif user_life_context == "working_professional":
        if on_holiday:
            mult *= 1.6
    elif user_life_context == "retired":
        mult *= 1.1

    # Winter months slight boost (Nov-Feb) — indoor season
    if day.month in (11, 12, 1, 2):
        mult *= 1.1
    elif day.month in (6, 7):
        mult *= 0.9  # summer dip

    return mult


def n_sessions_today(activity_prob: float, rng: random.Random) -> int:
    """
    How many sessions on an active day. Mostly 1, occasionally 2, rarely 3.
    activity_prob is already-scaled 0-1+.
    """
    if activity_prob < 0.5:
        return 1
    r = rng.random()
    if r < 0.15 and activity_prob > 0.9:
        return 3
    if r < 0.35:
        return 2
    return 1


# ---------------------------------------------------------------------------
# Session intensity — how many episodes/movies in this session
# ---------------------------------------------------------------------------
def sample_session_intensity(
    session_type: SessionType,
    user_mean: float,
    user_std: float,
    is_weekend: bool,
    rng: random.Random,
) -> int:
    """Sample how many pieces of content in this session."""
    if session_type == SessionType.PLANNED_BINGE:
        base = user_mean * 1.3
    elif session_type == SessionType.TRICKLE:
        base = min(user_mean, 2.0)
    elif session_type == SessionType.BACKGROUND:
        base = user_mean * 0.8
    elif session_type == SessionType.DISCOVERY_BROWSE:
        base = 1.5
    else:  # REWATCH
        base = user_mean

    if is_weekend:
        base *= 1.2

    val = base + rng.gauss(0, user_std)
    return max(1, min(10, round(val)))


# ---------------------------------------------------------------------------
# Continue-vs-new decision
# ---------------------------------------------------------------------------
def prob_continue_in_progress(
    session_type: SessionType, in_progress_count: int
) -> float:
    """
    Given an active in-progress set, probability that this session continues
    an existing series (vs starting new or picking a movie).
    """
    if in_progress_count == 0:
        return 0.0
    if session_type == SessionType.DISCOVERY_BROWSE:
        return 0.10
    if session_type == SessionType.PLANNED_BINGE:
        return 0.75
    if session_type == SessionType.REWATCH:
        return 0.30  # sometimes rewatchers pick from in-progress
    return 0.55  # trickle, background


# ---------------------------------------------------------------------------
# Completion sampler (with sleep dropoff for Night Owls)
# ---------------------------------------------------------------------------
def sample_completion_pct(
    base_completion: float,
    episode_index_in_session: int,
    hour_started: int,
    sleep_dropoff: bool,
    rng: random.Random,
) -> float:
    """
    Sample completion_pct for one watch event.
      - base_completion is user.completion_short (movies) or 0.85 (episodes)
      - sleep_dropoff kicks in for Night Owls after 22:00, worse deeper in session
    """
    val = base_completion + rng.gauss(0, 0.08)

    if sleep_dropoff:
        late_hour = hour_started + episode_index_in_session
        if late_hour >= 24 or (hour_started >= 22 and episode_index_in_session >= 2):
            drop = 0.15 * (episode_index_in_session - 1)
            val -= drop

    return max(0.05, min(1.0, val))


# ---------------------------------------------------------------------------
# Discovery source classifier
# ---------------------------------------------------------------------------
def classify_discovery(
    is_continue: bool,
    is_rewatch: bool,
    days_since_add: int,
    fame: float,
) -> DiscoverySource:
    if is_continue:
        return DiscoverySource.CONTINUE_WATCHING
    if is_rewatch:
        return DiscoverySource.OTHER
    if days_since_add <= 30 and fame >= 0.7:
        return DiscoverySource.NEW_RELEASE
    if fame >= 0.5:
        return DiscoverySource.RECOMMENDATION
    return DiscoverySource.SEARCH


# ---------------------------------------------------------------------------
# Novelty bonus — recently-added titles get a bonus scaled by user's
# novelty_seeking. Returns a per-title multiplier in [1.0, 1+novelty_seeking].
# ---------------------------------------------------------------------------
def novelty_bonus_scalar(days_since_add: int, novelty_seeking: float) -> float:
    """Scalar version — for one title. Numpy vectorized version in sim script."""
    if days_since_add <= 30:
        return 1.0 + novelty_seeking * 0.5
    if days_since_add <= 90:
        return 1.0 + novelty_seeking * 0.25
    if days_since_add <= 365:
        return 1.0 + novelty_seeking * 0.1
    return 1.0