"""
Gold aggregation tables — pre-computed for fast agent-tool lookups.

Produced by Stage 4 (sim_04_gold.py), which uses DuckDB to run columnar
aggregations against raw Postgres tables, then writes results back here
AND exports as Parquet for the SQL-fallback tier.

Four tables:
  - user_watch_stats           (3000 rows: one per user)
  - title_engagement_stats     (~2896 rows: one per title)
  - calendar_activity          (~4018 rows: one per date 2015-01-01..2025-12-31)
  - cohort_metrics             (~11 rows: one per signup year)
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.title import Base


class UserWatchStats(Base):
    __tablename__ = "user_watch_stats"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), primary_key=True
    )
    # Volume
    total_minutes_alltime: Mapped[int] = mapped_column(Integer, default=0)
    total_minutes_30d: Mapped[int] = mapped_column(Integer, default=0)
    active_days_alltime: Mapped[int] = mapped_column(Integer, default=0)
    avg_daily_active_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    active_days_per_week: Mapped[float] = mapped_column(Float, default=0.0)
    total_sessions: Mapped[int] = mapped_column(Integer, default=0)
    total_titles_watched: Mapped[int] = mapped_column(Integer, default=0)

    # Taste fingerprint
    top_3_genres: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    top_3_themes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    top_3_tones: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # Completion / abandonment
    completion_rate_short: Mapped[float] = mapped_column(Float, default=0.0)
    completion_rate_long: Mapped[float] = mapped_column(Float, default=0.0)
    avg_abandonment_episode: Mapped[float | None] = mapped_column(Float, nullable=True)
    currently_in_progress_count: Mapped[int] = mapped_column(Integer, default=0)

    # Lifecycle
    churn_risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    typical_watch_hour: Mapped[int] = mapped_column(Integer, default=20)
    last_active_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_uws_churn_risk", "churn_risk_score"),
        Index("ix_uws_last_active", "last_active_date"),
    )


class TitleEngagementStats(Base):
    __tablename__ = "title_engagement_stats"

    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("titles.title_id"), primary_key=True
    )
    total_watchers: Mapped[int] = mapped_column(Integer, default=0)
    total_events: Mapped[int] = mapped_column(Integer, default=0)
    total_minutes: Mapped[int] = mapped_column(Integer, default=0)
    completion_rate: Mapped[float] = mapped_column(Float, default=0.0)
    avg_abandonment_episode: Mapped[float | None] = mapped_column(Float, nullable=True)
    hot_score_7d: Mapped[int] = mapped_column(Integer, default=0)
    hot_score_30d: Mapped[int] = mapped_column(Integer, default=0)
    top_archetypes: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_tes_hot_7d", "hot_score_7d"),
        Index("ix_tes_hot_30d", "hot_score_30d"),
        Index("ix_tes_watchers", "total_watchers"),
    )


class CalendarActivity(Base):
    __tablename__ = "calendar_activity"

    activity_date: Mapped[date] = mapped_column(Date, primary_key=True)
    active_users: Mapped[int] = mapped_column(Integer, default=0)
    total_sessions: Mapped[int] = mapped_column(Integer, default=0)
    total_minutes_watched: Mapped[int] = mapped_column(Integer, default=0)
    top_3_titles: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CohortMetrics(Base):
    __tablename__ = "cohort_metrics"

    cohort_year: Mapped[int] = mapped_column(Integer, primary_key=True)
    users_in_cohort: Mapped[int] = mapped_column(Integer, default=0)
    avg_tenure_days: Mapped[float] = mapped_column(Float, default=0.0)
    retention_12m: Mapped[float] = mapped_column(Float, default=0.0)
    retention_24m: Mapped[float] = mapped_column(Float, default=0.0)
    avg_sessions_per_user: Mapped[float] = mapped_column(Float, default=0.0)
    avg_watch_minutes_per_user: Mapped[float] = mapped_column(Float, default=0.0)
    top_titles: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


if __name__ == "__main__":
    import os
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://binge:binge_dev_pw@localhost:5433/binge_intelligence",
    )
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    print(f"gold tables created at {db_url}")