"""
Binge Intelligence — Dev Audit Dashboard.

READ-ONLY observability over everything the consumer chat produces. No user
switching, no chatting from here. Just:

    [ KPI tiles for the selected time window ]
    [ Session list — one row per conversation across all users ]
       └─ click → session detail (transcript + event timeline)

Source of truth: agent_sessions, agent_messages, agent_events (Postgres).

Run (second port so it can live alongside the consumer chat):
    streamlit run app/ui/streamlit_app.py --server.port 8502
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_URL
from app.models.title import Base
# Register tables
from app.models.user import User, Household  # noqa: F401
from app.models.subscription import SubscriptionPeriod  # noqa: F401
from app.models.behavior import (  # noqa: F401
    Session as WatchSession, WatchEvent, UserSeriesProgress,
)
from app.models.gold import (  # noqa: F401
    UserWatchStats, TitleEngagementStats, CalendarActivity, CohortMetrics,
)
from app.models.agent import (  # noqa: F401
    AgentSession, AgentMessage, AgentEvent, MessageRole, EventType,
)

from app.ui._theme import (
    BORDER, CARD, DIM, DIMMER, NETFLIX_RED, TEXT, inject_theme,
)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
@st.cache_resource
def get_engine():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return engine


def _db() -> Session:
    return Session(get_engine(), expire_on_commit=False)


# ---------------------------------------------------------------------------
# Time window
# ---------------------------------------------------------------------------
WINDOWS: dict[str, int | None] = {
    "24h": 1,
    "7d":  7,
    "30d": 30,
    "all": None,
}


def _window_cutoff(window: str) -> datetime | None:
    days = WINDOWS.get(window)
    if days is None:
        return None
    return datetime.utcnow() - timedelta(days=days)


# ---------------------------------------------------------------------------
# KPI aggregation
# ---------------------------------------------------------------------------
def kpis_for_window(window: str) -> dict:
    cutoff = _window_cutoff(window)
    cutoff_sql = "WHERE created_at >= :cutoff" if cutoff else ""
    cutoff_sql_alias = "AND created_at >= :cutoff" if cutoff else ""
    params = {"cutoff": cutoff} if cutoff else {}

    with _db() as db:
        # Sessions in window
        sessions = db.execute(
            text(
                f"SELECT COUNT(*) FROM agent_sessions "
                f"{'WHERE started_at >= :cutoff' if cutoff else ''}"
            ),
            params,
        ).scalar() or 0

        # Messages in window
        messages = db.execute(
            text(f"SELECT COUNT(*) FROM agent_messages {cutoff_sql}"),
            params,
        ).scalar() or 0
        user_msgs = db.execute(
            text(
                f"SELECT COUNT(*) FROM agent_messages "
                f"WHERE role = 'user' {cutoff_sql_alias}"
            ),
            params,
        ).scalar() or 0

        # Event counts by type
        rows = db.execute(
            text(
                f"SELECT event_type::text AS t, COUNT(*) AS c "
                f"FROM agent_events {cutoff_sql} GROUP BY event_type"
            ),
            params,
        ).mappings().all()
        ev_counts: dict[str, int] = {r["t"]: int(r["c"]) for r in rows}

        tool_called   = ev_counts.get("tool_called", 0)
        tool_failed   = ev_counts.get("tool_failed", 0)
        sql_fallback  = ev_counts.get("sql_fallback", 0)
        refusals      = ev_counts.get("refusal", 0)
        injections    = ev_counts.get("prompt_injection_detected", 0)
        rate_limited  = ev_counts.get("rate_limited", 0)
        errors        = ev_counts.get("error", 0)
        llm_calls     = ev_counts.get("llm_call", 0)

        # Judge verdicts breakdown
        verdict_rows = db.execute(
            text(
                f"""
                SELECT details->>'verdict' AS v, COUNT(*) AS c
                FROM agent_events
                WHERE event_type = 'judge_verdict'
                {cutoff_sql_alias}
                GROUP BY details->>'verdict'
                """
            ),
            params,
        ).mappings().all()
        verdicts = {r["v"]: int(r["c"]) for r in verdict_rows if r["v"]}
        v_total = sum(verdicts.values()) or 1
        pass_rate = verdicts.get("pass", 0) / v_total

        # Avg LLM latency + total tokens
        lat_tok = db.execute(
            text(
                f"""
                SELECT AVG((details->>'latency_ms')::float) AS avg_lat,
                       SUM((details->>'tokens')::int) AS total_tokens
                FROM agent_events
                WHERE event_type = 'llm_call'
                {cutoff_sql_alias}
                """
            ),
            params,
        ).mappings().one_or_none()
        avg_lat   = int(lat_tok["avg_lat"] or 0) if lat_tok else 0
        total_tok = int(lat_tok["total_tokens"] or 0) if lat_tok else 0

        # Unique users
        uniq_users = db.execute(
            text(
                f"SELECT COUNT(DISTINCT user_id) FROM agent_sessions "
                f"{'WHERE started_at >= :cutoff' if cutoff else ''}"
            ),
            params,
        ).scalar() or 0

    tool_fail_rate = (tool_failed / tool_called) if tool_called else 0.0

    return {
        "sessions": sessions,
        "unique_users": uniq_users,
        "messages": messages,
        "user_msgs": user_msgs,
        "tool_called": tool_called,
        "tool_failed": tool_failed,
        "tool_fail_rate": tool_fail_rate,
        "sql_fallback": sql_fallback,
        "refusals": refusals,
        "injections": injections,
        "rate_limited": rate_limited,
        "errors": errors,
        "llm_calls": llm_calls,
        "verdicts": verdicts,
        "judge_pass_rate": pass_rate,
        "avg_latency_ms": avg_lat,
        "total_tokens": total_tok,
    }


# ---------------------------------------------------------------------------
# Session list + detail
# ---------------------------------------------------------------------------
def list_sessions(
    window: str,
    limit: int = 50,
    archetypes: list[str] | None = None,
    countries: list[str] | None = None,
    judge_fails_only: bool = False,
) -> list[dict]:
    cutoff = _window_cutoff(window)
    sql = """
        SELECT
            s.session_id, s.started_at, s.last_active_at, s.turn_count,
            s.status::text AS status,
            u.user_id, u.archetype::text AS archetype, u.age, u.country::text AS country,
            (
                SELECT content FROM agent_messages m
                WHERE m.session_id = s.session_id AND m.role = 'user'
                ORDER BY m.turn_num ASC LIMIT 1
            ) AS first_user_msg,
            (
                SELECT COUNT(*) FROM agent_events e
                WHERE e.session_id = s.session_id AND e.event_type = 'tool_called'
            ) AS tool_calls,
            (
                SELECT COUNT(*) FROM agent_events e
                WHERE e.session_id = s.session_id AND e.event_type = 'judge_verdict'
                      AND e.details->>'verdict' = 'fail'
            ) AS judge_fails
        FROM agent_sessions s
        JOIN users u ON u.user_id = s.user_id
        WHERE 1=1
    """
    params: dict = {}
    if cutoff:
        sql += " AND s.started_at >= :cutoff"
        params["cutoff"] = cutoff
    if archetypes:
        sql += " AND u.archetype::text = ANY(:arcs)"
        params["arcs"] = archetypes
    if countries:
        sql += " AND u.country::text = ANY(:countries)"
        params["countries"] = countries
    if judge_fails_only:
        sql += (
            " AND EXISTS ("
            "  SELECT 1 FROM agent_events e"
            "  WHERE e.session_id = s.session_id"
            "    AND e.event_type = 'judge_verdict'"
            "    AND e.details->>'verdict' = 'fail'"
            " )"
        )
    sql += " ORDER BY s.last_active_at DESC LIMIT :lim"
    params["lim"] = limit

    with _db() as db:
        rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def sessions_trend(window: str) -> list[dict]:
    """Sessions started per bucket (hour for 24h window, day otherwise)."""
    cutoff = _window_cutoff(window)
    if cutoff is None:
        # All-time: fall back to a 30-day daily trend so the chart stays legible
        cutoff = datetime.utcnow() - timedelta(days=30)
    hourly = window == "24h"
    trunc = "hour" if hourly else "day"
    sql = f"""
        SELECT date_trunc('{trunc}', started_at) AS bucket,
               COUNT(*) AS n
        FROM agent_sessions
        WHERE started_at >= :cutoff
        GROUP BY bucket
        ORDER BY bucket
    """
    with _db() as db:
        rows = db.execute(text(sql), {"cutoff": cutoff}).mappings().all()
    return [{"bucket": r["bucket"], "n": int(r["n"])} for r in rows]


def filter_facets() -> dict:
    with _db() as db:
        arcs = [
            r[0] for r in db.execute(
                text(
                    """
                    SELECT DISTINCT u.archetype::text
                    FROM users u
                    JOIN agent_sessions s ON s.user_id = u.user_id
                    ORDER BY 1
                    """
                )
            ).all() if r[0]
        ]
        countries = [
            r[0] for r in db.execute(
                text(
                    """
                    SELECT DISTINCT u.country::text
                    FROM users u
                    JOIN agent_sessions s ON s.user_id = u.user_id
                    ORDER BY 1
                    """
                )
            ).all() if r[0]
        ]
    return {"archetypes": arcs, "countries": countries}


def session_detail(session_id: uuid.UUID) -> tuple[dict, list[dict], list[dict]]:
    """Return (meta, messages, events) for one session."""
    with _db() as db:
        meta = db.execute(
            text(
                """
                SELECT s.session_id, s.started_at, s.last_active_at,
                       s.turn_count, s.status::text AS status,
                       u.user_id, u.archetype::text AS archetype,
                       u.age, u.country::text AS country,
                       u.maturity_ceiling::text AS maturity_ceiling
                FROM agent_sessions s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.session_id = :sid
                """
            ),
            {"sid": session_id},
        ).mappings().one_or_none()
        msgs = db.execute(
            text(
                """
                SELECT role::text AS role, content, tool_name, tool_args,
                       tool_result, turn_num, created_at,
                       model_used, tokens_used, latency_ms
                FROM agent_messages
                WHERE session_id = :sid
                ORDER BY turn_num ASC, created_at ASC
                """
            ),
            {"sid": session_id},
        ).mappings().all()
        events = db.execute(
            text(
                """
                SELECT event_type::text AS event_type, details, created_at
                FROM agent_events
                WHERE session_id = :sid
                ORDER BY created_at ASC
                """
            ),
            {"sid": session_id},
        ).mappings().all()
    return dict(meta) if meta else {}, [dict(m) for m in msgs], [dict(e) for e in events]


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
EVENT_COLORS = {
    "llm_call":                  "#60a5fa",  # blue
    "tool_called":               "#22c55e",  # green
    "tool_failed":               "#f59e0b",  # amber
    "sql_fallback":              "#a78bfa",  # purple
    "judge_verdict":             "#eab308",  # yellow (overridden by verdict)
    "refusal":                   "#f97316",  # orange
    "prompt_injection_detected": NETFLIX_RED,
    "rate_limited":              "#f59e0b",
    "error":                     NETFLIX_RED,
}


def _kpi_tile(col, label: str, value: str, sub: str = "",
              sub_cls: str = "", accent: str = NETFLIX_RED) -> None:
    # IMPORTANT: build HTML as a single line (no newlines/indent). Streamlit's
    # markdown preprocessor otherwise treats multi-line HTML as plain text and
    # the CSS classes never attach, which is why the first version rendered
    # as unstyled stacked text.
    sub_html = (
        f'<div class="bi-kpi-sub {sub_cls}">{sub}</div>' if sub else ""
    )
    html = (
        f'<div class="bi-kpi" style="--kpi-accent:{accent};">'
        f'<div class="bi-kpi-label">{label}</div>'
        f'<div class="bi-kpi-value">{value}</div>'
        f'{sub_html}'
        f'</div>'
    )
    col.markdown(html, unsafe_allow_html=True)


def _render_kpis(k: dict) -> None:
    r1 = st.columns(4, gap="small")
    _kpi_tile(r1[0], "Sessions",       f"{k['sessions']:,}",
              sub=f"{k['unique_users']:,} unique users")
    _kpi_tile(r1[1], "User messages",  f"{k['user_msgs']:,}",
              sub=f"{k['messages']:,} total records")
    _kpi_tile(r1[2], "Tool calls",     f"{k['tool_called']:,}",
              sub=(f"{k['tool_fail_rate']*100:.1f}% failure rate"
                   if k['tool_called'] else "no calls"),
              sub_cls=("bi-kpi-sub-bad" if k['tool_fail_rate'] > 0.05
                       else "bi-kpi-sub-good"),
              accent="#22c55e")
    _kpi_tile(r1[3], "Judge pass rate",
              f"{k['judge_pass_rate']*100:.0f}%" if k['verdicts'] else "—",
              sub=(f"{k['verdicts'].get('pass', 0)} pass · "
                   f"{k['verdicts'].get('weak', 0)} weak · "
                   f"{k['verdicts'].get('fail', 0)} fail")
                   if k['verdicts'] else "no verdicts yet",
              accent="#eab308")

    r2 = st.columns(4, gap="small")
    _kpi_tile(r2[0], "LLM calls", f"{k['llm_calls']:,}",
              sub=f"avg {k['avg_latency_ms']} ms",
              accent="#60a5fa")
    _kpi_tile(r2[1], "Tokens burned", f"{k['total_tokens']:,}",
              sub="total across all calls",
              accent="#60a5fa")
    _kpi_tile(r2[2], "SQL fallbacks", f"{k['sql_fallback']:,}",
              sub="escape-hatch SELECTs",
              accent="#a78bfa")
    _kpi_tile(
        r2[3], "Guards tripped",
        f"{k['refusals'] + k['injections'] + k['rate_limited']:,}",
        sub=(f"{k['refusals']} refusal · "
             f"{k['injections']} injection · "
             f"{k['rate_limited']} rate"),
        sub_cls=("bi-kpi-sub-warn"
                 if (k['refusals'] + k['injections']) > 0 else ""),
        accent=NETFLIX_RED,
    )


def _humanize_age(when: datetime) -> str:
    """'2h ago', '3d ago', etc. — reads like a chat timestamp."""
    delta = datetime.utcnow() - when
    s = int(delta.total_seconds())
    if s < 60:    return "just now"
    if s < 3600:  return f"{s // 60}m ago"
    if s < 86400: return f"{s // 3600}h ago"
    d = s // 86400
    if d < 7:  return f"{d}d ago"
    return when.strftime("%b %d")


def _render_trend_chart(window: str) -> None:
    """Line chart of sessions started per hour (24h) or per day (else)."""
    data = sessions_trend(window)
    if not data:
        return
    hourly = window == "24h"
    try:
        import pandas as pd
    except ImportError:
        return
    df = pd.DataFrame(data)
    df = df.set_index("bucket")
    st.markdown('<div class="bi-section">Session volume</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="bi-trend-wrap">', unsafe_allow_html=True)
    st.line_chart(df, height=180, color=NETFLIX_RED)
    label = "last 24h · hourly" if hourly else "trailing window · daily"
    st.caption(label)
    st.markdown('</div>', unsafe_allow_html=True)


def _render_session_list(
    window: str,
    archetypes: list[str] | None = None,
    countries: list[str] | None = None,
    judge_fails_only: bool = False,
) -> None:
    rows = list_sessions(
        window, limit=48,
        archetypes=archetypes or None,
        countries=countries or None,
        judge_fails_only=judge_fails_only,
    )
    if not rows:
        st.markdown(
            '<div style="color:#808080; padding:40px; text-align:center;">'
            'No sessions in this window yet.</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        f'<div class="bi-section">{len(rows)} recent sessions · click any to inspect</div>',
        unsafe_allow_html=True,
    )

    # Render as a 3-column grid of cards
    cards_per_row = 3
    for i in range(0, len(rows), cards_per_row):
        cols = st.columns(cards_per_row, gap="medium")
        for j, r in enumerate(rows[i:i + cards_per_row]):
            _render_session_card(cols[j], r)


def _render_session_card(col, r: dict) -> None:
    """Single clickable session card. Entire card is a hyperlink to
    ?session=<uuid> which main() reads at the top of each run."""
    sid = r["session_id"]
    arch = (r["archetype"] or "").replace("_", " ").title() or "Unknown"
    uid8 = str(r.get("user_id") or sid)[:8]
    preview_raw = (r["first_user_msg"] or "(no messages)")[:140]
    if r["first_user_msg"] and len(r["first_user_msg"]) > 140:
        preview_raw += "…"
    preview = _escape_html(preview_raw)
    when = _humanize_age(r["last_active_at"])
    tool_calls = r.get("tool_calls", 0) or 0
    judge_fails = r.get("judge_fails", 0) or 0
    fail_chip = (
        f'<span class="bi-sess-fail-chip">{judge_fails} fail</span>'
        if judge_fails else ""
    )

    # Single-line HTML — multi-line gets parsed as plain text by Streamlit
    html = (
        f'<a href="?session={sid}" class="bi-sess-link">'
        f'<div class="bi-sess-card">'
        f'<div class="bi-sess-arch">{arch}</div>'
        f'<div class="bi-sess-user">User · {uid8}</div>'
        f'<div class="bi-sess-user-sub">age {r["age"]} · {r["country"]}</div>'
        f'<div class="bi-sess-preview">"{preview}"</div>'
        f'<div class="bi-sess-metrics">'
        f'<div class="bi-sess-metric">'
        f'<span class="bi-sess-metric-value">{r["turn_count"]}</span>'
        f'<span class="bi-sess-metric-label">turns</span>'
        f'</div>'
        f'<div class="bi-sess-metric">'
        f'<span class="bi-sess-metric-value">{tool_calls}</span>'
        f'<span class="bi-sess-metric-label">tools</span>'
        f'</div>'
        f'{fail_chip}'
        f'<span class="bi-sess-time">{when}</span>'
        f'</div>'
        f'</div>'
        f'</a>'
    )
    col.markdown(html, unsafe_allow_html=True)


def _escape_html(s: str) -> str:
    import html
    return html.escape(str(s or ""))


def _render_session_detail(session_id: uuid.UUID) -> None:
    meta, msgs, events = session_detail(session_id)
    if not meta:
        st.error("Session not found.")
        if st.button("← Back to sessions"):
            st.session_state.focused_session_id = None
            st.rerun()
        return

    if st.button("← Back to sessions", key="back_to_list"):
        st.session_state.focused_session_id = None
        st.rerun()

    arch = (meta["archetype"] or "").replace("_", " ").title()
    st.markdown(
        f'<div class="bi-detail-head">'
        f'  <div style="font-weight:700; font-size:18px; color:{TEXT};">'
        f'    Session {str(meta["session_id"])[:8]}'
        f'  </div>'
        f'  <div style="color:{DIM}; font-size:13px; margin-top:4px;">'
        f'    {arch} · age {meta["age"]} · {meta["country"]} · '
        f'    ceiling {meta["maturity_ceiling"]} · '
        f'    {meta["turn_count"]} turns · '
        f'    started {meta["started_at"].strftime("%Y-%m-%d %H:%M")}'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    tcol, ecol = st.columns([3, 2], gap="large")

    # ---- Transcript ----
    with tcol:
        st.markdown(
            '<div class="bi-section">Transcript</div>',
            unsafe_allow_html=True,
        )
        if not msgs:
            st.caption("No messages recorded.")
        for m in msgs:
            role = m["role"]
            if role == "user":
                st.markdown(
                    f'<div class="bi-row bi-row-user">'
                    f'  <div class="bi-bubble bi-bubble-user">'
                    f'    {_escape(m["content"])[:1000]}'
                    f'  </div>'
                    f'  <div class="bi-avatar bi-avatar-user">U</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            elif role == "assistant":
                content = (m["content"] or "").strip() or "(empty)"
                sub = (
                    f'{m["model_used"] or "?"} · '
                    f'{m["tokens_used"] or 0}tok · '
                    f'{m["latency_ms"] or 0}ms'
                )
                st.markdown(
                    f'<div class="bi-row bi-row-ai">'
                    f'  <div class="bi-avatar bi-avatar-ai">B</div>'
                    f'  <div class="bi-bubble bi-bubble-ai">'
                    f'    {_escape(content)[:1500]}'
                    f'    <div style="font-size:10px; color:{DIMMER}; '
                    f'                margin-top:6px; letter-spacing:0.05em;">'
                    f'      {sub}'
                    f'    </div>'
                    f'  </div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            elif role == "tool_result":
                tool_name = m["tool_name"] or "?"
                with st.expander(f"tool_result: {tool_name}", expanded=False):
                    st.caption("args")
                    st.json(m["tool_args"] or {}, expanded=False)
                    st.caption("result (truncated)")
                    st.code((m["content"] or "")[:2000], language="json")

    # ---- Event timeline ----
    with ecol:
        st.markdown(
            '<div class="bi-section">Event timeline</div>',
            unsafe_allow_html=True,
        )
        if not events:
            st.caption("No events recorded.")
        for e in events:
            etype = e["event_type"]
            d = e["details"] or {}
            accent = EVENT_COLORS.get(etype, DIMMER)
            # Override judge verdict color
            if etype == "judge_verdict":
                v = d.get("verdict", "")
                accent = {"pass": "#22c55e", "weak": "#eab308",
                          "fail": NETFLIX_RED}.get(v, DIMMER)
            when = e["created_at"].strftime("%H:%M:%S")
            summary = _event_summary(etype, d)
            code_block = _event_code(etype, d)
            code_html = (
                f'<div class="bi-event-code">{_escape(code_block)}</div>'
                if code_block else ""
            )
            st.markdown(
                f'<div class="bi-event" style="--event-accent:{accent};">'
                f'  <div class="bi-event-head">'
                f'    <span class="bi-event-type">{etype.replace("_", " ")}</span>'
                f'    <span class="bi-event-time">{when}</span>'
                f'  </div>'
                f'  <div class="bi-event-body">{summary}</div>'
                f'  {code_html}'
                f'</div>',
                unsafe_allow_html=True,
            )


def _event_summary(etype: str, d: dict) -> str:
    if etype == "llm_call":
        return (f"{d.get('model', '?')} · step {d.get('step', '?')} · "
                f"{d.get('tokens', 0)}tok · {d.get('latency_ms', 0)}ms · "
                f"{d.get('tool_calls', 0)} tool call(s)")
    if etype == "tool_called":
        return f"tool: <b>{d.get('tool', '?')}</b>"
    if etype == "tool_failed":
        return (f"tool: <b>{d.get('tool', '?')}</b> — "
                f"error: {_escape(str(d.get('error', ''))[:200])}")
    if etype == "sql_fallback":
        v = d.get("validation", {}) or {}
        flags = []
        if v.get("user_filter_injected"): flags.append("auth-injected")
        if v.get("limit_injected"): flags.append("limit-capped")
        flag_str = " · ".join(flags) if flags else "clean"
        return (f"intent: {_escape(d.get('intent', ''))[:120]} · "
                f"rows: {d.get('row_count', 0)} · {flag_str}")
    if etype == "judge_verdict":
        return (f"<b>{d.get('verdict', '?').upper()}</b> on "
                f"<code>{d.get('tool', '?')}</code> — "
                f"{_escape(d.get('reasoning', ''))}")
    if etype == "refusal":
        return (f"tool: <b>{d.get('tool', '?')}</b> — "
                f"{_escape(d.get('reason', ''))}")
    if etype == "prompt_injection_detected":
        return f"pattern: <code>{_escape(d.get('pattern', ''))}</code>"
    if etype == "rate_limited":
        return "user hit the hourly rate limit"
    if etype == "error":
        return (f"stage: {d.get('stage', '?')} — "
                f"{_escape(str(d.get('error', ''))[:200])}")
    return json.dumps(d, default=str)[:200]


def _event_code(etype: str, d: dict) -> str:
    """Optional code snippet shown under the event summary."""
    if etype == "sql_fallback":
        v = d.get("validation", {}) or {}
        sql = v.get("rewritten_sql") or d.get("sql") or ""
        return sql[:800]
    if etype == "tool_called":
        args = d.get("args") or {}
        if args:
            return json.dumps(args, default=str, indent=2)[:400]
    return ""


def _escape(s: str) -> str:
    import html
    return html.escape(str(s or ""))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _init_state() -> None:
    if "focused_session_id" not in st.session_state:
        st.session_state.focused_session_id = None
    if "audit_window" not in st.session_state:
        st.session_state.audit_window = "24h"


def main() -> None:
    st.set_page_config(
        page_title="Binge Intelligence · Audit",
        page_icon=None,
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_theme()
    _init_state()

    # Clickable-card drill-in: read ?session=<uuid> set by a card hyperlink
    qp_sid = st.query_params.get("session")
    if qp_sid:
        try:
            st.session_state.focused_session_id = uuid.UUID(qp_sid)
        except (ValueError, TypeError):
            pass
        st.query_params.clear()
        st.rerun()

    # Header
    hcol1, hcol2 = st.columns([5, 2])
    with hcol1:
        st.markdown(
            '<div class="bi-brandbar">'
            '<span class="bi-logo">B</span>'
            '<span class="bi-logo-text">Binge Intelligence · '
            '<span style="color:#B3B3B3;">Audit</span></span>'
            '</div>',
            unsafe_allow_html=True,
        )
    with hcol2:
        if st.session_state.focused_session_id:
            st.caption("inspecting one session")
        else:
            if st.button("Refresh", use_container_width=True, key="refresh"):
                st.rerun()

    st.markdown(f'<hr style="border-color:{BORDER}; margin:12px 0 18px 0;"/>',
                unsafe_allow_html=True)

    # Session detail mode?
    if st.session_state.focused_session_id:
        _render_session_detail(st.session_state.focused_session_id)
        return

    # Dashboard mode
    # Time window pills (as buttons)
    st.markdown('<div class="bi-section">Time window</div>',
                unsafe_allow_html=True)
    win_cols = st.columns(len(WINDOWS))
    for i, w in enumerate(WINDOWS.keys()):
        label = {"24h": "Last 24h", "7d": "Last 7 days",
                 "30d": "Last 30 days", "all": "All time"}[w]
        with win_cols[i]:
            is_active = st.session_state.audit_window == w
            if st.button(
                ("● " if is_active else "") + label,
                use_container_width=True,
                key=f"win_{w}",
            ):
                st.session_state.audit_window = w
                st.rerun()

    st.markdown('<div style="height:12px;"></div>', unsafe_allow_html=True)

    k = kpis_for_window(st.session_state.audit_window)

    _render_kpis(k)
    st.markdown('<div style="height:20px;"></div>', unsafe_allow_html=True)

    # Trend chart
    _render_trend_chart(st.session_state.audit_window)
    st.markdown('<div style="height:12px;"></div>', unsafe_allow_html=True)

    # Session filters
    facets = filter_facets()
    st.markdown('<div class="bi-section">Filter sessions</div>',
                unsafe_allow_html=True)
    fcol1, fcol2, fcol3 = st.columns([2, 2, 1.4])
    with fcol1:
        f_arcs = st.multiselect(
            "Archetype", facets["archetypes"], default=[],
            format_func=lambda s: s.replace("_", " ").title(),
            key="audit_f_arcs",
        )
    with fcol2:
        f_countries = st.multiselect(
            "Country", facets["countries"], default=[],
            key="audit_f_countries",
        )
    with fcol3:
        f_fails = st.toggle(
            "Judge-fails only", value=False, key="audit_f_fails",
            help="Show only sessions where at least one tool/SQL output "
                 "was rated 'fail' by the LLM judge.",
        )

    st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)
    _render_session_list(
        st.session_state.audit_window,
        archetypes=f_arcs,
        countries=f_countries,
        judge_fails_only=f_fails,
    )


if __name__ == "__main__":
    main()
else:
    main()
