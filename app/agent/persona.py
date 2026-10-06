"""
Age/generation-aware persona builder + maturity gate.

Two jobs:
1. Build a tone/style directive for the system prompt based on the user's age.
2. Decide whether a requested title is above the user's maturity ceiling, so the
   orchestrator can refuse politely BEFORE calling the LLM to generate a
   recommendation sentence.

Age buckets (loose, overlap OK):
    kid        7-12
    teen       13-17   (GenZ-leaning but kept clean)
    young_ad   18-26   (GenZ slang, warm, playful; current reference points)
    adult      27-44   (millennial, friendly-professional, references OK)
    older      45+     (plain, respectful, less slang)

Maturity order used by the catalog: 7+ < 12+ < 16+ < 18+.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.auth import AuthContext

MATURITY_ORDER = ["7+", "12+", "16+", "18+"]


# ---------------------------------------------------------------------------
# Age bucket
# ---------------------------------------------------------------------------
def age_bucket(age: int | None) -> str:
    if age is None:
        return "adult"
    if age <= 12:
        return "kid"
    if age <= 17:
        return "teen"
    if age <= 26:
        return "young_ad"
    if age <= 44:
        return "adult"
    return "older"


# ---------------------------------------------------------------------------
# Tone directives per bucket
# ---------------------------------------------------------------------------
_TONE_DIRECTIVES: dict[str, str] = {
    "kid": (
        "The user is a KID (around 7-12). Use simple words, short sentences, "
        "and a cheerful tone. Avoid sarcasm, dark humor, violence, romance "
        "talk, and anything scary. Explain things like you would to a younger "
        "sibling. Emoji sparingly is fine. NEVER mention or recommend content "
        "rated above 7+. If the kid asks about a show/movie above 7+, say you "
        "can't talk about that one and offer something fun from their age range."
    ),
    "teen": (
        "The user is a TEEN (13-17). Match a GenZ, chill tone, light slang is "
        "fine ('vibe', 'low-key', 'fr', 'solid') but don't overdo it. Be warm, "
        "not preachy. Don't lecture. You can discuss romance/relationships at a "
        "general level but avoid explicit content. NEVER recommend content "
        "rated above 16+. If they push for 18+ content, decline kindly and "
        "offer the closest teen-friendly match."
    ),
    "young_ad": (
        "The user is a YOUNG ADULT (18-26), likely GenZ. Be warm, witty, "
        "unfiltered, and use natural GenZ slang when it fits ('tbh', 'ngl', "
        "'lowkey', 'this hits', 'the vibe is immaculate', 'that's wild', "
        "'no bc'). Treat them like a smart friend. If they share personal "
        "stuff (relationships, feelings, big life moments), respond like a "
        "friend would — react first, THEN naturally offer a show or movie "
        "that matches the vibe. Example: user says they just had their first "
        "kiss → react with warmth and maybe one line of hype, then offer a "
        "romance series that matches the energy, like 'wanna ride that high "
        "with a show that nails first-love butterflies? i got you.'"
    ),
    "adult": (
        "The user is an ADULT (27-44), likely millennial. Be warm, "
        "conversational, intelligent. Dry wit is welcome. Pop culture "
        "references across the last 30 years land well. Be concise and respect "
        "their time. If they share personal/off-topic stuff, respond "
        "thoughtfully (don't be clinical), then pivot gracefully to a "
        "show/movie that fits the mood."
    ),
    "older": (
        "The user is OLDER (45+). Keep the tone warm, plain-spoken, and "
        "respectful. Skip slang and internet-speak. Favor well-known titles "
        "and give a bit of context on newer or niche picks (year, genre, why "
        "it's loved). If they share personal stuff, respond with genuine "
        "care, not performative empathy, then offer a title if it fits."
    ),
}


def tone_directive_for(age: int | None) -> str:
    return _TONE_DIRECTIVES[age_bucket(age)]


# ---------------------------------------------------------------------------
# Maturity gate
# ---------------------------------------------------------------------------
@dataclass
class MaturityDecision:
    allowed: bool
    reason: str = ""
    refusal_hint: str = ""


def _rank(m: str | None) -> int:
    if m in MATURITY_ORDER:
        return MATURITY_ORDER.index(m)
    return -1


def check_title_maturity(
    title_rating: str | None, ctx: AuthContext
) -> MaturityDecision:
    """
    Return allowed=False if the title's rating exceeds the user's ceiling.
    """
    if not title_rating or not ctx.maturity_ceiling:
        return MaturityDecision(allowed=True)
    if _rank(title_rating) > _rank(ctx.maturity_ceiling):
        return MaturityDecision(
            allowed=False,
            reason=f"title rated {title_rating} exceeds user ceiling {ctx.maturity_ceiling}",
            refusal_hint=_refusal_hint(ctx),
        )
    return MaturityDecision(allowed=True)


def _refusal_hint(ctx: AuthContext) -> str:
    """Short style guidance for how to phrase the refusal, by age."""
    b = age_bucket(ctx.age)
    if b == "kid":
        return (
            "Say that one's for grown-ups and you can't chat about it, but "
            "offer two fun picks they CAN watch instead. Stay cheerful."
        )
    if b == "teen":
        return (
            "Say that title is above their rating so you can't go there, no "
            "lecture. Offer the closest teen-friendly alternative."
        )
    return (
        "Explain briefly that this title is outside their account's rating "
        "ceiling and offer the closest in-ceiling match. Don't be preachy."
    )


# ---------------------------------------------------------------------------
# Filtering helpers for tool results
# ---------------------------------------------------------------------------
def filter_titles_by_ceiling(
    rows: list[dict], ctx: AuthContext, rating_key: str = "maturity_rating"
) -> list[dict]:
    """Drop titles above the user's ceiling. Defensive — tools already filter."""
    if not ctx.maturity_ceiling:
        return rows
    ceil = _rank(ctx.maturity_ceiling)
    out = []
    for r in rows:
        rt = r.get(rating_key)
        if rt is None or _rank(rt) <= ceil:
            out.append(r)
    return out
