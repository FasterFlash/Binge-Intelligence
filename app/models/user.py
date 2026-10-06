"""
Household + User models.

Uses the same Base as title.py so all tables share metadata.
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
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.title import Base


class HouseholdType(str, enum.Enum):
    COUPLE = "couple"
    ROOMMATES = "roommates"
    FAMILY_WITH_KIDS = "family_with_kids"


class AgeBand(str, enum.Enum):
    KID = "7-12"
    TEEN = "13-17"
    YOUNG_ADULT = "18-24"
    ADULT = "25-34"
    MIDDLE_AGED = "35-49"
    OLDER = "50+"


class Archetype(str, enum.Enum):
    BINGER = "binger"
    CASUAL_TRICKLER = "casual_trickler"
    SAMPLER = "sampler"
    COMFORT_REWATCHER = "comfort_rewatcher"
    WEEKEND_WARRIOR = "weekend_warrior"
    EVENT_VIEWER = "event_viewer"
    NIGHT_OWL = "night_owl"


class LifeContextPattern(str, enum.Enum):
    KID = "kid"
    STUDENT = "student"
    WORKING_PROFESSIONAL = "working_professional"
    RETIRED = "retired"


class Household(Base):
    __tablename__ = "households"

    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    country: Mapped[str] = mapped_column(String(8), nullable=False)
    household_type: Mapped[HouseholdType] = mapped_column(
        Enum(HouseholdType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_households_country", "country"),
    )


class User(Base):
    __tablename__ = "users"

    # --- Identity ---
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.household_id"), nullable=True
    )
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    age_band: Mapped[AgeBand] = mapped_column(
        Enum(AgeBand, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    country: Mapped[str] = mapped_column(String(8), nullable=False)
    signup_date: Mapped[date] = mapped_column(Date, nullable=False)
    cohort_year: Mapped[int] = mapped_column(Integer, nullable=False)
    maturity_ceiling: Mapped[str] = mapped_column(String(8), nullable=False)
    archetype: Mapped[Archetype] = mapped_column(
        Enum(Archetype, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    proactive_msg_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    life_context_pattern: Mapped[LifeContextPattern] = mapped_column(
        Enum(LifeContextPattern, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )

    # --- Affinity vectors (JSONB) ---
    genre_affinity: Mapped[dict] = mapped_column(JSONB, nullable=False)
    theme_affinity: Mapped[dict] = mapped_column(JSONB, nullable=False)
    tone_affinity: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # --- Behavioral rhythm ---
    weekday_activity: Mapped[list[float]] = mapped_column(
        ARRAY(Float), nullable=False
    )  # length 7 (Mon..Sun)
    hour_of_day_profile: Mapped[list[float]] = mapped_column(
        ARRAY(Float), nullable=False
    )  # length 24
    session_intensity_mean: Mapped[float] = mapped_column(Float, nullable=False)
    session_intensity_std: Mapped[float] = mapped_column(Float, nullable=False)

    # --- Style parameters ---
    mainstream_susceptibility: Mapped[float] = mapped_column(Float, nullable=False)
    novelty_seeking: Mapped[float] = mapped_column(Float, nullable=False)
    completion_short: Mapped[float] = mapped_column(Float, nullable=False)
    completion_long: Mapped[float] = mapped_column(Float, nullable=False)
    concurrent_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    rewatch_tendency: Mapped[float] = mapped_column(Float, nullable=False)
    mood_variance: Mapped[float] = mapped_column(Float, nullable=False)
    hot_release_trigger_prob: Mapped[float] = mapped_column(Float, nullable=False)
    movie_series_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    subtitle_tolerance: Mapped[float] = mapped_column(Float, nullable=False)
    sleep_dropoff: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # --- Bookkeeping ---
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("age BETWEEN 5 AND 100", name="ck_user_age_range"),
        CheckConstraint(
            "mainstream_susceptibility BETWEEN 0 AND 1",
            name="ck_mainstream_range",
        ),
        CheckConstraint("novelty_seeking BETWEEN 0 AND 1", name="ck_novelty_range"),
        CheckConstraint(
            "completion_short BETWEEN 0 AND 1", name="ck_comp_short_range"
        ),
        CheckConstraint("completion_long BETWEEN 0 AND 1", name="ck_comp_long_range"),
        CheckConstraint(
            "concurrent_capacity BETWEEN 1 AND 6", name="ck_concurrent_range"
        ),
        Index("ix_users_archetype", "archetype"),
        Index("ix_users_country", "country"),
        Index("ix_users_cohort_year", "cohort_year"),
        Index("ix_users_household_id", "household_id"),
        Index("ix_users_age_band", "age_band"),
    )


if __name__ == "__main__":
    import os

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://binge:binge_dev_pw@localhost:5433/binge_intelligence",
    )
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    print(f"households + users tables created (or already existed) at {db_url}")