"""
Behavioral tables — the output of Stage 3.

- Session: one row per user session (start, end, type, event count)
- WatchEvent: one row per episode/movie watched (per-episode granularity)
- UserSeriesProgress: one row per (user, series) they touched, with state

All three point back to users; watch_events point to titles and (optionally,
for episodes) carry season+episode numbers.
"""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.title import Base


class SessionType(str, enum.Enum):
    PLANNED_BINGE = "planned_binge"
    TRICKLE = "trickle"
    BACKGROUND = "background"
    DISCOVERY_BROWSE = "discovery_browse"
    REWATCH = "rewatch"


class ContentTypeWatched(str, enum.Enum):
    MOVIE = "movie"
    EPISODE = "episode"


class DiscoverySource(str, enum.Enum):
    RECOMMENDATION = "recommendation"
    SEARCH = "search"
    CONTINUE_WATCHING = "continue_watching"
    NEW_RELEASE = "new_release"
    SOCIAL = "social"
    OTHER = "other"


class SeriesProgressState(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    PAUSED = "paused"


class Session(Base):
    __tablename__ = "sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Mon
    hour_started: Mapped[int] = mapped_column(Integer, nullable=False)
    session_type: Mapped[SessionType] = mapped_column(
        Enum(SessionType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_sessions_user_date", "user_id", "started_at"),
        Index("ix_sessions_started_at", "started_at"),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_dow"),
        CheckConstraint("hour_started BETWEEN 0 AND 23", name="ck_hour"),
    )


class WatchEvent(Base):
    __tablename__ = "watch_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.session_id"),
        nullable=False, index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"),
        nullable=False, index=True,
    )
    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("titles.title_id"), nullable=False
    )
    content_type: Mapped[ContentTypeWatched] = mapped_column(
        Enum(ContentTypeWatched, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    season_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    minutes_watched: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_pct: Mapped[float] = mapped_column(Float, nullable=False)
    was_completed: Mapped[bool] = mapped_column(nullable=False)
    was_abandoned: Mapped[bool] = mapped_column(nullable=False)
    discovery_source: Mapped[DiscoverySource] = mapped_column(
        Enum(DiscoverySource, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_watch_events_user_started", "user_id", "started_at"),
        Index("ix_watch_events_title", "title_id"),
        CheckConstraint(
            "completion_pct >= 0 AND completion_pct <= 1",
            name="ck_completion_pct_range",
        ),
    )


class UserSeriesProgress(Base):
    __tablename__ = "user_series_progress"

    progress_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"),
        nullable=False, index=True,
    )
    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("titles.title_id"), nullable=False
    )
    state: Mapped[SeriesProgressState] = mapped_column(
        Enum(SeriesProgressState, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    current_season: Mapped[int] = mapped_column(Integer, nullable=False)
    current_episode: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[date] = mapped_column(Date, nullable=False)
    last_watched_at: Mapped[date] = mapped_column(Date, nullable=False)
    ended_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    abandonment_episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_episodes_watched: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "title_id", name="uq_user_title_progress"),
        Index("ix_progress_user_state", "user_id", "state"),
    )


if __name__ == "__main__":
    import os
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://binge:binge_dev_pw@localhost:5433/binge_intelligence",
    )
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    print(f"behavior tables created (or already existed) at {db_url}")