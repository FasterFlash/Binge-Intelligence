"""
Controlled vocabularies — the single source of truth for the whole system.

FROZEN. Every title tags ONLY from these lists. Every user affinity vector is
defined OVER these exact strings. Do not free-text tags anywhere. If you must
extend, extend HERE and nowhere else, and only when a real query has nowhere to land.

Rules enforced downstream:
  - genres: 1-4 per title  (auto-seeded from TMDB genre IDs)
  - themes: 1-4 per title  (LLM enrichment pass, from THEMES only)
  - tones:  1-3 per title  (LLM enrichment pass, from TONES only)
  - maturity is NOT a genre -> handled by the maturity_rating field
"""

# ---------------------------------------------------------------------------
# GENRES  (~20)  — anchored on TMDB's taxonomy; all but KIDS map to TMDB IDs
# ---------------------------------------------------------------------------
GENRES = [
    "Action",
    "Adventure",
    "Animation",
    "Comedy",
    "Crime",
    "Documentary",
    "Drama",
    "Family",        # watchable together across ages (Pixar-ish)
    "Fantasy",
    "History",
    "Horror",
    "Music",
    "Mystery",
    "Romance",
    "Science Fiction",
    "Thriller",
    "War",
    "Western",
    "Reality",
    "Kids",          # made FOR children specifically (TV-native, no TMDB movie ID)
]

# ---------------------------------------------------------------------------
# THEMES  (~34)  — what it's ABOUT. Groups are organizational only;
#                 a title may pull tags from any group.
# ---------------------------------------------------------------------------
THEMES = [
    # Romance / relationship
    "coming-of-age",
    "enemies-to-lovers",
    "opposites-attract",
    "forbidden-love",
    "love-triangle",
    "second-chance-romance",
    "coming-out",
    "found-family",
    # Crime / thriller
    "heist",
    "whodunit",
    "cat-and-mouse",
    "conspiracy",
    "revenge",
    "courtroom",
    "undercover",
    # Struggle / arc
    "redemption",
    "underdog",
    "rise-and-fall",
    "addiction-recovery",
    "survival",
    "betrayal",
    "rags-to-riches",
    # Speculative
    "dystopia",
    "post-apocalyptic",
    "time-travel",
    "supernatural",
    "chosen-one",
    "alternate-reality",
    # Life / journey
    "fish-out-of-water",
    "road-trip",
    "mentor-protege",
    "workplace",
    "class-conflict",
    "immigrant-experience",
    "war-torn",
]

# ---------------------------------------------------------------------------
# TONES  (~20)  — how it FEELS. Powers mood queries
#                 ("something to cry to", "bad day keep it light", "2am don't hook me")
# ---------------------------------------------------------------------------
TONES = [
    "swoonworthy",
    "feel-good",
    "heartwarming",
    "cozy",
    "comforting",
    "whimsical",
    "campy",
    "satirical",
    "bittersweet",
    "melancholic",
    "tearjerker",
    "thought-provoking",
    "tense",
    "suspenseful",
    "dark",
    "gritty",
    "disturbing",
    "eerie",
    "high-energy",
    "slow-burn",
]

# ---------------------------------------------------------------------------
# Tag-count caps (enforce at tagging time; more = noise, not signal)
# ---------------------------------------------------------------------------
TAG_CAPS = {
    "genres": (1, 4),
    "themes": (1, 4),
    "tones": (1, 3),
}

# ---------------------------------------------------------------------------
# Maturity ratings — SEPARATE axis from genre. Drives age-ceiling gating.
# ---------------------------------------------------------------------------
MATURITY_RATINGS = ["7+", "12+", "16+", "18+"]

# Fast membership checks + drift guard
GENRES_SET = frozenset(GENRES)
THEMES_SET = frozenset(THEMES)
TONES_SET = frozenset(TONES)
MATURITY_SET = frozenset(MATURITY_RATINGS)


def validate_tags(genres=None, themes=None, tones=None):
    """Reject any tag not in the frozen vocab and any count outside the caps.
    Call this at the END of the enrichment pass so drift never reaches the DB."""
    for name, values, allowed in (
        ("genres", genres or [], GENRES_SET),
        ("themes", themes or [], THEMES_SET),
        ("tones", tones or [], TONES_SET),
    ):
        bad = [v for v in values if v not in allowed]
        if bad:
            raise ValueError(f"{name}: tags outside frozen vocab: {bad}")
        lo, hi = TAG_CAPS[name]
        if values and not (lo <= len(values) <= hi):
            raise ValueError(f"{name}: count {len(values)} outside cap {lo}-{hi}")
    return True


if __name__ == "__main__":
    print(f"GENRES  : {len(GENRES)}")
    print(f"THEMES  : {len(THEMES)}")
    print(f"TONES   : {len(TONES)}")
    print(f"MATURITY: {len(MATURITY_RATINGS)}")
    # smoke test
    validate_tags(
        genres=["Romance", "Drama"],
        themes=["coming-of-age", "opposites-attract", "coming-out"],
        tones=["swoonworthy", "bittersweet"],
    )
    print("validate_tags: OK")