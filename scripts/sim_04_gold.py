"""
scripts/sim_04_gold.py

Stage 4 — Gold aggregations via DuckDB.

Reads raw Postgres tables through DuckDB's postgres extension, runs columnar
aggregation queries, materializes four gold tables, writes them back to
Postgres (for agent-tool indexed lookups), AND exports each as Parquet
(for the SQL-fallback tier to query via DuckDB directly).

Four tables produced:
  - user_watch_stats          (3000 rows)
  - title_engagement_stats    (~2896 rows)
  - calendar_activity         (~4018 rows: 2015-01-01..2025-12-31)
  - cohort_metrics            (11 rows: cohort years)

Deterministic. Rerunnable (truncates targets first).

Run:
    python -m scripts.sim_04_gold
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.title import Base
from app.models.gold import (
    CalendarActivity,
    CohortMetrics,
    TitleEngagementStats,
    UserWatchStats,
)
from app.sim.duckdb_helpers import PARQUET_DIR, duckdb_connected


SIM_END = date(2025, 12, 31)
LAST_30D_START = date(2025, 12, 1)
LAST_7D_START = date(2025, 12, 25)
LAST_90D_START = date(2025, 10, 2)


# ---------------------------------------------------------------------------
# Aggregation builders — each returns a pandas DataFrame
# ---------------------------------------------------------------------------
def build_user_watch_stats(con) -> pd.DataFrame:
    print("  [uws] base watch-event aggregates...")
    base = con.execute(f"""
        SELECT u.user_id,
               COALESCE(SUM(w.minutes_watched), 0)::BIGINT AS total_minutes_alltime,
               COALESCE(SUM(CASE WHEN w.started_at >= DATE '{LAST_30D_START}'
                                 THEN w.minutes_watched ELSE 0 END), 0)::BIGINT AS total_minutes_30d,
               COUNT(DISTINCT CAST(w.started_at AS DATE))::INT AS active_days_alltime,
               MAX(CAST(w.started_at AS DATE)) AS last_active_date,
               COUNT(DISTINCT w.title_id)::INT AS total_titles_watched,
               AVG(CASE WHEN w.content_type = 'movie' THEN
                   CASE WHEN w.was_completed THEN 1.0 ELSE 0.0 END END) AS completion_rate_short,
               AVG(CASE WHEN w.content_type = 'episode' THEN
                   CASE WHEN w.was_completed THEN 1.0 ELSE 0.0 END END) AS completion_rate_long
        FROM pg.users u
        LEFT JOIN pg.watch_events w ON u.user_id = w.user_id
        GROUP BY u.user_id
    """).fetchdf()

    print("  [uws] sessions aggregates...")
    sess = con.execute("""
        SELECT user_id, COUNT(*)::INT AS total_sessions,
               MODE() WITHIN GROUP (ORDER BY hour_started)::INT AS typical_watch_hour
        FROM pg.sessions
        GROUP BY user_id
    """).fetchdf()

    print("  [uws] top-3 genres per user...")
    top_g = con.execute("""
        WITH expanded AS (
            SELECT w.user_id, UNNEST(t.genres) AS tag, w.minutes_watched
            FROM pg.watch_events w
            JOIN pg.titles t ON w.title_id = t.title_id
        ),
        ug AS (
            SELECT user_id, tag, SUM(minutes_watched) AS mins
            FROM expanded GROUP BY user_id, tag
        ),
        ranked AS (
            SELECT user_id, tag, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY mins DESC) AS rn
            FROM ug
        )
        SELECT user_id, LIST(tag ORDER BY rn) AS top_3_genres
        FROM ranked WHERE rn <= 3
        GROUP BY user_id
    """).fetchdf()

    print("  [uws] top-3 themes per user...")
    top_t = con.execute("""
        WITH expanded AS (
            SELECT w.user_id, UNNEST(t.themes) AS tag, w.minutes_watched
            FROM pg.watch_events w
            JOIN pg.titles t ON w.title_id = t.title_id
        ),
        ut AS (
            SELECT user_id, tag, SUM(minutes_watched) AS mins
            FROM expanded GROUP BY user_id, tag
        ),
        ranked AS (
            SELECT user_id, tag, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY mins DESC) AS rn
            FROM ut
        )
        SELECT user_id, LIST(tag ORDER BY rn) AS top_3_themes
        FROM ranked WHERE rn <= 3 GROUP BY user_id
    """).fetchdf()

    print("  [uws] top-3 tones per user...")
    top_tn = con.execute("""
        WITH expanded AS (
            SELECT w.user_id, UNNEST(t.tones) AS tag, w.minutes_watched
            FROM pg.watch_events w
            JOIN pg.titles t ON w.title_id = t.title_id
        ),
        utn AS (
            SELECT user_id, tag, SUM(minutes_watched) AS mins
            FROM expanded GROUP BY user_id, tag
        ),
        ranked AS (
            SELECT user_id, tag, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY mins DESC) AS rn
            FROM utn
        )
        SELECT user_id, LIST(tag ORDER BY rn) AS top_3_tones
        FROM ranked WHERE rn <= 3 GROUP BY user_id
    """).fetchdf()

    print("  [uws] series-progress aggregates...")
    prog = con.execute("""
        SELECT user_id,
               SUM(CASE WHEN state = 'in_progress' THEN 1 ELSE 0 END)::INT AS currently_in_progress_count,
               AVG(CASE WHEN state = 'abandoned' THEN abandonment_episode END) AS avg_abandonment_episode
        FROM pg.user_series_progress
        GROUP BY user_id
    """).fetchdf()

    print("  [uws] active-days-per-week over last 90d...")
    adpw = con.execute(f"""
        SELECT user_id,
               (COUNT(DISTINCT CAST(started_at AS DATE)) * 7.0 / 90.0)::FLOAT AS active_days_per_week
        FROM pg.watch_events
        WHERE started_at >= DATE '{LAST_90D_START}'
        GROUP BY user_id
    """).fetchdf()

    # ----- Merge in pandas -----
    df = base.merge(sess, on="user_id", how="left")
    df = df.merge(top_g, on="user_id", how="left")
    df = df.merge(top_t, on="user_id", how="left")
    df = df.merge(top_tn, on="user_id", how="left")
    df = df.merge(prog, on="user_id", how="left")
    df = df.merge(adpw, on="user_id", how="left")

    # Fill NaN sensibly
    df["total_sessions"] = df["total_sessions"].fillna(0).astype(int)
    df["typical_watch_hour"] = df["typical_watch_hour"].fillna(20).astype(int)
    df["currently_in_progress_count"] = df["currently_in_progress_count"].fillna(0).astype(int)
    df["active_days_per_week"] = df["active_days_per_week"].fillna(0.0)
    df["completion_rate_short"] = df["completion_rate_short"].fillna(0.0)
    df["completion_rate_long"] = df["completion_rate_long"].fillna(0.0)
    for col in ("top_3_genres", "top_3_themes", "top_3_tones"):
        df[col] = df[col].apply(
            lambda v: list(v) if v is not None and hasattr(v, "__iter__") and not isinstance(v, str)
            else []
        )

    # Derived: avg_daily_active_minutes
    df["avg_daily_active_minutes"] = (
        df["total_minutes_alltime"] / df["active_days_alltime"].replace(0, 1)
    ).round(1)

    # Churn risk: how long since last active, scaled 0..1
    sim_end_ts = pd.Timestamp(SIM_END)
    df["last_active_date"] = pd.to_datetime(df["last_active_date"])
    df["days_since_active"] = (sim_end_ts - df["last_active_date"]).dt.days.fillna(9999)
    df["churn_risk_score"] = (df["days_since_active"] / 180.0).clip(0, 1).round(3)
    df = df.drop(columns=["days_since_active"])
    df["last_active_date"] = df["last_active_date"].dt.date

    return df


def build_title_engagement_stats(con) -> pd.DataFrame:
    print("  [tes] base watch-event aggregates per title...")
    base = con.execute(f"""
        SELECT t.title_id,
               COUNT(DISTINCT w.user_id)::INT AS total_watchers,
               COUNT(w.event_id)::BIGINT AS total_events,
               COALESCE(SUM(w.minutes_watched), 0)::BIGINT AS total_minutes,
               AVG(CASE WHEN w.was_completed THEN 1.0 ELSE 0.0 END) AS completion_rate,
               SUM(CASE WHEN w.started_at >= DATE '{LAST_7D_START}' THEN 1 ELSE 0 END)::INT AS hot_score_7d,
               SUM(CASE WHEN w.started_at >= DATE '{LAST_30D_START}' THEN 1 ELSE 0 END)::INT AS hot_score_30d
        FROM pg.titles t
        LEFT JOIN pg.watch_events w ON t.title_id = w.title_id
        GROUP BY t.title_id
    """).fetchdf()

    print("  [tes] avg abandonment episode (series only)...")
    aband = con.execute("""
        SELECT title_id,
               AVG(abandonment_episode) AS avg_abandonment_episode
        FROM pg.user_series_progress
        WHERE state = 'abandoned' AND abandonment_episode IS NOT NULL
        GROUP BY title_id
    """).fetchdf()

    print("  [tes] top-3 archetypes per title (watcher counts)...")
    arch = con.execute("""
        WITH per AS (
            SELECT w.title_id, u.archetype, COUNT(DISTINCT w.user_id)::INT AS watchers
            FROM pg.watch_events w
            JOIN pg.users u ON w.user_id = u.user_id
            GROUP BY w.title_id, u.archetype
        ),
        ranked AS (
            SELECT title_id, archetype, watchers,
                   ROW_NUMBER() OVER (PARTITION BY title_id ORDER BY watchers DESC) AS rn
            FROM per
        )
        SELECT title_id,
               MAP(LIST(archetype ORDER BY rn), LIST(watchers ORDER BY rn)) AS top_archetypes
        FROM ranked WHERE rn <= 3
        GROUP BY title_id
    """).fetchdf()

    df = base.merge(aband, on="title_id", how="left")
    df = df.merge(arch, on="title_id", how="left")

    df["completion_rate"] = df["completion_rate"].fillna(0.0)
    # top_archetypes may come back as dict already from DuckDB MAP → normalize
    df["top_archetypes"] = df["top_archetypes"].apply(
        lambda v: dict(v) if v is not None and hasattr(v, "items") else (v if isinstance(v, dict) else {})
    )

    return df


def build_calendar_activity(con) -> pd.DataFrame:
    print("  [cal] daily activity aggregates...")
    base = con.execute("""
        SELECT CAST(started_at AS DATE) AS activity_date,
               COUNT(DISTINCT user_id)::INT AS active_users,
               COUNT(*)::INT AS total_sessions,
               COALESCE(SUM(total_minutes), 0)::BIGINT AS total_minutes_watched
        FROM pg.sessions
        GROUP BY CAST(started_at AS DATE)
    """).fetchdf()

    print("  [cal] top-3 titles per date...")
    top = con.execute("""
        WITH per AS (
            SELECT CAST(w.started_at AS DATE) AS d, t.title,
                   SUM(w.minutes_watched) AS mins
            FROM pg.watch_events w
            JOIN pg.titles t ON w.title_id = t.title_id
            GROUP BY CAST(w.started_at AS DATE), t.title
        ),
        ranked AS (
            SELECT d, title, ROW_NUMBER() OVER (PARTITION BY d ORDER BY mins DESC) AS rn
            FROM per
        )
        SELECT d AS activity_date, LIST(title ORDER BY rn) AS top_3_titles
        FROM ranked WHERE rn <= 3 GROUP BY d
    """).fetchdf()

    df = base.merge(top, on="activity_date", how="left")
    df["top_3_titles"] = df["top_3_titles"].apply(
        lambda v: list(v) if v is not None and hasattr(v, "__iter__") and not isinstance(v, str)
        else []
    )
    return df


def build_cohort_metrics(con) -> pd.DataFrame:
    print("  [coh] cohort retention + metrics...")
    df = con.execute(f"""
        WITH cohort_users AS (
            SELECT cohort_year, user_id, signup_date FROM pg.users
        ),
        activity AS (
            SELECT c.cohort_year, c.user_id, c.signup_date,
                   COUNT(DISTINCT CAST(w.started_at AS DATE)) AS active_days,
                   COUNT(w.event_id) AS events,
                   COALESCE(SUM(w.minutes_watched), 0) AS total_minutes
            FROM cohort_users c
            LEFT JOIN pg.watch_events w ON c.user_id = w.user_id
            GROUP BY c.cohort_year, c.user_id, c.signup_date
        ),
        sessions_by_user AS (
            SELECT user_id, COUNT(*) AS n FROM pg.sessions GROUP BY user_id
        ),
        ret AS (
            SELECT c.cohort_year, c.user_id, c.signup_date,
                   MAX(CAST(w.started_at AS DATE)) AS last_active
            FROM cohort_users c
            LEFT JOIN pg.watch_events w ON c.user_id = w.user_id
            GROUP BY c.cohort_year, c.user_id, c.signup_date
        )
        SELECT a.cohort_year,
               COUNT(DISTINCT a.user_id)::INT AS users_in_cohort,
               AVG(COALESCE(DATE_DIFF('day', a.signup_date, r.last_active), 0))::FLOAT AS avg_tenure_days,
               AVG(CASE WHEN r.last_active IS NOT NULL
                        AND DATE_DIFF('day', a.signup_date, r.last_active) >= 365
                        THEN 1.0 ELSE 0.0 END) AS retention_12m,
               AVG(CASE WHEN r.last_active IS NOT NULL
                        AND DATE_DIFF('day', a.signup_date, r.last_active) >= 730
                        THEN 1.0 ELSE 0.0 END) AS retention_24m,
               AVG(COALESCE(s.n, 0))::FLOAT AS avg_sessions_per_user,
               AVG(a.total_minutes)::FLOAT AS avg_watch_minutes_per_user
        FROM activity a
        JOIN ret r ON a.user_id = r.user_id
        LEFT JOIN sessions_by_user s ON a.user_id = s.user_id
        GROUP BY a.cohort_year
        ORDER BY a.cohort_year
    """).fetchdf()

    print("  [coh] top titles per cohort...")
    top = con.execute("""
        WITH per AS (
            SELECT u.cohort_year, t.title,
                   COUNT(DISTINCT w.user_id) AS watchers
            FROM pg.watch_events w
            JOIN pg.users u ON w.user_id = u.user_id
            JOIN pg.titles t ON w.title_id = t.title_id
            GROUP BY u.cohort_year, t.title
        ),
        ranked AS (
            SELECT cohort_year, title, ROW_NUMBER() OVER
                   (PARTITION BY cohort_year ORDER BY watchers DESC) AS rn
            FROM per
        )
        SELECT cohort_year, LIST(title ORDER BY rn) AS top_titles
        FROM ranked WHERE rn <= 5
        GROUP BY cohort_year
    """).fetchdf()

    df = df.merge(top, on="cohort_year", how="left")
    df["top_titles"] = df["top_titles"].apply(
        lambda v: list(v) if v is not None and hasattr(v, "__iter__") and not isinstance(v, str)
        else []
    )
    for c in ("retention_12m", "retention_24m", "avg_tenure_days",
              "avg_sessions_per_user", "avg_watch_minutes_per_user"):
        df[c] = df[c].fillna(0.0).round(3)
    return df


# ---------------------------------------------------------------------------
# Postgres write-back
# ---------------------------------------------------------------------------
def write_to_postgres(engine, df: pd.DataFrame, table: str, pk_col: str) -> None:
    """Truncate target + bulk-insert via SQLAlchemy."""
    df = df.copy()
    df["updated_at"] = datetime.utcnow()  # satisfy NOT NULL
    with engine.begin() as con:
        con.execute(text(f"TRUNCATE TABLE {table}"))
    df.to_sql(table, engine, if_exists="append", index=False, chunksize=500)
    print(f"  → {table}: {len(df)} rows written")


def write_to_parquet(df: pd.DataFrame, name: str) -> None:
    import uuid as _uuid
    out = PARQUET_DIR / f"{name}.parquet"
    df_out = df.copy()
    # PyArrow can't serialize Python UUID objects; stringify them
    for col in df_out.columns:
        if df_out[col].dtype == object:
            sample = df_out[col].dropna().iloc[0] if not df_out[col].dropna().empty else None
            if isinstance(sample, _uuid.UUID):
                df_out[col] = df_out[col].astype(str)
    df_out.to_parquet(out, index=False)
    print(f"  → {out.name}: {len(df)} rows ({out.stat().st_size // 1024} KB)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    t0 = time.time()
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)

    with duckdb_connected() as con:
        print("[Stage 4] Gold aggregations via DuckDB")
        print(f"  DuckDB path: {con.execute('PRAGMA database_list;').fetchall()}")

        print("\n==> user_watch_stats")
        uws = build_user_watch_stats(con)
        write_to_postgres(engine, uws, "user_watch_stats", "user_id")
        write_to_parquet(uws, "user_watch_stats")

        print("\n==> title_engagement_stats")
        tes = build_title_engagement_stats(con)
        # top_archetypes is a dict column — need to normalize for SQLAlchemy JSONB
        tes_out = tes.copy()
        # psycopg2 can't adapt Python dict -> JSONB; serialize to JSON string,
        # Postgres implicitly casts text to jsonb on insert.
        tes_out["top_archetypes"] = tes_out["top_archetypes"].apply(
            lambda d: json.dumps({k: int(v) for k, v in d.items()}) if isinstance(d, dict) else "{}"
        )
        write_to_postgres(engine, tes_out, "title_engagement_stats", "title_id")

        # For parquet, keep dicts as proper nested values (readable)
        tes_pq = tes.copy()
        tes_pq["top_archetypes"] = tes_pq["top_archetypes"].apply(
            lambda d: {k: int(v) for k, v in d.items()} if isinstance(d, dict) else {}
        )
        write_to_parquet(tes_pq, "title_engagement_stats")

        print("\n==> calendar_activity")
        cal = build_calendar_activity(con)
        write_to_postgres(engine, cal, "calendar_activity", "activity_date")
        write_to_parquet(cal, "calendar_activity")

        print("\n==> cohort_metrics")
        coh = build_cohort_metrics(con)
        write_to_postgres(engine, coh, "cohort_metrics", "cohort_year")
        write_to_parquet(coh, "cohort_metrics")

    elapsed = time.time() - t0
    print(f"\nDONE in {elapsed:.1f}s.")
    print(f"  Parquet files in: {PARQUET_DIR}")


if __name__ == "__main__":
    main()