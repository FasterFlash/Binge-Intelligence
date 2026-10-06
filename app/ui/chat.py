"""
Binge Intelligence — consumer chat (Streamlit, Netflix dark theme).

Two screens, one app:
    1. "Who's watching?" — multi-filter picker over the simulated user base
       (country, age range, archetype). Users render as cards; click to log in.
    2. Chat — starter prompt chips when empty, poster-card recommendations when
       the agent returns titles. Everything backend-y stays hidden.

Run:
    streamlit run app/ui/chat.py

Env:
    DATABASE_URL
    GROQ_API_KEY | GEMINI_API_KEY
    LLM_PROVIDER   (groq | gemini)
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_URL
from app.agent.auth import load_auth_context
from app.agent.orchestrator import Orchestrator
from app.agent.policy_rag import ensure_policies_indexed
from app.agent.providers import get_provider
from app.agent.session import SessionManager
from app.models.title import Base
# Register all tables
from app.models.user import User, Household  # noqa: F401
from app.models.subscription import SubscriptionPeriod  # noqa: F401
from app.models.behavior import (  # noqa: F401
    Session as WatchSession, WatchEvent, UserSeriesProgress,
)
from app.models.gold import (  # noqa: F401
    UserWatchStats, TitleEngagementStats, CalendarActivity, CohortMetrics,
)
from app.models.agent import AgentSession, AgentMessage, AgentEvent, MessageRole  # noqa: F401

from app.ui._theme import (
    BORDER, DIM, DIMMER, NETFLIX_RED, TEXT, inject_theme, poster_gradient,
)
from app.ui._prompts import STARTER_PROMPTS


# Thinking indicator — wrapped in a row so it aligns like an AI message
# (avatar on left, bubble sized to content). Animation defined in _theme.py.
_THINKING_HTML = """
<div class="bi-thinking-row">
  <div class="bi-avatar bi-avatar-ai">B</div>
  <div class="bi-thinking">
    <div class="bi-thinking-dots">
      <span></span><span></span><span></span>
    </div>
    <div class="bi-thinking-text">
      <span>Playing the ball around your area...</span>
      <span>Let me go through your profile...</span>
      <span>Scanning the catalog for your vibe...</span>
      <span>Lining up something you'd actually watch...</span>
    </div>
  </div>
</div>
"""


# ---------------------------------------------------------------------------
# Minimal markdown → safe HTML for chat bubbles
# ---------------------------------------------------------------------------
import html as _html
import re as _re


def _bubble_md(s: str) -> str:
    """
    Convert the agent's lightly-formatted output into bubble-safe HTML.
    Handles: bold (**), italics (* and _), inline code (`), bulleted lists
    (- / *), and paragraph breaks. HTML-escapes everything else.
    """
    if not s:
        return ""
    s = _html.escape(s)
    # Bold must come BEFORE italics (both use *)
    s = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = _re.sub(r'__(.+?)__', r'<strong>\1</strong>', s)
    # Italics
    s = _re.sub(r'\*([^*\n]+?)\*', r'<em>\1</em>', s)
    s = _re.sub(r'(?<![\w])_([^_\n]+?)_(?![\w])', r'<em>\1</em>', s)
    # Inline code
    s = _re.sub(r'`([^`\n]+?)`', r'<code>\1</code>', s)

    # Convert `- item` / `* item` lines into a <ul>
    lines = s.split('\n')
    out: list[str] = []
    in_list = False
    for ln in lines:
        m = _re.match(r'^\s*[-*]\s+(.+)$', ln)
        if m:
            if not in_list:
                out.append('<ul>')
                in_list = True
            out.append(f'<li>{m.group(1)}</li>')
        else:
            if in_list:
                out.append('</ul>')
                in_list = False
            out.append(ln)
    if in_list:
        out.append('</ul>')
    s = '\n'.join(out)

    # Paragraphs: split on 2+ newlines, join single newlines with <br/>
    parts = _re.split(r'\n{2,}', s)
    html_parts: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p.startswith('<ul>'):
            html_parts.append(p)
        else:
            html_parts.append(f'<p>{p.replace(chr(10), "<br/>")}</p>')
    return ''.join(html_parts)


def _render_bubble(role: str, content: str) -> None:
    """Render a chat bubble as pure HTML — proper right/left alignment."""
    is_user = role == "user"
    body = _bubble_md(content)
    if is_user:
        html = (
            '<div class="bi-row bi-row-user">'
            f'<div class="bi-bubble bi-bubble-user">{body}</div>'
            '<div class="bi-avatar bi-avatar-user">U</div>'
            '</div>'
        )
    else:
        html = (
            '<div class="bi-row bi-row-ai">'
            '<div class="bi-avatar bi-avatar-ai">B</div>'
            f'<div class="bi-bubble bi-bubble-ai">{body}</div>'
            '</div>'
        )
    st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Infra
# ---------------------------------------------------------------------------
@st.cache_resource
def get_engine():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as db:
        try:
            ensure_policies_indexed(db)
        except Exception as e:
            print(f"[policies] skipped: {e}")
    return engine


def _db() -> Session:
    return Session(get_engine(), expire_on_commit=False)


# ---------------------------------------------------------------------------
# Data lookups
# ---------------------------------------------------------------------------
@st.cache_data(ttl=120)
def filter_options() -> dict:
    with _db() as db:
        countries = [
            r[0] for r in db.execute(
                text("SELECT DISTINCT country::text FROM users ORDER BY country::text")
            ).all() if r[0]
        ]
        archetypes = [
            r[0] for r in db.execute(
                text("SELECT DISTINCT archetype::text FROM users ORDER BY archetype::text")
            ).all() if r[0]
        ]
        age_min, age_max = db.execute(
            text("SELECT MIN(age), MAX(age) FROM users")
        ).one()
        cy_min, cy_max = db.execute(
            text("SELECT MIN(cohort_year), MAX(cohort_year) FROM users")
        ).one()
        tw_min, tw_max = db.execute(
            text(
                "SELECT COALESCE(MIN(total_titles_watched), 0), "
                "       COALESCE(MAX(total_titles_watched), 0) "
                "FROM user_watch_stats"
            )
        ).one()
        # DISTINCT values across every user's top_3 arrays.
        # These columns are Postgres TEXT[] / VARCHAR[], NOT jsonb → use unnest.
        themes = [
            r[0] for r in db.execute(
                text(
                    """
                    SELECT DISTINCT unnest(top_3_themes) AS v
                    FROM user_watch_stats
                    WHERE top_3_themes IS NOT NULL
                    ORDER BY v
                    """
                )
            ).all() if r[0]
        ]
        tones = [
            r[0] for r in db.execute(
                text(
                    """
                    SELECT DISTINCT unnest(top_3_tones) AS v
                    FROM user_watch_stats
                    WHERE top_3_tones IS NOT NULL
                    ORDER BY v
                    """
                )
            ).all() if r[0]
        ]
    return {
        "countries": countries,
        "archetypes": archetypes,
        "age_min": int(age_min or 7),
        "age_max": int(age_max or 80),
        "cy_min": int(cy_min or 2015),
        "cy_max": int(cy_max or 2026),
        "tw_min": int(tw_min or 0),
        "tw_max": int(tw_max or 500),
        "themes": themes,
        "tones": tones,
    }


def search_users(
    countries: list[str],
    archetypes: list[str],
    age_range: tuple[int, int],
    cohort_range: tuple[int, int] | None = None,
    completion_min: float = 0.0,
    titles_range: tuple[int, int] | None = None,
    themes: list[str] | None = None,
    tones: list[str] | None = None,
    limit: int = 24,
) -> list[dict]:
    sql = """
        SELECT u.user_id, u.archetype, u.age, u.country, u.cohort_year,
               uws.top_3_genres, uws.top_3_themes, uws.top_3_tones,
               uws.active_days_per_week, uws.completion_rate_short,
               uws.total_titles_watched, uws.last_active_date
        FROM users u
        LEFT JOIN user_watch_stats uws ON u.user_id = uws.user_id
        WHERE u.age BETWEEN :age_min AND :age_max
    """
    params: dict = {"age_min": age_range[0], "age_max": age_range[1]}

    if countries:
        sql += " AND u.country::text = ANY(:countries)"
        params["countries"] = countries
    if archetypes:
        sql += " AND u.archetype::text = ANY(:archetypes)"
        params["archetypes"] = archetypes
    if cohort_range:
        sql += " AND u.cohort_year BETWEEN :cy_min AND :cy_max"
        params["cy_min"], params["cy_max"] = cohort_range
    if completion_min > 0:
        sql += " AND uws.completion_rate_short >= :comp_min"
        params["comp_min"] = completion_min
    if titles_range:
        sql += " AND uws.total_titles_watched BETWEEN :tw_min AND :tw_max"
        params["tw_min"], params["tw_max"] = titles_range
    if themes:
        # top_3_themes is Postgres varchar[]; `&&` wants matching element types,
        # and psycopg2 sends :themes as text[]. Cast the COLUMN to text[] so
        # both sides agree. Semantics unchanged: "has at least one in common".
        sql += " AND uws.top_3_themes::text[] && CAST(:themes AS text[])"
        params["themes"] = themes
    if tones:
        sql += " AND uws.top_3_tones::text[] && CAST(:tones AS text[])"
        params["tones"] = tones

    # Most-active users first (feels less like a random sample)
    sql += (
        " ORDER BY uws.last_active_date DESC NULLS LAST,"
        "          uws.total_titles_watched DESC NULLS LAST,"
        "          u.user_id"
        " LIMIT :lim"
    )
    params["lim"] = limit

    with _db() as db:
        rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def load_history(session_id: uuid.UUID) -> list[dict]:
    with _db() as db:
        rows = db.execute(
            text(
                """
                SELECT role, content, tool_name, tool_result, turn_num, created_at
                FROM agent_messages
                WHERE session_id = :sid
                ORDER BY turn_num ASC, created_at ASC
                """
            ),
            {"sid": session_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def list_user_sessions_with_titles(user_id: uuid.UUID, limit: int = 40) -> list[dict]:
    """
    List this user's past sessions, each annotated with a derived title (the
    first user message, truncated) + turn count + last_active_at.
    """
    with _db() as db:
        rows = db.execute(
            text(
                """
                SELECT
                    s.session_id, s.started_at, s.last_active_at,
                    s.turn_count, s.status::text AS status,
                    (
                        SELECT content FROM agent_messages m
                        WHERE m.session_id = s.session_id AND m.role = 'user'
                        ORDER BY m.turn_num ASC LIMIT 1
                    ) AS first_user_msg
                FROM agent_sessions s
                WHERE s.user_id = :uid
                ORDER BY s.last_active_at DESC
                LIMIT :lim
                """
            ),
            {"uid": user_id, "lim": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


def continue_watching(user_id: uuid.UUID, limit: int = 4) -> list[dict]:
    """In-progress series for the Continue Watching rail."""
    with _db() as db:
        rows = db.execute(
            text(
                """
                SELECT t.title_id, t.title, t.release_year,
                       t.genres, t.maturity_rating::text AS maturity_rating,
                       t.poster_url, t.backdrop_url,
                       usp.current_season, usp.current_episode,
                       usp.last_watched_at,
                       usp.total_episodes_watched
                FROM user_series_progress usp
                JOIN titles t ON t.title_id = usp.title_id
                WHERE usp.user_id = :uid
                  AND usp.state::text = 'in_progress'
                ORDER BY usp.last_watched_at DESC NULLS LAST
                LIMIT :lim
                """
            ),
            {"uid": user_id, "lim": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


def fetch_title_detail(title_id: uuid.UUID) -> dict | None:
    """Everything the title deep-dive needs — title row + top cast + crew."""
    with _db() as db:
        title = db.execute(
            text(
                """
                SELECT title_id, title, release_year,
                       content_type::text AS content_type,
                       runtime_minutes, episode_count, season_count,
                       country, original_language,
                       maturity_rating::text AS maturity_rating,
                       genres, themes, tones, keywords,
                       synopsis, tagline, tmdb_rating, tmdb_vote_count,
                       poster_url, backdrop_url, fame,
                       is_platform_original, platform_add_date
                FROM titles
                WHERE title_id = :tid
                """
            ),
            {"tid": title_id},
        ).mappings().one_or_none()
        if not title:
            return None

        cast = db.execute(
            text(
                """
                SELECT p.name, p.photo_url, tc.character_name,
                       tc.billing_order
                FROM title_cast tc
                JOIN people p ON p.person_id = tc.person_id
                WHERE tc.title_id = :tid
                ORDER BY tc.billing_order ASC
                LIMIT 10
                """
            ),
            {"tid": title_id},
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
            {"tid": title_id},
        ).mappings().all()

    return {
        "title": dict(title),
        "cast": [dict(r) for r in cast],
        "crew": [dict(r) for r in crew],
    }


def last_watched_titles(user_id: uuid.UUID, limit: int = 6) -> list[dict]:
    """
    Most recent distinct titles the user has watched — movies and series alike.
    Powers the 'Last watched' poster rail on the chat empty state.
    """
    with _db() as db:
        rows = db.execute(
            text(
                """
                WITH latest AS (
                    SELECT title_id, MAX(started_at) AS last_watched_at
                    FROM watch_events
                    WHERE user_id = :uid
                    GROUP BY title_id
                )
                SELECT t.title_id, t.title, t.release_year,
                       t.content_type::text AS content_type,
                       t.genres,
                       t.maturity_rating::text AS maturity_rating,
                       t.poster_url, t.backdrop_url,
                       l.last_watched_at
                FROM latest l
                JOIN titles t ON t.title_id = l.title_id
                ORDER BY l.last_watched_at DESC
                LIMIT :lim
                """
            ),
            {"uid": user_id, "lim": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
def _init_state() -> None:
    for key, default in {
        "stage": "picker",
        "user_id": None,
        "session_id": None,
        "pending_prompt": None,
        "filters": None,
        "search_results": None,
        "focused_title_id": None,
    }.items():
        if key not in st.session_state:
            st.session_state[key] = default


# ---------------------------------------------------------------------------
# UI pieces
# ---------------------------------------------------------------------------
def _taste_blurb(row: dict) -> str:
    bits = []
    tones = row.get("top_3_tones") or []
    genres = row.get("top_3_genres") or []
    if tones:
        bits.append(f"loves {', '.join(tones[:2]).lower()}")
    if genres:
        bits.append(f"watches {', '.join(genres[:2]).lower()}")
    comp = row.get("completion_rate_short")
    if comp is not None:
        bits.append(f"{int(float(comp) * 100)}% completion")
    act = row.get("active_days_per_week")
    if act is not None:
        bits.append(f"{float(act):.1f} days/wk active")
    return " · ".join(bits) if bits else "new to the platform"


def _render_user_card(col, row: dict) -> None:
    """
    Render a user card as a full hyperlink to ?select_user=<uid>. The picker
    screen reads that query param at the top of its run and uses it to log in.
    No separate button — the whole card is the click target.
    """
    uid = row["user_id"]
    arch = row["archetype"].replace("_", " ").title()
    taste = _taste_blurb(row)
    with col:
        st.markdown(
            f"""
            <a href="?select_user={uid}" class="bi-user-link">
                <div class="bi-user-card">
                    <span class="bi-badge">{arch}</span>
                    <span class="bi-badge bi-badge-ghost" style="margin-left:6px;">
                        {row['country']} · age {row['age']}
                    </span>
                    <div class="bi-user-name">User · {str(uid)[:8]}</div>
                    <div class="bi-user-meta">
                        Cohort {row['cohort_year']} · {row.get('total_titles_watched') or 0} titles watched
                    </div>
                    <div class="bi-user-taste">{taste}</div>
                </div>
            </a>
            """,
            unsafe_allow_html=True,
        )


def _render_poster(col, title_row: dict) -> None:
    name = title_row.get("title", "Untitled")
    year = title_row.get("release_year", "")
    rating = title_row.get("maturity_rating", "")
    genres = title_row.get("genres") or []
    if isinstance(genres, str):
        try:
            genres = json.loads(genres)
        except Exception:
            genres = [genres]
    c1, c2 = poster_gradient(name)
    genre_chips = "".join(f'<span class="bi-chip">{g}</span>' for g in genres[:3])
    maturity_badge = (
        f'<div class="bi-poster-maturity">{rating}</div>' if rating else ""
    )

    # Real poster image from MinIO when available; CSS gradient is the fallback
    poster_url = title_row.get("poster_url")
    if poster_url:
        bg_style = (
            f"background-image: linear-gradient(180deg, rgba(0,0,0,0.0) 40%, "
            f"rgba(0,0,0,0.85) 100%), url('{poster_url}'); "
            f"background-size: cover; background-position: center;"
        )
        extra_class = " bi-poster-img"
    else:
        bg_style = f"--c1:{c1}; --c2:{c2};"
        extra_class = ""

    tid = title_row.get("title_id")
    with col:
        link_open = f'<a href="?title={tid}" class="bi-poster-link">' if tid else ""
        link_close = "</a>" if tid else ""
        st.markdown(
            f'{link_open}'
            f'<div class="bi-poster{extra_class}" style="{bg_style}">'
            f'{maturity_badge}'
            f'<div>'
            f'<div class="bi-poster-title">{name}</div>'
            f'<div class="bi-poster-year">{year}</div>'
            f'</div>'
            f'<div style="margin-top:12px;">{genre_chips}</div>'
            f'</div>'
            f'{link_close}',
            unsafe_allow_html=True,
        )


def _group_sessions_by_date(sessions: list[dict]) -> dict[str, list[dict]]:
    """Group sessions into Today / Yesterday / Last 7 days / Older buckets."""
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    today = now.date()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)

    groups: dict[str, list[dict]] = {
        "Today": [],
        "Yesterday": [],
        "Last 7 days": [],
        "Older": [],
    }
    for s in sessions:
        d = s["last_active_at"].date() if s.get("last_active_at") else today
        if d == today:
            groups["Today"].append(s)
        elif d == yesterday:
            groups["Yesterday"].append(s)
        elif d >= week_ago:
            groups["Last 7 days"].append(s)
        else:
            groups["Older"].append(s)
    return {k: v for k, v in groups.items() if v}


def _session_title(session_row: dict) -> str:
    """Derive a human title from the first user message."""
    msg = (session_row.get("first_user_msg") or "").strip()
    if not msg:
        return "New conversation"
    if len(msg) > 60:
        return msg[:57] + "…"
    return msg


def _relative_time(when) -> str:
    from datetime import datetime
    delta = datetime.utcnow() - when
    s = int(delta.total_seconds())
    if s < 60:    return "just now"
    if s < 3600:  return f"{s // 60}m ago"
    if s < 86400: return f"{s // 3600}h ago"
    d = s // 86400
    if d < 7:  return f"{d}d ago"
    return when.strftime("%b %d")


def _render_history_drawer(user_id: uuid.UUID,
                           current_session_id: uuid.UUID) -> None:
    """Collapsible drawer listing the user's past chats."""
    sessions = list_user_sessions_with_titles(user_id, limit=40)
    if not sessions:
        st.markdown(
            '<div class="bi-history-wrap">'
            '<div style="color:#808080; font-size:13px; text-align:center;">'
            'No past chats yet.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    groups = _group_sessions_by_date(sessions)
    html_parts: list[str] = ['<div class="bi-history-wrap">']
    for group_label, group_sessions in groups.items():
        html_parts.append(
            f'<div class="bi-date-group">{group_label}</div>'
        )
        for s in group_sessions:
            sid = s["session_id"]
            title = _escape(_session_title(s))
            turns = s["turn_count"] or 0
            when = _relative_time(s["last_active_at"])
            active_cls = " active" if str(sid) == str(current_session_id) else ""
            html_parts.append(
                f'<a href="?resume={sid}" class="bi-history-link">'
                f'<div class="bi-history-item{active_cls}">'
                f'<div class="bi-history-title">{title}</div>'
                f'<span class="bi-history-turns">{turns}</span>'
                f'<span class="bi-history-meta">{when}</span>'
                f'</div></a>'
            )
    html_parts.append('</div>')
    st.markdown("".join(html_parts), unsafe_allow_html=True)


def _render_last_watched(user_id: uuid.UUID) -> None:
    """Horizontal rail of the user's most recently watched titles — real
    TMDB posters, click any to seed a prompt about it."""
    titles = last_watched_titles(user_id, limit=6)
    if not titles:
        return

    st.markdown(
        '<div class="bi-rail-label">Last watched</div>',
        unsafe_allow_html=True,
    )

    cols = st.columns(6, gap="small")
    for i, t in enumerate(titles[:6]):
        name = _escape(t["title"])
        year = t.get("release_year", "")
        ctype_full = (t.get("content_type") or "").lower()
        ctype_label = {"movie": "FILM", "series": "SERIES"}.get(ctype_full, "")
        tid = t.get("title_id")

        poster = t.get("poster_url") or t.get("backdrop_url")
        if poster:
            tile_style = f"background-image: url('{poster}');"
        else:
            c1, c2 = poster_gradient(t["title"])
            tile_style = f"--c1:{c1}; --c2:{c2};"

        with cols[i]:
            st.markdown(
                f'<a href="?title={tid}" class="bi-lw-link">'
                f'<div class="bi-lw-tile" style="{tile_style}">'
                f'<div class="bi-lw-type-badge">{ctype_label}</div>'
                f'<div class="bi-lw-caption">'
                f'<div class="bi-lw-title">{name}</div>'
                f'<div class="bi-lw-meta">{year}</div>'
                f'</div>'
                f'</div></a>',
                unsafe_allow_html=True,
            )


def _render_continue_watching(user_id: uuid.UUID) -> None:
    """Netflix-style 'Continue Watching' rail — in-progress series only."""
    titles = continue_watching(user_id, limit=4)
    if not titles:
        return

    st.markdown(
        '<div class="bi-rail-label">Continue watching</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(4, gap="small")
    for i, t in enumerate(titles[:4]):
        name = _escape(t["title"])
        year = t.get("release_year", "")
        season = t.get("current_season") or 1
        episode = t.get("current_episode") or 1
        total_watched = t.get("total_episodes_watched") or 0
        pct = min(100, int((total_watched / max(total_watched + 3, 10)) * 100))
        prompt = f"Where did I leave off on {t['title']}?"

        # Prefer backdrop (wider aspect) for the rail; fall back to poster,
        # then gradient
        bg_url = t.get("backdrop_url") or t.get("poster_url")
        if bg_url:
            card_style = (
                f"background-image: linear-gradient(180deg, rgba(0,0,0,0.0) 30%, "
                f"rgba(0,0,0,0.9) 100%), url('{bg_url}'); "
                f"background-size: cover; background-position: center;"
            )
        else:
            c1, c2 = poster_gradient(t["title"])
            card_style = f"--c1:{c1}; --c2:{c2};"

        with cols[i]:
            st.markdown(
                f'<a href="?cw={_escape(prompt)}" class="bi-rail-link">'
                f'<div class="bi-rail-card" style="{card_style}">'
                f'<div>'
                f'<div class="bi-rail-title">{name}</div>'
                f'<div class="bi-rail-progress">S{season} · E{episode} · {year}</div>'
                f'</div>'
                f'<div class="bi-rail-bar">'
                f'<div class="bi-rail-bar-fill" style="width:{pct}%;"></div>'
                f'</div>'
                f'</div></a>',
                unsafe_allow_html=True,
            )


def _followup_chips_for(last_turn_had_titles: bool) -> list[str]:
    """Choose contextual follow-up prompts based on what just happened."""
    base = [
        "Tell me more",
        "Something different",
        "Make it shorter",
    ]
    if last_turn_had_titles:
        base.insert(0, "More like these")
        base.insert(1, "Tell me about #1")
    return base[:4]


def _escape(s: str) -> str:
    return _html.escape(str(s or ""))


def _extract_titles_from_history(history: list[dict], turn_num: int) -> list[dict]:
    """
    Peek at tool_result rows at this turn and pull out a list of title dicts
    if any tool returned one. Keeps the UI decoupled from tool names.
    """
    titles: list[dict] = []
    for m in history:
        if m["turn_num"] != turn_num or m["role"] != MessageRole.TOOL_RESULT.value:
            continue
        content = m.get("content") or ""
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        # Common keys that carry title lists
        for key in (
            "recommendations", "results", "popular_titles",
            "new_releases", "leaving_soon", "driving_titles",
            "completed", "in_progress", "abandoned", "recent",
        ):
            maybe = parsed.get(key)
            if isinstance(maybe, list) and maybe and isinstance(maybe[0], dict):
                for t in maybe:
                    if "title" in t:
                        titles.append(t)
                if titles:
                    break
    # Dedup by title
    seen = set()
    out = []
    for t in titles:
        if t["title"] in seen:
            continue
        seen.add(t["title"])
        out.append(t)
    out = out[:6]

    # Hydrate poster_url / backdrop_url for any extracted titles that have a
    # title_id. Tool responses from the agent don't carry these columns, but
    # we store them on the titles table from the TMDB sync.
    ids = [t["title_id"] for t in out if t.get("title_id")]
    if ids:
        with _db() as db:
            rows = db.execute(
                text(
                    "SELECT title_id, poster_url, backdrop_url "
                    "FROM titles WHERE title_id = ANY(:ids)"
                ),
                {"ids": [str(i) for i in ids]},
            ).mappings().all()
        urls = {str(r["title_id"]): dict(r) for r in rows}
        for t in out:
            u = urls.get(str(t.get("title_id") or ""))
            if u:
                t.setdefault("poster_url", u["poster_url"])
                t.setdefault("backdrop_url", u["backdrop_url"])
    return out


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------
def screen_picker() -> None:
    # ---- Clickable-card login: read ?select_user=<uid> from URL ----
    qp_uid = st.query_params.get("select_user")
    if qp_uid:
        try:
            st.session_state.user_id = uuid.UUID(qp_uid)
            st.session_state.session_id = None
            st.session_state.stage = "chat"
        except (ValueError, TypeError):
            pass
        # Clear so a refresh doesn't re-trigger
        st.query_params.clear()
        st.rerun()

    st.markdown(
        '<div class="bi-brandbar">'
        '<span class="bi-logo">B</span>'
        '<span class="bi-logo-text">Binge Intelligence</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
    st.markdown('<div class="bi-hero">Who\'s watching?</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="bi-sub">Pick the user you want to log in as. '
        'Filter by country, age, taste type, cohort, how much they watch, '
        'and the themes or tones they lean into.</div>',
        unsafe_allow_html=True,
    )

    opts = filter_options()

    # ---- Filter row 1 ----
    c1, c2, c3 = st.columns(3)
    with c1:
        countries = st.multiselect("Country", opts["countries"], default=[])
    with c2:
        age_range = st.slider(
            "Age range",
            min_value=opts["age_min"], max_value=opts["age_max"],
            value=(opts["age_min"], opts["age_max"]),
        )
    with c3:
        archetypes = st.multiselect(
            "Taste type",
            opts["archetypes"], default=[],
            format_func=lambda s: s.replace("_", " ").title(),
        )

    # ---- Filter row 2 ----
    c4, c5, c6 = st.columns(3)
    with c4:
        cohort_range = st.slider(
            "Cohort year",
            min_value=opts["cy_min"], max_value=opts["cy_max"],
            value=(opts["cy_min"], opts["cy_max"]),
        )
    with c5:
        titles_range = st.slider(
            "Titles watched",
            min_value=opts["tw_min"], max_value=opts["tw_max"],
            value=(opts["tw_min"], opts["tw_max"]),
        )
    with c6:
        completion_pct = st.slider(
            "Min finish rate (%)",
            min_value=0, max_value=100, value=0, step=5,
        )

    # ---- Filter row 3 ----
    c7, c8 = st.columns(2)
    with c7:
        themes = st.multiselect(
            "Themes they lean into", opts["themes"], default=[],
        )
    with c8:
        tones = st.multiselect(
            "Tones they lean into", opts["tones"], default=[],
        )

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    st.markdown('<div class="bi-primary">', unsafe_allow_html=True)
    find = st.button("Find users", use_container_width=False)
    st.markdown("</div>", unsafe_allow_html=True)

    if find or st.session_state.search_results is not None:
        if find:
            st.session_state.search_results = search_users(
                countries=countries,
                archetypes=archetypes,
                age_range=age_range,
                cohort_range=cohort_range,
                completion_min=completion_pct / 100.0 if completion_pct > 0 else 0.0,
                titles_range=titles_range,
                themes=themes or None,
                tones=tones or None,
                limit=24,
            )

        results = st.session_state.search_results or []
        st.markdown(
            f'<div class="bi-section">{len(results)} users match · click a card to log in</div>',
            unsafe_allow_html=True,
        )
        if not results:
            st.caption("No users match these filters — loosen them a bit.")
        else:
            # 3-column grid
            for i in range(0, len(results), 3):
                cols = st.columns(3, gap="medium")
                for j, row in enumerate(results[i:i + 3]):
                    _render_user_card(cols[j], row)


def screen_chat() -> None:
    user_id = st.session_state.user_id

    # ---- Query-param handlers (hyperlink-click routing) ----
    # ?title=<uuid>  → open the title deep-dive view
    qp_title = st.query_params.get("title")
    if qp_title:
        try:
            st.session_state.focused_title_id = uuid.UUID(qp_title)
        except (ValueError, TypeError):
            pass
        st.query_params.clear()
        st.rerun()
    # ?resume=<uuid>  → load that past session into the current view
    qp_resume = st.query_params.get("resume")
    if qp_resume:
        try:
            st.session_state.session_id = uuid.UUID(qp_resume)
            st.session_state.show_history = False
        except (ValueError, TypeError):
            pass
        st.query_params.clear()
        st.rerun()
    # ?cw=<prompt>  → "Continue watching" card seeds a user prompt
    qp_cw = st.query_params.get("cw")
    if qp_cw:
        st.session_state.pending_prompt = qp_cw
        st.query_params.clear()
        st.rerun()

    with _db() as db:
        ctx = load_auth_context(db, user_id)
        # AUTO-RESUME: on login, pick up the user's most recent active session.
        # Only start a fresh one if they have no prior sessions, or they
        # explicitly clicked "New" (which clears session_id before rerun).
        if st.session_state.session_id is None:
            existing = SessionManager.latest_for_user(db, user_id)
            if existing is not None:
                sm = existing
            else:
                sm = SessionManager.start_new(db, user_id)
            st.session_state.session_id = sm.session_id

    # Open the chat-view wrapper. All chat-specific CSS lives under this class.
    st.markdown('<div class="bi-chat-view">', unsafe_allow_html=True)

    # Center the whole chat column — gives it a WhatsApp / ChatGPT narrow feel
    _, mid, _ = st.columns([1, 6, 1])
    with mid:
        # ---- Compact chat header ----
        arch = ctx.archetype.replace("_", " ").title()
        hcol_left, hcol_actions = st.columns([5, 3])
        with hcol_left:
            st.markdown(
                f'''
                <div class="bi-chat-header-left" style="padding-top:6px;">
                    <span class="bi-logo bi-logo-sm">B</span>
                    <div class="bi-chat-ctx">
                        <div class="bi-chat-ctx-name">
                            <span class="bi-chat-dot"></span>{arch}
                        </div>
                        <div class="bi-chat-ctx-sub">
                            age {ctx.age} · {ctx.country} · {ctx.maturity_ceiling}
                        </div>
                    </div>
                </div>
                ''',
                unsafe_allow_html=True,
            )
        with hcol_actions:
            ac_hist, ac_new, ac_switch = st.columns(3)
            with ac_hist:
                show_hist = st.session_state.get("show_history", False)
                hist_label = "Hide" if show_hist else "History"
                if st.button(hist_label, use_container_width=True,
                             key="btn_history"):
                    st.session_state.show_history = not show_hist
                    st.rerun()
            with ac_new:
                if st.button("New", use_container_width=True, key="btn_new_chat"):
                    st.session_state.session_id = None
                    st.session_state.show_history = False
                    st.rerun()
            with ac_switch:
                if st.button("Switch", use_container_width=True, key="btn_switch_user"):
                    st.session_state.stage = "picker"
                    st.session_state.user_id = None
                    st.session_state.session_id = None
                    st.session_state.show_history = False
                    st.rerun()

        st.markdown(
            f'<hr style="border-color:{BORDER}; margin:10px 0 18px 0; opacity:0.5;"/>',
            unsafe_allow_html=True,
        )

        # History drawer (toggled by the header button)
        if st.session_state.get("show_history", False):
            _render_history_drawer(user_id, st.session_state.session_id)

        # Chat history
        history = load_history(st.session_state.session_id)
        user_assistant_turns = [
            m for m in history
            if m["role"] in (MessageRole.USER.value, MessageRole.ASSISTANT.value)
        ]

        # Empty state → Last Watched + starter chips
        if not user_assistant_turns:
            # Force scroll-to-top: Streamlit auto-scrolls the main container
            # to the bottom whenever a chat_input is present (so the user sees
            # the latest message). On the empty state there's no message yet,
            # so the hero + chips end up off-screen above. This zero-height
            # component runs in an iframe and reaches up to the parent window
            # to reset the scroll position.
            components.html(
                """
                <script>
                  const scrollUp = () => {
                    try {
                      const w = window.parent;
                      w.scrollTo(0, 0);
                      const sel = [
                        '[data-testid="stAppViewContainer"]',
                        '[data-testid="stMain"]',
                        '.main',
                        'section.main',
                      ];
                      for (const s of sel) {
                        const el = w.document.querySelector(s);
                        if (el) el.scrollTo(0, 0);
                      }
                    } catch (e) {}
                  };
                  scrollUp();
                  setTimeout(scrollUp, 60);
                  setTimeout(scrollUp, 200);
                </script>
                """,
                height=0,
            )
            st.markdown(
                '<div class="bi-hero-sm">What do you feel like watching?</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div class="bi-sub">Pick a question to get started, or type your own.</div>',
                unsafe_allow_html=True,
            )
            # Last Watched rail — real poster tiles of the user's recent titles
            _render_last_watched(user_id)

            for category, prompts in STARTER_PROMPTS.items():
                st.markdown(
                    f'<div class="bi-section">{category}</div>',
                    unsafe_allow_html=True,
                )
                for i in range(0, len(prompts), 2):
                    cols = st.columns(2)
                    for j, p in enumerate(prompts[i:i + 2]):
                        with cols[j]:
                            if st.button(p, key=f"starter_{category}_{i+j}",
                                         use_container_width=True):
                                st.session_state.pending_prompt = p
                                st.rerun()

        # Render chat history as custom bubbles — user on the right, AI left.
        last_assistant_had_titles = False
        for idx, m in enumerate(user_assistant_turns):
            role = "user" if m["role"] == MessageRole.USER.value else "assistant"
            content = (m["content"] or "").strip()
            if role == "assistant" and not content:
                content = (
                    "Hmm, that one slipped past me. Want to rephrase it, "
                    "or ask me something else?"
                )
            _render_bubble(role, content)
            # Posters below the AI bubble if that turn had title results
            if role == "assistant":
                titles = _extract_titles_from_history(history, m["turn_num"])
                last_assistant_had_titles = bool(titles)
                if titles:
                    st.markdown(
                        '<div class="bi-section" style="margin-top:14px;">'
                        'Picked for you'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    for i in range(0, len(titles), 2):
                        pcols = st.columns(2, gap="small")
                        for j, t in enumerate(titles[i:i + 2]):
                            _render_poster(pcols[j], t)

        # Follow-up chips — show only if the LAST message was from the AI
        # (so there's something for the user to react to).
        if (user_assistant_turns and
                user_assistant_turns[-1]["role"] == MessageRole.ASSISTANT.value):
            followups = _followup_chips_for(last_assistant_had_titles)
            st.markdown(
                '<div class="bi-followup-wrap">'
                '<div class="bi-followup-label">Quick follow-ups</div>'
                '</div>',
                unsafe_allow_html=True,
            )
            fcols = st.columns(len(followups))
            for i, p in enumerate(followups):
                with fcols[i]:
                    if st.button(p, key=f"followup_{i}",
                                 use_container_width=True):
                        st.session_state.pending_prompt = p
                        st.rerun()

    # Close the chat-view wrapper so the input bar below is unaffected by
    # chat-only CSS rules.
    st.markdown('</div>', unsafe_allow_html=True)
    # TMDB attribution (required by their TOS when using their API)
    st.markdown(
        '<div style="text-align:center; margin:28px auto 90px auto; '
        'color:#555; font-size:10px; letter-spacing:0.1em; '
        'text-transform:uppercase;">'
        'This product uses the TMDB API but is not endorsed or certified by TMDB.'
        '</div>',
        unsafe_allow_html=True,
    )

    # Process pending prompt from a chip click
    prompt = st.session_state.pending_prompt
    st.session_state.pending_prompt = None

    # Chat input (stays bottom-anchored by Streamlit — CSS narrows it to a pill)
    typed = st.chat_input("Message Binge Intelligence...")
    if typed:
        prompt = typed

    if prompt:
        # Re-open the chat-view wrapper so the user bubble + thinking indicator
        # inherit the chat styling.
        st.markdown('<div class="bi-chat-view">', unsafe_allow_html=True)
        _, mid2, _ = st.columns([1, 6, 1])
        with mid2:
            # 1) User's message lands immediately on the right.
            _render_bubble("user", prompt)

            # 2) Rotating thinking indicator (shown until first token arrives).
            thinking_slot = st.empty()
            thinking_slot.markdown(_THINKING_HTML, unsafe_allow_html=True)

            # 3) Streaming slot — the thinking indicator swaps to a live
            #    AI bubble on the first chunk, then grows as tokens stream.
            stream_slot = st.empty()
            # Mutable accumulator (list of str) + first-chunk latch (list[bool])
            # so the closure can mutate without a `nonlocal` declaration.
            _acc: list[str] = []
            _started: list[bool] = [False]

            def _on_chunk(piece: str) -> None:
                if not piece:
                    return
                if not _started[0]:
                    thinking_slot.empty()
                    _started[0] = True
                _acc.append(piece)
                body = _bubble_md("".join(_acc))
                stream_slot.markdown(
                    '<div class="bi-row bi-row-ai">'
                    '<div class="bi-avatar bi-avatar-ai">B</div>'
                    f'<div class="bi-bubble bi-bubble-ai">{body}</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )

        # 4) Call the agent. Pass the streaming callback so the final answer
        #    tokens render progressively. On failure, show a friendly bubble.
        failed = False
        with _db() as db:
            try:
                ctx = load_auth_context(db, user_id)
                sm = SessionManager.resume(db, st.session_state.session_id)
                provider = get_provider()
                orch = Orchestrator(db, ctx, sm, provider)
                orch.ask(prompt, stream_callback=_on_chunk)
            except Exception as e:
                print(f"[chat] agent error: {e}")
                failed = True

        thinking_slot.empty()
        # Keep the streamed bubble visible for a moment, then let the rerun
        # replace it with the committed version from history.
        stream_slot.empty()

        if failed:
            with mid2:
                _render_bubble(
                    "assistant",
                    "I'm catching my breath — a lot going on right now. "
                    "Give me a sec and ask again?",
                )
            import time
            time.sleep(1.8)

        # Close the second chat-view wrapper before rerun
        st.markdown('</div>', unsafe_allow_html=True)
        st.rerun()


# ---------------------------------------------------------------------------
# Title deep-dive screen
# ---------------------------------------------------------------------------
def screen_title_detail(title_id: uuid.UUID) -> None:
    data = fetch_title_detail(title_id)
    if not data:
        st.error("Title not found.")
        if st.button("← Back to chat"):
            st.session_state.focused_title_id = None
            st.rerun()
        return

    t = data["title"]
    cast = data["cast"]
    crew = data["crew"]

    directors = [c["name"] for c in crew
                 if c["job"] in ("Director", "Creator", "Showrunner")][:3]

    name = _escape(t["title"])
    year = t.get("release_year", "")
    rating = t.get("maturity_rating", "")
    tmdb_rating = t.get("tmdb_rating")
    rating_block = (
        f'<div class="bi-td-rating">★ {tmdb_rating:.1f}'
        f'<span class="bi-td-rating-sub"> / 10</span></div>'
        if tmdb_rating else ""
    )
    tagline = _escape(t.get("tagline") or "")
    synopsis = _escape(t.get("synopsis") or "")
    genres = t.get("genres") or []
    runtime = t.get("runtime_minutes")
    ep_count = t.get("episode_count")
    season_count = t.get("season_count")

    meta_bits: list[str] = []
    if year: meta_bits.append(str(year))
    if rating: meta_bits.append(rating)
    if t.get("content_type") == "movie" and runtime:
        meta_bits.append(f"{runtime} min")
    if t.get("content_type") == "series" and season_count:
        meta_bits.append(f"{season_count} season{'s' if season_count > 1 else ''}")
    meta_line = " · ".join(meta_bits)

    genre_chips = "".join(f'<span class="bi-chip">{g}</span>' for g in genres[:5])

    backdrop = t.get("backdrop_url") or t.get("poster_url")
    hero_bg = (
        f"background-image: linear-gradient(180deg, rgba(10,10,10,0.4) 0%, "
        f"rgba(10,10,10,0.95) 100%), url('{backdrop}'); "
        f"background-size: cover; background-position: center;"
        if backdrop else "background: #1a1a1a;"
    )

    poster = t.get("poster_url")

    # ---- Header: back button + logo ----
    hcol_left, _ = st.columns([2, 6])
    with hcol_left:
        if st.button("← Back to chat", key="back_from_detail",
                     use_container_width=False):
            st.session_state.focused_title_id = None
            st.rerun()

    # ---- Hero section ----
    poster_html = (
        f'<div class="bi-td-poster" '
        f'style="background-image: url(\'{poster}\'); '
        f'background-size: cover; background-position: center;"></div>'
        if poster else '<div class="bi-td-poster"></div>'
    )
    directors_html = (
        f'<div class="bi-td-director">By {", ".join(_escape(d) for d in directors)}</div>'
        if directors else ""
    )
    tagline_html = (
        f'<div class="bi-td-tagline">"{tagline}"</div>' if tagline else ""
    )
    st.markdown(
        f'<div class="bi-td-hero" style="{hero_bg}">'
        f'<div class="bi-td-hero-inner">'
        f'{poster_html}'
        f'<div class="bi-td-text">'
        f'<div class="bi-td-title">{name}</div>'
        f'<div class="bi-td-meta">{meta_line}</div>'
        f'{tagline_html}'
        f'{directors_html}'
        f'{rating_block}'
        f'<div style="margin-top:10px;">{genre_chips}</div>'
        f'</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ---- Synopsis ----
    if synopsis:
        st.markdown('<div class="bi-section">Synopsis</div>',
                    unsafe_allow_html=True)
        st.markdown(
            f'<div class="bi-td-synopsis">{synopsis}</div>',
            unsafe_allow_html=True,
        )

    # ---- Cast carousel ----
    if cast:
        st.markdown('<div class="bi-section">Cast</div>',
                    unsafe_allow_html=True)
        n = min(len(cast), 8)
        cols = st.columns(n, gap="small")
        for i, member in enumerate(cast[:n]):
            with cols[i]:
                photo = member.get("photo_url")
                photo_html = (
                    f'<div class="bi-td-cast-photo" '
                    f'style="background-image: url(\'{photo}\');"></div>'
                    if photo else
                    '<div class="bi-td-cast-photo bi-td-cast-photo-blank"></div>'
                )
                st.markdown(
                    f'<div class="bi-td-cast-card">'
                    f'{photo_html}'
                    f'<div class="bi-td-cast-name">{_escape(member["name"])}</div>'
                    f'<div class="bi-td-cast-role">{_escape(member.get("character_name") or "")}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # ---- Follow-up prompt chips tied to this title ----
    st.markdown('<div class="bi-section">Ask about this one</div>',
                unsafe_allow_html=True)
    follow_prompts = [
        f"Something similar to {t['title']}",
        f"Why would I like {t['title']}?",
        f"Who else is in {t['title']}?",
    ]
    fcols = st.columns(len(follow_prompts))
    for i, p in enumerate(follow_prompts):
        with fcols[i]:
            if st.button(p, key=f"td_follow_{i}", use_container_width=True):
                st.session_state.focused_title_id = None
                st.session_state.pending_prompt = p
                st.rerun()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(
        page_title="Binge Intelligence",
        page_icon=None,
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_theme()
    _init_state()

    if st.session_state.stage == "picker" or st.session_state.user_id is None:
        screen_picker()
    elif st.session_state.focused_title_id is not None:
        # Title deep-dive wraps the chat-view class so bubbles / avatars /
        # input get the same styling if any happen to appear.
        st.markdown('<div class="bi-chat-view">', unsafe_allow_html=True)
        screen_title_detail(st.session_state.focused_title_id)
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        screen_chat()


if __name__ == "__main__":
    main()
else:
    main()
