"""
Lifecycle rules for Stage 2 — subscription lifecycle constants and predicates.

Kept separate from archetype_config.py so archetype_config stays about the
users themselves, while lifecycle_rules is about the world (dates, holidays,
churn multipliers).
"""

from __future__ import annotations

from datetime import date

from app.models.user import Archetype


# ---------------------------------------------------------------------------
# Sim window
# ---------------------------------------------------------------------------
SIM_START = date(2015, 1, 1)
SIM_END = date(2025, 12, 31)


# ---------------------------------------------------------------------------
# Year-based churn multiplier (COVID retention 2020-21, streaming fatigue 2022+)
# ---------------------------------------------------------------------------
YEAR_CHURN_MULTIPLIER = {
    2015: 1.0,
    2016: 1.0,
    2017: 1.0,
    2018: 1.0,
    2019: 1.0,
    2020: 0.40,  # COVID lockdown, low churn
    2021: 0.50,
    2022: 1.50,  # streaming fatigue peak
    2023: 1.30,
    2024: 1.20,
    2025: 1.10,
}

STREAMING_FATIGUE_YEARS = {2022, 2023, 2024, 2025}


# ---------------------------------------------------------------------------
# Per-archetype base daily churn + reactivation rates.
# Calibrated so avg subscription periods per user roughly match Table A.
# ---------------------------------------------------------------------------
ARCHETYPE_BASE_CHURN = {
    Archetype.CASUAL_TRICKLER:  0.00035,
    Archetype.COMFORT_REWATCHER: 0.00040,
    Archetype.BINGER:            0.00055,
    Archetype.NIGHT_OWL:         0.00060,
    Archetype.WEEKEND_WARRIOR:   0.00065,
    Archetype.SAMPLER:           0.00095,
    Archetype.EVENT_VIEWER:      0.0028,
}

ARCHETYPE_BASE_REACTIVATION = {
    Archetype.CASUAL_TRICKLER:   0.0008,
    Archetype.COMFORT_REWATCHER: 0.0009,
    Archetype.BINGER:            0.0018,
    Archetype.NIGHT_OWL:         0.0015,
    Archetype.WEEKEND_WARRIOR:   0.0018,
    Archetype.SAMPLER:           0.0028,
    Archetype.EVENT_VIEWER:      0.0065,
}


# ---------------------------------------------------------------------------
# Honeymoon reduction on churn — the first 6 weeks are force-watch mode
# ---------------------------------------------------------------------------
HONEYMOON_DAYS = 42       # 6 weeks of strong retention
HONEYMOON_MULT = 0.30     # churn ~ 30% of baseline during honeymoon
POST_HONEYMOON_DAYS = 90  # 42-90 days: partial protection
POST_HONEYMOON_MULT = 0.60


# ---------------------------------------------------------------------------
# Hot-release detection
# ---------------------------------------------------------------------------
HOT_RELEASE_FAME_THRESHOLD = 0.60   # a "hot release" needs fame >= this
HOT_RELEASE_AFFINITY_THRESHOLD = 0.45  # user's affinity match must clear this
# scale factor: fame × affinity × user.hot_release_trigger_prob × this
HOT_RELEASE_ACTIVATION_SCALE = 0.35


# ---------------------------------------------------------------------------
# Holiday calendars (hardcoded, per country) — boost weeks for signup surges
# Values are (month, day_start, day_end) tuples. Whole days inclusive.
# ---------------------------------------------------------------------------
HOLIDAY_WINDOWS = {
    "US": [
        (12, 20, 31),  # Christmas / New Year
        (11, 22, 28),  # Thanksgiving week
        (7,   1,  7),  # July 4 week
    ],
    "IN": [
        (10, 20, 31),  # Diwali window (approximate)
        (11,  1,  5),  # Diwali overflow
        (12, 20, 31),  # Christmas / New Year
        (8,  13, 18),  # Independence Day
        (3,   5, 15),  # Holi (approximate)
    ],
    "KR": [
        (2,   5, 14),  # Seollal window
        (9,  25, 30),  # Chuseok
        (10,  1,  5),  # Chuseok overflow
        (12, 20, 31),  # Christmas / New Year
    ],
    "JP": [
        (12, 28, 31),  # New Year prep
        (1,   1,  3),  # New Year
        (4,  29, 30),  # Golden Week
        (5,   1,  5),  # Golden Week
        (8,  13, 16),  # Obon
    ],
    "GB": [
        (12, 20, 31),
        (4,   1,  5),  # Easter (approximate)
        (8,  25, 31),  # Summer bank holiday
    ],
    "FR": [
        (12, 20, 31),
        (7,  14, 20),  # Bastille Day week
        (8,   1, 15),  # August holidays
    ],
    "ES": [
        (12, 20, 31),
        (8,   1, 15),
        (4,   1, 10),  # Semana Santa
    ],
    "DE": [
        (12, 20, 31),
        (10,  1,  7),  # Oktoberfest
    ],
    "MX": [
        (12, 12, 31),  # Guadalupe + Christmas
        (11,  1,  3),  # Day of the Dead
    ],
    "BR": [
        (12, 20, 31),
        (2,  15, 25),  # Carnival window
    ],
}


def is_holiday_week(country: str, day: date) -> bool:
    windows = HOLIDAY_WINDOWS.get(country, HOLIDAY_WINDOWS["US"])
    for month, d_lo, d_hi in windows:
        if day.month == month and d_lo <= day.day <= d_hi:
            return True
    return False


# ---------------------------------------------------------------------------
# Student break months (global — northern hemisphere; simplification)
# ---------------------------------------------------------------------------
STUDENT_BREAK_MONTHS = {6, 7, 8, 12}  # summer + winter break


def is_student_break(day: date) -> bool:
    return day.month in STUDENT_BREAK_MONTHS


# ---------------------------------------------------------------------------
# Life context multipliers (churn side)
#   - Free periods reduce churn (user has time to keep watching)
#   - Busy periods increase churn (user drops out)
# ---------------------------------------------------------------------------
def churn_life_context_mult(life_context: str, country: str, day: date) -> float:
    """
    Returns a multiplier on churn probability based on the user's life context.
      <1.0 = churn less (they're in free time)
      >1.0 = churn more (they're busy / bored)
    """
    on_holiday = is_holiday_week(country, day)

    if life_context == "student":
        if is_student_break(day) or on_holiday:
            return 0.5      # summer/winter break, retention
        return 1.4          # school term, higher churn
    if life_context == "working_professional":
        if on_holiday:
            return 0.6
        return 1.0          # baseline
    if life_context == "retired":
        return 0.7          # consistent, low churn
    if life_context == "kid":
        return 0.9          # steady, parent-controlled
    return 1.0


# ---------------------------------------------------------------------------
# Life context multipliers (reactivation side)
#   - Free periods boost reactivation
# ---------------------------------------------------------------------------
def reactivation_life_context_mult(life_context: str, country: str, day: date) -> float:
    on_holiday = is_holiday_week(country, day)

    if life_context == "student":
        if is_student_break(day):
            return 4.0
        if on_holiday:
            return 3.0
        return 0.6          # school term, low re-engagement
    if life_context == "working_professional":
        if on_holiday:
            return 3.5
        return 1.0
    if life_context == "retired":
        return 1.2
    if life_context == "kid":
        return 1.3 if on_holiday else 1.0
    return 1.0


# ---------------------------------------------------------------------------
# Time-since-end factor — longer break = higher probability of return
# ---------------------------------------------------------------------------
def time_since_end_mult(days_since_end: int) -> float:
    if days_since_end < 30:
        return 0.5          # just churned; low chance of immediate return
    if days_since_end < 90:
        return 1.0
    if days_since_end < 365:
        return 1.3
    return 1.5              # long break, itching to come back