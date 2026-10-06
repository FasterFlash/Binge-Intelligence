"""
Archetype parameter definitions — the numbers behind each of the 7 archetypes.

Per-user parameters are drawn as noisy samples from these priors, so no two
users in the same archetype behave identically.

Tables A–J from the design conversation are encoded here.
"""

from app.models.user import AgeBand, Archetype, LifeContextPattern


# ---------------------------------------------------------------------------
# Table A + B/C/D — Archetype numeric parameters + affinity priors
# ---------------------------------------------------------------------------
ARCHETYPE_CONFIG = {
    Archetype.BINGER: {
        # --- Behavioral rhythm ---
        "session_intensity_mean": 5.0,
        "session_intensity_std": 2.0,
        "weekday_activity": [0.4, 0.4, 0.4, 0.5, 0.8, 0.9, 0.8],
        "peak_hours": (20, 26),  # 20:00 to 02:00 (wraps)
        # --- Style ---
        "novelty_seeking": 0.60,
        "completion_short": 0.85,
        "completion_long": 0.70,
        "concurrent_capacity_range": (1, 2),
        "mainstream_susceptibility": 0.40,
        "rewatch_tendency": 0.15,
        "mood_variance": 0.30,
        "hot_release_trigger_prob": 0.40,
        "movie_series_ratio": 0.20,
        "subtitle_tolerance": 0.70,
        "sleep_dropoff": False,
        "avg_subscription_periods": 1.5,
        # --- Affinity priors ---
        "strong_genres": ["Thriller", "Crime", "Mystery", "Drama"],
        "medium_genres": ["Science Fiction", "Fantasy", "Action"],
        "strong_themes": [
            "whodunit", "cat-and-mouse", "conspiracy", "betrayal", "rise-and-fall"
        ],
        "strong_tones": ["tense", "suspenseful", "dark", "gritty"],
    },
    Archetype.CASUAL_TRICKLER: {
        "session_intensity_mean": 1.5,
        "session_intensity_std": 0.5,
        "weekday_activity": [0.6, 0.6, 0.6, 0.6, 0.6, 0.7, 0.7],
        "peak_hours": (19, 22),
        "novelty_seeking": 0.40,
        "completion_short": 0.75,
        "completion_long": 0.45,
        "concurrent_capacity_range": (1, 2),
        "mainstream_susceptibility": 0.70,
        "rewatch_tendency": 0.35,
        "mood_variance": 0.40,
        "hot_release_trigger_prob": 0.15,
        "movie_series_ratio": 0.40,
        "subtitle_tolerance": 0.40,
        "sleep_dropoff": False,
        "avg_subscription_periods": 1.2,
        "strong_genres": ["Comedy", "Drama", "Family", "Romance"],
        "medium_genres": ["History", "Documentary", "Music"],
        "strong_themes": ["workplace", "coming-of-age", "found-family", "road-trip"],
        "strong_tones": ["feel-good", "heartwarming", "comforting", "whimsical"],
    },
    Archetype.SAMPLER: {
        "session_intensity_mean": 2.5,
        "session_intensity_std": 1.5,
        "weekday_activity": [0.5, 0.5, 0.5, 0.6, 0.7, 0.8, 0.7],
        "peak_hours": (18, 24),
        "novelty_seeking": 0.85,
        "completion_short": 0.55,
        "completion_long": 0.15,
        "concurrent_capacity_range": (3, 5),
        "mainstream_susceptibility": 0.60,
        "rewatch_tendency": 0.05,
        "mood_variance": 0.80,
        "hot_release_trigger_prob": 0.60,
        "movie_series_ratio": 0.35,
        "subtitle_tolerance": 0.85,
        "sleep_dropoff": False,
        "avg_subscription_periods": 2.5,
        # Sampler uses bimodal/broad affinity — build_sampler_affinity() ignores
        # these lists and gives high variance across the full vocab.
        "strong_genres": [],
        "medium_genres": [],
        "strong_themes": [],
        "strong_tones": [],
    },
    Archetype.COMFORT_REWATCHER: {
        "session_intensity_mean": 2.0,
        "session_intensity_std": 1.0,
        "weekday_activity": [0.5, 0.5, 0.5, 0.5, 0.6, 0.7, 0.7],
        "peak_hours": (20, 23),
        "novelty_seeking": 0.15,
        "completion_short": 0.90,
        "completion_long": 0.60,
        "concurrent_capacity_range": (1, 2),
        "mainstream_susceptibility": 0.50,
        "rewatch_tendency": 0.80,
        "mood_variance": 0.10,
        "hot_release_trigger_prob": 0.10,
        "movie_series_ratio": 0.30,
        "subtitle_tolerance": 0.30,
        "sleep_dropoff": False,
        "avg_subscription_periods": 1.3,
        "strong_genres": ["Comedy", "Family", "Romance", "Animation"],
        "medium_genres": ["Music", "Fantasy"],
        "strong_themes": [
            "found-family", "coming-of-age", "second-chance-romance"
        ],
        "strong_tones": [
            "cozy", "comforting", "feel-good", "heartwarming", "whimsical"
        ],
    },
    Archetype.WEEKEND_WARRIOR: {
        "session_intensity_mean": 3.0,
        "session_intensity_std": 2.5,
        "weekday_activity": [0.05, 0.05, 0.05, 0.05, 0.30, 0.80, 0.70],
        "peak_hours": (19, 25),
        "novelty_seeking": 0.50,
        "completion_short": 0.80,
        "completion_long": 0.50,
        "concurrent_capacity_range": (1, 2),
        "mainstream_susceptibility": 0.60,
        "rewatch_tendency": 0.25,
        "mood_variance": 0.50,
        "hot_release_trigger_prob": 0.30,
        "movie_series_ratio": 0.50,
        "subtitle_tolerance": 0.50,
        "sleep_dropoff": False,
        "avg_subscription_periods": 1.8,
        "strong_genres": ["Action", "Adventure", "Thriller", "War"],
        "medium_genres": ["Science Fiction", "Crime"],
        "strong_themes": ["heist", "revenge", "undercover", "survival", "war-torn"],
        "strong_tones": ["high-energy", "tense", "campy", "thought-provoking"],
    },
    Archetype.EVENT_VIEWER: {
        "session_intensity_mean": 3.0,
        "session_intensity_std": 2.0,
        "weekday_activity": [0.2, 0.2, 0.2, 0.2, 0.3, 0.4, 0.3],
        "peak_hours": (18, 23),
        "novelty_seeking": 0.90,
        "completion_short": 0.70,
        "completion_long": 0.40,
        "concurrent_capacity_range": (1, 3),
        "mainstream_susceptibility": 0.95,
        "rewatch_tendency": 0.10,
        "mood_variance": 0.60,
        "hot_release_trigger_prob": 0.80,
        "movie_series_ratio": 0.30,
        "subtitle_tolerance": 0.60,
        "sleep_dropoff": False,
        "avg_subscription_periods": 4.5,
        # Event Viewer skews toward blockbuster tropes but fame dominates
        "strong_genres": [],
        "medium_genres": ["Fantasy", "Thriller", "Science Fiction", "Action"],
        "strong_themes": ["dystopia", "chosen-one", "post-apocalyptic"],
        "strong_tones": ["thought-provoking", "tense", "dark", "high-energy"],
    },
    Archetype.NIGHT_OWL: {
        "session_intensity_mean": 2.0,
        "session_intensity_std": 1.5,
        "weekday_activity": [0.5, 0.5, 0.5, 0.5, 0.6, 0.8, 0.7],
        "peak_hours": (22, 28),  # 22:00 to 04:00 (wraps)
        "novelty_seeking": 0.60,
        "completion_short": 0.65,
        "completion_long": 0.30,
        "concurrent_capacity_range": (2, 3),
        "mainstream_susceptibility": 0.35,
        "rewatch_tendency": 0.20,
        "mood_variance": 0.40,
        "hot_release_trigger_prob": 0.25,
        "movie_series_ratio": 0.45,
        "subtitle_tolerance": 0.60,
        "sleep_dropoff": True,  # only Night Owls
        "avg_subscription_periods": 1.6,
        "strong_genres": ["Horror", "Thriller", "Science Fiction", "Documentary"],
        "medium_genres": ["Mystery", "Crime"],
        "strong_themes": [
            "supernatural", "conspiracy", "dystopia", "post-apocalyptic"
        ],
        "strong_tones": [
            "dark", "eerie", "disturbing", "slow-burn", "melancholic"
        ],
    },
}


# ---------------------------------------------------------------------------
# Table E — Age band → archetype mix
# ---------------------------------------------------------------------------
AGE_BAND_ARCHETYPE_MIX = {
    AgeBand.KID: {
        Archetype.CASUAL_TRICKLER: 0.60,
        Archetype.COMFORT_REWATCHER: 0.40,
    },
    AgeBand.TEEN: {
        Archetype.SAMPLER: 0.30,
        Archetype.BINGER: 0.25,
        Archetype.CASUAL_TRICKLER: 0.20,
        Archetype.COMFORT_REWATCHER: 0.10,
        Archetype.NIGHT_OWL: 0.10,
        Archetype.WEEKEND_WARRIOR: 0.05,
    },
    AgeBand.YOUNG_ADULT: {
        Archetype.BINGER: 0.25,
        Archetype.SAMPLER: 0.25,
        Archetype.NIGHT_OWL: 0.20,
        Archetype.EVENT_VIEWER: 0.10,
        Archetype.WEEKEND_WARRIOR: 0.10,
        Archetype.CASUAL_TRICKLER: 0.05,
        Archetype.COMFORT_REWATCHER: 0.05,
    },
    AgeBand.ADULT: {
        Archetype.BINGER: 0.20,
        Archetype.CASUAL_TRICKLER: 0.20,
        Archetype.SAMPLER: 0.15,
        Archetype.WEEKEND_WARRIOR: 0.15,
        Archetype.EVENT_VIEWER: 0.10,
        Archetype.NIGHT_OWL: 0.10,
        Archetype.COMFORT_REWATCHER: 0.10,
    },
    AgeBand.MIDDLE_AGED: {
        Archetype.CASUAL_TRICKLER: 0.30,
        Archetype.WEEKEND_WARRIOR: 0.20,
        Archetype.COMFORT_REWATCHER: 0.20,
        Archetype.BINGER: 0.10,
        Archetype.EVENT_VIEWER: 0.10,
        Archetype.NIGHT_OWL: 0.05,
        Archetype.SAMPLER: 0.05,
    },
    AgeBand.OLDER: {
        Archetype.CASUAL_TRICKLER: 0.40,
        Archetype.COMFORT_REWATCHER: 0.40,
        Archetype.WEEKEND_WARRIOR: 0.10,
        Archetype.EVENT_VIEWER: 0.05,
        Archetype.BINGER: 0.03,
        Archetype.NIGHT_OWL: 0.01,
        Archetype.SAMPLER: 0.01,
    },
}


# ---------------------------------------------------------------------------
# Table F — Life context patterns per age band
# ---------------------------------------------------------------------------
AGE_TO_LIFE_CONTEXT = {
    AgeBand.KID: LifeContextPattern.KID,
    AgeBand.TEEN: LifeContextPattern.STUDENT,
    AgeBand.YOUNG_ADULT: LifeContextPattern.STUDENT,
    AgeBand.ADULT: LifeContextPattern.WORKING_PROFESSIONAL,
    AgeBand.MIDDLE_AGED: LifeContextPattern.WORKING_PROFESSIONAL,
    AgeBand.OLDER: LifeContextPattern.RETIRED,
}


# ---------------------------------------------------------------------------
# Table G — Country distribution (mirrors catalog)
# ---------------------------------------------------------------------------
COUNTRY_MIX = {
    "US": 0.40,
    "IN": 0.15,
    "KR": 0.10,
    "JP": 0.10,
    "GB": 0.07,
    "FR": 0.05,
    "ES": 0.04,
    "DE": 0.03,
    "MX": 0.03,
    "BR": 0.03,
}


# ---------------------------------------------------------------------------
# Table H — Household mix
# ---------------------------------------------------------------------------
HOUSEHOLD_MIX = {
    "solo": 0.75,
    "2_person": 0.20,
    "3_person": 0.05,
}
COUPLE_VS_ROOMMATES = 0.80  # of 2-person, 80% couple / 20% roommates


# ---------------------------------------------------------------------------
# Table I — Signup growth curve (COVID spike in 2020, retention high 2020-21)
# ---------------------------------------------------------------------------
SIGNUP_GROWTH_CURVE = {
    2015: 0.04,
    2016: 0.05,
    2017: 0.06,
    2018: 0.07,
    2019: 0.08,
    2020: 0.14,
    2021: 0.12,
    2022: 0.11,
    2023: 0.11,
    2024: 0.12,
    2025: 0.10,
}


# ---------------------------------------------------------------------------
# Table J — Base age distribution across all users
# ---------------------------------------------------------------------------
AGE_BAND_MIX = {
    AgeBand.KID: 0.05,
    AgeBand.TEEN: 0.10,
    AgeBand.YOUNG_ADULT: 0.25,
    AgeBand.ADULT: 0.30,
    AgeBand.MIDDLE_AGED: 0.20,
    AgeBand.OLDER: 0.10,
}


AGE_BAND_TO_AGE_RANGE = {
    AgeBand.KID: (7, 12),
    AgeBand.TEEN: (13, 17),
    AgeBand.YOUNG_ADULT: (18, 24),
    AgeBand.ADULT: (25, 34),
    AgeBand.MIDDLE_AGED: (35, 49),
    AgeBand.OLDER: (50, 75),
}


PROACTIVE_MSG_OPT_IN_RATE = 0.60  # 60% opt in


def maturity_ceiling(age: int) -> str:
    """Derive maturity ceiling from a user's current age."""
    if age < 13:
        return "7+"
    if age < 16:
        return "12+"
    if age < 18:
        return "16+"
    return "18+"