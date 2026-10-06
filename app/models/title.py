"""
Title model — the catalog table. Single source of truth for the DB schema.

Layers (see design notes):
  1. Factual      — from TMDB, ground truth, never hallucinate over this
  2. Tag vectors  — LLM-enriched, MUST pass validate_tags() before insert
  3. Simulation   — assigned by us, drives the behavior engine
  4. Media        — poster/backdrop URLs stored in MinIO, served to the UI

Run this file directly to create the table in the DB (via Base.metadata.create_all),
or wire it into an Alembic migration later once the schema stabilizes.
"""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ContentType(str, enum.Enum):
    MOVIE = "movie"
    SERIES = "series"


class SeriesStatus(str, enum.Enum):
    ONGOING = "ongoing"
    ENDED = "ended"
    CANCELLED = "cancelled"


class MaturityRating(str, enum.Enum):
    R7 = "7+"
    R12 = "12+"
    R16 = "16+"
    R18 = "18+"


class Title(Base):
    __tablename__ = "titles"

    # --- Identity ---
    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)

    # --- Layer 1: Factual (TMDB ground truth) ---
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType, values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    release_year: Mapped[int] = mapped_column(Integer, nullable=False)

    # movies only
    runtime_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # series only
    episode_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    season_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_episode_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    series_status: Mapped[SeriesStatus | None] = mapped_column(
        Enum(SeriesStatus, values_callable=lambda x: [e.value for e in x]), nullable=True
    )

    country: Mapped[str | None] = mapped_column(String(8), nullable=True)  # ISO country code
    original_language: Mapped[str | None] = mapped_column(String(8), nullable=True)  # ISO lang code

    tmdb_popularity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tmdb_vote_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    synopsis: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # --- Layer 2: Tag vectors (LLM-enriched, closed vocab only — validate before insert) ---
    genres: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    themes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    tones: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    # --- Layer 3: Simulation fields (assigned by us) ---
    maturity_rating: Mapped[MaturityRating] = mapped_column(
        Enum(MaturityRating, values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    fame: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # 0-1
    binge_factor: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-1, series only
    platform_add_date: Mapped[date] = mapped_column(Date, nullable=False)
    platform_leaving_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_platform_original: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- Layer 4: Media (served from MinIO, pulled from TMDB) ---
    # Full public URLs; NULL means fetch not yet run or no image available.
    poster_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    backdrop_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Bookkeeping ---
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("tmdb_id", name="uq_titles_tmdb_id"),
        CheckConstraint("fame >= 0 AND fame <= 1", name="ck_fame_range"),
        CheckConstraint(
            "binge_factor IS NULL OR (binge_factor >= 0 AND binge_factor <= 1)",
            name="ck_binge_factor_range",
        ),
        CheckConstraint(
            "(content_type = 'movie' AND runtime_minutes IS NOT NULL) OR "
            "(content_type = 'series' AND episode_count IS NOT NULL)",
            name="ck_type_specific_fields",
        ),
        # GIN indexes for array containment queries (e.g. WHERE 'thriller' = ANY(genres))
        Index("ix_titles_genres_gin", "genres", postgresql_using="gin"),
        Index("ix_titles_themes_gin", "themes", postgresql_using="gin"),
        Index("ix_titles_tones_gin", "tones", postgresql_using="gin"),
        Index("ix_titles_fame", "fame"),
        Index("ix_titles_country", "country"),
        Index("ix_titles_has_poster", "poster_url"),
    )

    def __repr__(self) -> str:
        return f"<Title {self.title!r} ({self.release_year}, {self.content_type.value})>"


def ensure_media_columns(engine) -> None:
    """
    Add poster_url / backdrop_url columns to an EXISTING titles table that
    was created before the Layer-4 fields existed. Idempotent.
    `Base.metadata.create_all` only creates missing tables — it won't ALTER.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE titles ADD COLUMN IF NOT EXISTS poster_url   TEXT"
        ))
        conn.execute(text(
            "ALTER TABLE titles ADD COLUMN IF NOT EXISTS backdrop_url TEXT"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_titles_has_poster ON titles(poster_url)"
        ))


if __name__ == "__main__":
    import os

    db_url = os.environ.get(
        "DATABASE_URL", "postgresql://binge:binge_dev_pw@localhost:5433/binge_intelligence"
    )
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    ensure_media_columns(engine)
    print(f"titles table created / migrated at {db_url}")
