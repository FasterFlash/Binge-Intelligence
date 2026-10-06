"""
Agent tools — parameterized functions wrapping gold-table lookups + RAG.

Each tool is tagged with a ToolScope. The orchestrator enforces scope:
  - SELF_DATA: user_id must be auth user; no other user's data accessible
  - CATALOG:   public title info
  - AGGREGATE: population stats (no PII)
  - OTHER_USER: denied by default

Tool declarations below are converted to OpenAI function-calling schema
by `all_tool_schemas()` for the LLM.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agent.auth import AuthContext
from app.agent.content_rag import search_titles


# ----------------------------------------------------------------------------
# Scope + registry
# ----------------------------------------------------------------------------
class ToolScope(str, enum.Enum):
    SELF_DATA = "self_data"
    CATALOG = "catalog"
    AGGREGATE = "aggregate"
    OTHER_USER = "other_user"


@dataclass
class ToolSpec:
    name: str
    description: str
    scope: ToolScope
    parameters: dict          # JSON-schema style
    handler: Callable         # (db, ctx, **kwargs) -> dict


_TOOLS: dict[str, ToolSpec] = {}


def tool(scope: ToolScope, parameters: dict):
    """Decorator — registers the function as a tool."""
    def wrap(fn):
        _TOOLS[fn.__name__] = ToolSpec(
            name=fn.__name__,
            description=(fn.__doc__ or "").strip(),
            scope=scope,
            parameters=parameters,
            handler=fn,
        )
        return fn
    return wrap


def get_tool(name: str) -> ToolSpec | None:
    return _TOOLS.get(name)


def all_tool_schemas() -> list[dict]:
    """OpenAI-style function-calling schemas for every tool."""
    out = []
    for t in _TOOLS.values():
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
        )
    return out


# ----------------------------------------------------------------------------
# SELF_DATA tools — scoped to AuthContext.user_id
# ----------------------------------------------------------------------------
@tool(
    scope=ToolScope.SELF_DATA,
    parameters={"type": "object", "properties": {}, "required": []},
)
def get_my_profile(db: Session, ctx: AuthContext) -> dict:
    """Return the authenticated user's taste profile and activity summary.
    Use for ANY question about 'me' / 'my' habits / 'my taste'.
    No parameters."""
    row = db.execute(
        text(
            """
            SELECT uws.total_minutes_alltime, uws.total_minutes_30d,
                   uws.active_days_per_week, uws.avg_daily_active_minutes,
                   uws.top_3_genres, uws.top_3_themes, uws.top_3_tones,
                   uws.completion_rate_short, uws.completion_rate_long,
                   uws.avg_abandonment_episode, uws.currently_in_progress_count,
                   uws.churn_risk_score, uws.typical_watch_hour,
                   uws.last_active_date, uws.total_sessions, uws.total_titles_watched,
                   u.archetype, u.age, u.country, u.cohort_year
            FROM user_watch_stats uws
            JOIN users u ON uws.user_id = u.user_id
            WHERE uws.user_id = :uid
            """
        ),
        {"uid": ctx.user_id},
    ).mappings().one_or_none()
    return dict(row) if row else {}


@tool(
    scope=ToolScope.SELF_DATA,
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 15}},
        "required": [],
    },
)
def get_my_in_progress_series(db: Session, ctx: AuthContext, limit: int = 15) -> dict:
    """List the authenticated user's currently in-progress series
    (what they're actively watching). Use for 'continue watching', 'what am I watching'."""
    rows = db.execute(
        text(
            """
            SELECT t.title, t.content_type, p.current_season, p.current_episode,
                   p.total_episodes_watched, p.last_watched_at, p.started_at,
                   t.episode_count, t.season_count
            FROM user_series_progress p
            JOIN titles t ON p.title_id = t.title_id
            WHERE p.user_id = :uid AND p.state = 'in_progress'
            ORDER BY p.last_watched_at DESC LIMIT :lim
            """
        ),
        {"uid": ctx.user_id, "lim": limit},
    ).mappings().all()
    return {"in_progress": [dict(r) for r in rows]}


@tool(
    scope=ToolScope.SELF_DATA,
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)
def get_my_abandoned_series(db: Session, ctx: AuthContext, limit: int = 20) -> dict:
    """List series the authenticated user abandoned (dropped mid-way).
    Returns which show, which episode they dropped at, when."""
    rows = db.execute(
        text(
            """
            SELECT t.title, t.genres, t.themes, p.abandonment_episode,
                   p.total_episodes_watched, p.last_watched_at, p.started_at
            FROM user_series_progress p
            JOIN titles t ON p.title_id = t.title_id
            WHERE p.user_id = :uid AND p.state = 'abandoned'
            ORDER BY p.last_watched_at DESC LIMIT :lim
            """
        ),
        {"uid": ctx.user_id, "lim": limit},
    ).mappings().all()
    return {"abandoned": [dict(r) for r in rows]}


@tool(
    scope=ToolScope.SELF_DATA,
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)
def get_my_completed_titles(db: Session, ctx: AuthContext, limit: int = 20) -> dict:
    """List series the user has fully completed."""
    rows = db.execute(
        text(
            """
            SELECT t.title, t.genres, t.themes, t.tones,
                   p.ended_at, p.total_episodes_watched
            FROM user_series_progress p
            JOIN titles t ON p.title_id = t.title_id
            WHERE p.user_id = :uid AND p.state = 'completed'
            ORDER BY p.ended_at DESC LIMIT :lim
            """
        ),
        {"uid": ctx.user_id, "lim": limit},
    ).mappings().all()
    return {"completed": [dict(r) for r in rows]}


@tool(
    scope=ToolScope.SELF_DATA,
    parameters={
        "type": "object",
        "properties": {
            "days": {"type": "integer", "default": 30, "description": "Last N days"},
            "limit": {"type": "integer", "default": 25},
        },
        "required": [],
    },
)
def get_my_recent_watches(
    db: Session, ctx: AuthContext, days: int = 30, limit: int = 25
) -> dict:
    """Return the user's most-recent watch events over the last N days.
    Each row has title + when + how long + completion."""
    since = date(2025, 12, 31) - timedelta(days=days)
    rows = db.execute(
        text(
            """
            SELECT t.title, w.content_type, w.season_num, w.episode_num,
                   w.started_at, w.minutes_watched, w.completion_pct
            FROM watch_events w
            JOIN titles t ON w.title_id = t.title_id
            WHERE w.user_id = :uid AND w.started_at >= :since
            ORDER BY w.started_at DESC LIMIT :lim
            """
        ),
        {"uid": ctx.user_id, "since": since, "lim": limit},
    ).mappings().all()
    return {"recent_watches": [dict(r) for r in rows], "since": str(since)}


@tool(
    scope=ToolScope.SELF_DATA,
    parameters={"type": "object", "properties": {}, "required": []},
)
def get_my_subscription_history(db: Session, ctx: AuthContext) -> dict:
    """Return every subscription period for the user, with why it started / ended."""
    rows = db.execute(
        text(
            """
            SELECT p.period_num, p.started_on, p.ended_on, p.days_active,
                   p.trigger_reason, p.end_reason,
                   t.title AS triggered_by_title
            FROM subscription_periods p
            LEFT JOIN titles t ON p.trigger_title_id = t.title_id
            WHERE p.user_id = :uid
            ORDER BY p.period_num
            """
        ),
        {"uid": ctx.user_id},
    ).mappings().all()
    return {"periods": [dict(r) for r in rows]}


# ----------------------------------------------------------------------------
# CATALOG tools — public info
# ----------------------------------------------------------------------------
@tool(
    scope=ToolScope.CATALOG,
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language description of what the user is looking for",
            },
            "limit": {"type": "integer", "default": 10},
            "country": {
                "type": "string",
                "description": "Optional ISO country code to filter by title origin",
            },
        },
        "required": ["query"],
    },
)
def search_titles_by_description(
    db: Session, ctx: AuthContext, query: str, limit: int = 10, country: str | None = None
) -> dict:
    """Semantic search over title synopses. Use for 'that show about X'
    or finding something by premise/vibe. Respects maturity ceiling of user."""
    results = search_titles(
        db, query, limit=limit,
        maturity_ceiling=ctx.maturity_ceiling,
        country_filter=country,
    )
    for r in results:
        if r.get("synopsis"):
            r["synopsis"] = r["synopsis"][:600]  # keep tokens tight
    return {"results": results}


@tool(
    scope=ToolScope.CATALOG,
    parameters={
        "type": "object",
        "properties": {
            "title_name": {"type": "string", "description": "Exact or fuzzy title name"},
        },
        "required": ["title_name"],
    },
)
def get_title_details(db: Session, ctx: AuthContext, title_name: str) -> dict:
    """Fetch details for a specific title: synopsis, genres, cast etc.
    Spoiler-aware (future: filter by user's current episode progress)."""
    rows = db.execute(
        text(
            """
            SELECT title_id, title, release_year, content_type, country,
                   runtime_minutes, episode_count, season_count, series_status,
                   genres, themes, tones, maturity_rating, synopsis, fame,
                   platform_add_date, platform_leaving_date, is_platform_original
            FROM titles
            WHERE lower(title) LIKE lower(:q)
              AND maturity_rating::text = ANY(:allowed)
            ORDER BY fame DESC
            LIMIT 5
            """
        ),
        {
            "q": f"%{title_name}%",
            "allowed": _allowed_maturities(ctx.maturity_ceiling),
        },
    ).mappings().all()
    out = [dict(r) for r in rows]
    for r in out:
        if r.get("synopsis"):
            r["synopsis"] = r["synopsis"][:800]
    return {"matches": out}


@tool(
    scope=ToolScope.CATALOG,
    parameters={
        "type": "object",
        "properties": {
            "country": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
        },
        "required": [],
    },
)
def get_catalog_new_releases(
    db: Session, ctx: AuthContext, country: str | None = None, limit: int = 20
) -> dict:
    """Recent catalog additions (last 60 days of sim), ordered by fame.
    Use for 'what's new' / 'latest from India'."""
    sql = """
        SELECT title, country, genres, themes, tones, fame,
               platform_add_date, is_platform_original
        FROM titles
        WHERE platform_add_date >= DATE '2025-11-01'
          AND maturity_rating::text = ANY(:allowed)
    """
    params: dict[str, Any] = {"allowed": _allowed_maturities(ctx.maturity_ceiling)}
    if country:
        sql += " AND country = :c"
        params["c"] = country
    sql += " ORDER BY fame DESC LIMIT :lim"
    params["lim"] = limit
    rows = db.execute(text(sql), params).mappings().all()
    return {"new_releases": [dict(r) for r in rows]}


@tool(
    scope=ToolScope.CATALOG,
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)
def get_catalog_leaving_soon(
    db: Session, ctx: AuthContext, limit: int = 20
) -> dict:
    """Titles leaving the platform in the next ~90 days, ordered by fame."""
    rows = db.execute(
        text(
            """
            SELECT title, country, genres, themes, platform_leaving_date, fame
            FROM titles
            WHERE platform_leaving_date BETWEEN DATE '2025-12-31'
              AND DATE '2026-03-31'
              AND maturity_rating::text = ANY(:allowed)
            ORDER BY platform_leaving_date, fame DESC
            LIMIT :lim
            """
        ),
        {"allowed": _allowed_maturities(ctx.maturity_ceiling), "lim": limit},
    ).mappings().all()
    return {"leaving_soon": [dict(r) for r in rows]}


@tool(
    scope=ToolScope.CATALOG,
    parameters={
        "type": "object",
        "properties": {
            "title_name": {"type": "string"},
        },
        "required": ["title_name"],
    },
)
def get_series_dropoff_pattern(
    db: Session, ctx: AuthContext, title_name: str
) -> dict:
    """For a series, show at which episode viewers typically drop off.
    Powers 'should I quit at episode X?' questions."""
    row = db.execute(
        text(
            """
            SELECT t.title, t.episode_count, t.season_count,
                   tes.total_watchers, tes.completion_rate,
                   tes.avg_abandonment_episode
            FROM titles t
            JOIN title_engagement_stats tes ON t.title_id = tes.title_id
            WHERE lower(t.title) LIKE lower(:q)
              AND t.content_type = 'series'
            ORDER BY t.fame DESC
            LIMIT 1
            """
        ),
        {"q": f"%{title_name}%"},
    ).mappings().one_or_none()
    return dict(row) if row else {"error": "no matching series"}


# ----------------------------------------------------------------------------
# AGGREGATE tools — population stats
# ----------------------------------------------------------------------------
@tool(
    scope=ToolScope.AGGREGATE,
    parameters={
        "type": "object",
        "properties": {
            "country": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
            "window": {
                "type": "string",
                "enum": ["7d", "30d", "alltime"],
                "default": "30d",
            },
        },
        "required": [],
    },
)
def get_popular_titles(
    db: Session,
    ctx: AuthContext,
    country: str | None = None,
    limit: int = 10,
    window: str = "30d",
) -> dict:
    """Most-popular titles overall or by country.
    window: '7d' (hot now), '30d' (trending), 'alltime' (classics)."""
    score_col = {
        "7d": "tes.hot_score_7d",
        "30d": "tes.hot_score_30d",
        "alltime": "tes.total_watchers",
    }.get(window, "tes.hot_score_30d")

    sql = f"""
        SELECT t.title, t.country, t.genres, t.themes,
               t.fame, {score_col} AS score,
               tes.total_watchers, tes.completion_rate
        FROM title_engagement_stats tes
        JOIN titles t ON tes.title_id = t.title_id
        WHERE t.maturity_rating::text = ANY(:allowed)
    """
    params: dict[str, Any] = {"allowed": _allowed_maturities(ctx.maturity_ceiling)}
    if country:
        sql += " AND t.country = :c"
        params["c"] = country
    sql += f" ORDER BY {score_col} DESC NULLS LAST LIMIT :lim"
    params["lim"] = limit
    rows = db.execute(text(sql), params).mappings().all()
    return {"popular": [dict(r) for r in rows], "window": window}


@tool(
    scope=ToolScope.AGGREGATE,
    parameters={
        "type": "object",
        "properties": {"cohort_year": {"type": "integer"}},
        "required": [],
    },
)
def get_cohort_insights(
    db: Session, ctx: AuthContext, cohort_year: int | None = None
) -> dict:
    """Signup-cohort retention + top titles. Defaults to the asking user's cohort."""
    year = cohort_year if cohort_year is not None else _lookup_user_cohort(db, ctx)
    row = db.execute(
        text(
            """
            SELECT cohort_year, users_in_cohort, avg_tenure_days,
                   retention_12m, retention_24m, avg_sessions_per_user,
                   avg_watch_minutes_per_user, top_titles
            FROM cohort_metrics WHERE cohort_year = :y
            """
        ),
        {"y": year},
    ).mappings().one_or_none()
    return dict(row) if row else {"error": f"no cohort data for {year}"}


@tool(
    scope=ToolScope.AGGREGATE,
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 10}},
        "required": [],
    },
)
def get_top_subscription_driving_titles(
    db: Session, ctx: AuthContext, limit: int = 10
) -> dict:
    """Titles that drove the most new/returning subscriptions via hot-release triggers."""
    rows = db.execute(
        text(
            """
            SELECT t.title, t.fame, t.is_platform_original,
                   COUNT(*) AS subscriptions_driven
            FROM subscription_periods p
            JOIN titles t ON p.trigger_title_id = t.title_id
            WHERE p.trigger_reason = 'hot_release'
            GROUP BY t.title, t.fame, t.is_platform_original
            ORDER BY subscriptions_driven DESC
            LIMIT :lim
            """
        ),
        {"lim": limit},
    ).mappings().all()
    return {"driving_titles": [dict(r) for r in rows]}


# ----------------------------------------------------------------------------
# Composite tool — personalized recommendations
# ----------------------------------------------------------------------------
@tool(
    scope=ToolScope.SELF_DATA,
    parameters={
        "type": "object",
        "properties": {
            "mood_or_filter": {
                "type": "string",
                "description": (
                    "Natural-language description of what they want "
                    "(mood, genre, 'short series', 'something to cry to', ...)"
                ),
            },
            "limit": {"type": "integer", "default": 10},
        },
        "required": [],
    },
)
def recommend_for_me(
    db: Session, ctx: AuthContext, mood_or_filter: str = "", limit: int = 10
) -> dict:
    """Personalized recommendations blending the user's taste profile with
    popular + semantic-search candidates. Mood/filter optional free text."""
    # Pull my taste
    me = db.execute(
        text(
            """
            SELECT top_3_genres, top_3_themes, top_3_tones
            FROM user_watch_stats WHERE user_id = :uid
            """
        ),
        {"uid": ctx.user_id},
    ).mappings().one_or_none()

    taste = dict(me) if me else {}

    # If mood specified, do semantic search; else use popular-in-country
    if mood_or_filter:
        hits = search_titles(
            db, mood_or_filter, limit=limit * 2,
            maturity_ceiling=ctx.maturity_ceiling,
        )
    else:
        rows = db.execute(
            text(
                """
                SELECT t.title_id, t.title, t.country, t.genres, t.themes,
                       t.tones, t.fame
                FROM title_engagement_stats tes
                JOIN titles t ON tes.title_id = t.title_id
                WHERE t.maturity_rating::text = ANY(:allowed)
                ORDER BY tes.hot_score_30d DESC LIMIT :lim
                """
            ),
            {"allowed": _allowed_maturities(ctx.maturity_ceiling), "lim": limit * 2},
        ).mappings().all()
        hits = [dict(r) for r in rows]

    # Exclude titles the user has already touched (watched/abandoned)
    seen = db.execute(
        text(
            """
            SELECT title_id FROM user_series_progress WHERE user_id = :uid
            UNION
            SELECT DISTINCT title_id FROM watch_events WHERE user_id = :uid
            """
        ),
        {"uid": ctx.user_id},
    ).scalars().all()
    seen_set = set(str(x) for x in seen)
    filtered = [h for h in hits if str(h["title_id"]) not in seen_set][:limit]

    return {"recommendations": filtered, "based_on_taste": taste}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
_MATURITY_ORDER = ["7+", "12+", "16+", "18+"]


def _allowed_maturities(ceiling: str) -> list[str]:
    if ceiling not in _MATURITY_ORDER:
        return _MATURITY_ORDER
    return _MATURITY_ORDER[: _MATURITY_ORDER.index(ceiling) + 1]


def _lookup_user_cohort(db: Session, ctx: AuthContext) -> int:
    r = db.execute(
        text("SELECT cohort_year FROM users WHERE user_id = :uid"),
        {"uid": ctx.user_id},
    ).scalar_one_or_none()
    return int(r) if r else 2024


# ----------------------------------------------------------------------------
# SQL fallback tool — escape hatch when no scoped tool fits.
# ----------------------------------------------------------------------------
@tool(
    scope=ToolScope.SELF_DATA,
    parameters={
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "A single read-only SELECT (or WITH ... SELECT) against the "
                    "catalog / behavior schema. The authenticated user's "
                    "user_id filter is auto-injected if you query a per-user "
                    "table. LIMIT is capped at 200. No writes."
                ),
            },
            "intent": {
                "type": "string",
                "description": (
                    "One short sentence describing what question this SQL is "
                    "meant to answer. Used by the judge for verification."
                ),
            },
        },
        "required": ["sql", "intent"],
    },
)
def run_sql_fallback(
    db: Session, ctx: AuthContext, sql: str, intent: str = ""
) -> dict:
    """ESCAPE HATCH. Use ONLY when no other tool fits the question.
    Executes a validated, read-only SELECT against the DB. The authenticated
    user's user_id is auto-injected for per-user tables; multi-statement
    queries, writes, and dangerous functions are rejected. Row cap: 200.
    Prefer scoped tools over this; this is a last resort."""
    from app.agent.sql_fallback import run_fallback

    result = run_fallback(db, sql, str(ctx.user_id))
    out: dict = {
        "ok": result.ok,
        "row_count": result.row_count,
        "columns": result.columns,
        "rows": result.rows,
        "intent": intent,
    }
    if not result.ok:
        out["error"] = result.error
    if result.validation:
        out["validation"] = {
            "tables_touched": result.validation.tables_touched,
            "user_filter_injected": result.validation.user_filter_injected,
            "limit_injected": result.validation.limit_injected,
            "rewritten_sql": result.validation.rewritten_sql,
        }
    return out


# ----------------------------------------------------------------------------
# PHASE B — cast / crew / runtime / rating / keyword tools (catalog scope)
# ----------------------------------------------------------------------------
@tool(
    scope=ToolScope.CATALOG,
    parameters={
        "type": "object",
        "properties": {
            "actor_name": {
                "type": "string",
                "description": (
                    "Full or partial actor name. Case-insensitive substring "
                    "match on people.name — e.g. 'Chalamet' matches "
                    "'Timothée Chalamet'."
                ),
            },
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["actor_name"],
    },
)
def search_by_cast(
    db: Session, ctx: AuthContext, actor_name: str, limit: int = 10,
) -> dict:
    """Find titles featuring an actor (by name, substring, case-insensitive).
    Returns titles ordered by the actor's billing position + title fame.
    Respects the user's maturity ceiling."""
    rows = db.execute(
        text(
            """
            SELECT t.title_id, t.title, t.release_year,
                   t.content_type::text AS content_type,
                   t.genres, t.themes, t.tones,
                   t.maturity_rating::text AS maturity_rating,
                   t.fame, t.tmdb_rating, t.tagline,
                   p.name AS actor_name, tc.character_name,
                   tc.billing_order
            FROM title_cast tc
            JOIN people p ON p.person_id = tc.person_id
            JOIN titles t ON t.title_id = tc.title_id
            WHERE p.name ILIKE :needle
              AND t.maturity_rating::text = ANY(:allowed)
            ORDER BY tc.billing_order ASC, t.fame DESC
            LIMIT :lim
            """
        ),
        {
            "needle": f"%{actor_name}%",
            "allowed": _allowed_maturities(ctx.maturity_ceiling),
            "lim": limit,
        },
    ).mappings().all()
    return {"actor_query": actor_name, "results": [dict(r) for r in rows]}


@tool(
    scope=ToolScope.CATALOG,
    parameters={
        "type": "object",
        "properties": {
            "director_name": {
                "type": "string",
                "description": "Full or partial director name, substring match.",
            },
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["director_name"],
    },
)
def search_by_director(
    db: Session, ctx: AuthContext, director_name: str, limit: int = 10,
) -> dict:
    """Find titles by a director / creator / showrunner. Case-insensitive
    substring match. Respects the user's maturity ceiling."""
    rows = db.execute(
        text(
            """
            SELECT t.title_id, t.title, t.release_year,
                   t.content_type::text AS content_type,
                   t.genres, t.themes, t.tones,
                   t.maturity_rating::text AS maturity_rating,
                   t.fame, t.tmdb_rating, t.tagline,
                   p.name AS person_name, tc.job
            FROM title_crew tc
            JOIN people p ON p.person_id = tc.person_id
            JOIN titles t ON t.title_id = tc.title_id
            WHERE p.name ILIKE :needle
              AND tc.job IN ('Director', 'Creator', 'Showrunner')
              AND t.maturity_rating::text = ANY(:allowed)
            ORDER BY t.fame DESC, t.release_year DESC
            LIMIT :lim
            """
        ),
        {
            "needle": f"%{director_name}%",
            "allowed": _allowed_maturities(ctx.maturity_ceiling),
            "lim": limit,
        },
    ).mappings().all()
    return {"director_query": director_name, "results": [dict(r) for r in rows]}


@tool(
    scope=ToolScope.CATALOG,
    parameters={
        "type": "object",
        "properties": {
            "max_minutes": {
                "type": "integer",
                "description": "Max runtime in minutes (movies only).",
            },
            "min_minutes": {"type": "integer", "default": 0},
            "genres": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional genre filter, matches any.",
            },
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["max_minutes"],
    },
)
def search_by_runtime(
    db: Session, ctx: AuthContext,
    max_minutes: int, min_minutes: int = 0,
    genres: list[str] | None = None, limit: int = 10,
) -> dict:
    """Find MOVIES by runtime window — "something short tonight" / "a 90-minute
    comedy". Series are excluded (runtime semantics don't apply)."""
    sql = """
        SELECT title_id, title, release_year, runtime_minutes,
               genres, themes, tones,
               maturity_rating::text AS maturity_rating,
               fame, tmdb_rating, tagline
        FROM titles
        WHERE content_type = 'movie'
          AND runtime_minutes BETWEEN :min_m AND :max_m
          AND maturity_rating::text = ANY(:allowed)
    """
    params: dict = {
        "min_m": min_minutes, "max_m": max_minutes,
        "allowed": _allowed_maturities(ctx.maturity_ceiling),
    }
    if genres:
        sql += " AND genres && CAST(:genres AS text[])"
        params["genres"] = genres
    sql += " ORDER BY fame DESC, tmdb_rating DESC NULLS LAST LIMIT :lim"
    params["lim"] = limit
    rows = db.execute(text(sql), params).mappings().all()
    return {"results": [dict(r) for r in rows]}


@tool(
    scope=ToolScope.CATALOG,
    parameters={
        "type": "object",
        "properties": {
            "min_rating": {
                "type": "number",
                "description": "Minimum TMDB rating 0-10 (7.5 = critically acclaimed).",
            },
            "min_votes": {
                "type": "integer",
                "default": 500,
                "description": "Minimum vote count — guards against niche high ratings.",
            },
            "content_type": {
                "type": "string",
                "enum": ["movie", "series", "any"],
                "default": "any",
            },
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["min_rating"],
    },
)
def search_by_rating(
    db: Session, ctx: AuthContext,
    min_rating: float, min_votes: int = 500,
    content_type: str = "any", limit: int = 10,
) -> dict:
    """Critically acclaimed titles above a rating floor with enough votes
    to be meaningful. Great for "something smart", "what are the all-time
    greats", etc."""
    sql = """
        SELECT title_id, title, release_year,
               content_type::text AS content_type,
               genres, themes, tones,
               maturity_rating::text AS maturity_rating,
               fame, tmdb_rating, tmdb_vote_count, tagline
        FROM titles
        WHERE tmdb_rating IS NOT NULL
          AND tmdb_rating >= :min_r
          AND tmdb_vote_count >= :min_v
          AND maturity_rating::text = ANY(:allowed)
    """
    params: dict = {
        "min_r": min_rating, "min_v": min_votes,
        "allowed": _allowed_maturities(ctx.maturity_ceiling),
    }
    if content_type in ("movie", "series"):
        sql += " AND content_type = :ctype"
        params["ctype"] = content_type
    sql += " ORDER BY tmdb_rating DESC, tmdb_vote_count DESC LIMIT :lim"
    params["lim"] = limit
    rows = db.execute(text(sql), params).mappings().all()
    return {"results": [dict(r) for r in rows]}


@tool(
    scope=ToolScope.CATALOG,
    parameters={
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "TMDB keywords — fine-grained content tags like "
                    "'time travel', 'heist', 'post-apocalyptic'. OR match."
                ),
            },
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["keywords"],
    },
)
def search_by_keyword(
    db: Session, ctx: AuthContext,
    keywords: list[str], limit: int = 10,
) -> dict:
    """Find titles tagged with any of these TMDB keywords. Case-insensitive
    match. Keywords are finer-grained than genres / themes (e.g. 'rogue AI',
    'bank heist', 'college comedy')."""
    needles = [k.lower() for k in keywords]
    rows = db.execute(
        text(
            """
            SELECT title_id, title, release_year,
                   content_type::text AS content_type,
                   genres, themes, tones, keywords,
                   maturity_rating::text AS maturity_rating,
                   fame, tmdb_rating, tagline
            FROM titles
            WHERE keywords IS NOT NULL
              AND maturity_rating::text = ANY(:allowed)
              AND EXISTS (
                SELECT 1 FROM unnest(keywords) AS k
                WHERE LOWER(k) = ANY(:needles)
              )
            ORDER BY fame DESC, tmdb_rating DESC NULLS LAST
            LIMIT :lim
            """
        ),
        {
            "needles": needles,
            "allowed": _allowed_maturities(ctx.maturity_ceiling),
            "lim": limit,
        },
    ).mappings().all()
    return {"keywords_queried": keywords, "results": [dict(r) for r in rows]}


@tool(
    scope=ToolScope.CATALOG,
    parameters={
        "type": "object",
        "properties": {
            "title_id": {
                "type": "string",
                "description": "Internal title_id (UUID) to look up cast/crew for.",
            },
            "cast_limit": {"type": "integer", "default": 8},
        },
        "required": ["title_id"],
    },
)
def get_title_cast(
    db: Session, ctx: AuthContext, title_id: str, cast_limit: int = 8,
) -> dict:
    """Get the top-billed cast + key crew (directors/writers/creators) for a
    specific title. Use when the user asks 'who's in it', 'who directed it',
    'who created the show'."""
    try:
        tid = uuid.UUID(title_id)
    except (ValueError, TypeError):
        return {"error": "invalid title_id"}

    title_row = db.execute(
        text(
            "SELECT title_id, title, release_year, tagline, tmdb_rating "
            "FROM titles WHERE title_id = :tid"
        ),
        {"tid": tid},
    ).mappings().one_or_none()
    if not title_row:
        return {"error": "title not found"}

    cast = db.execute(
        text(
            """
            SELECT p.name, p.photo_url, tc.character_name, tc.billing_order
            FROM title_cast tc
            JOIN people p ON p.person_id = tc.person_id
            WHERE tc.title_id = :tid
            ORDER BY tc.billing_order ASC
            LIMIT :lim
            """
        ),
        {"tid": tid, "lim": cast_limit},
    ).mappings().all()

    crew = db.execute(
        text(
            """
            SELECT p.name, tc.job, tc.department
            FROM title_crew tc
            JOIN people p ON p.person_id = tc.person_id
            WHERE tc.title_id = :tid
            ORDER BY tc.job
            """
        ),
        {"tid": tid},
    ).mappings().all()

    return {
        "title": dict(title_row),
        "cast": [dict(r) for r in cast],
        "crew": [dict(r) for r in crew],
    }
