"""
SubscriptionPeriod model — SCD-Type-2 style history of user subscription
state changes over the 11-year sim window.

One user has 1 to N periods. Each period has a start, an optional end
(null = still active at sim's "now" = 2025-12-31), and the reason it started
+ ended.
"""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    create_engine,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.title import Base


class TriggerReason(str, enum.Enum):
    INITIAL_SIGNUP = "initial_signup"
    HOT_RELEASE = "hot_release"
    LIFE_HOLIDAY = "life_holiday"
    LIFE_BOREDOM = "life_boredom"
    RETURN_FROM_BREAK = "return_from_break"
    RANDOM = "random"


class EndReason(str, enum.Enum):
    STILL_ACTIVE = "still_active"        # period is current at sim end
    CHURN_LACK_OF_USE = "churn_lack_of_use"
    CHURN_STREAMING_FATIGUE = "churn_streaming_fatigue"  # 2022+ era
    CHURN_LIFE_EVENT = "churn_life_event"                # busy season pullout


class SubscriptionPeriod(Base):
    __tablename__ = "subscription_periods"

    period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True
    )
    period_num: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 2, 3...

    started_on: Mapped[date] = mapped_column(Date, nullable=False)
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    days_active: Mapped[int] = mapped_column(Integer, nullable=False)

    trigger_reason: Mapped[TriggerReason] = mapped_column(
        Enum(TriggerReason, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    trigger_title_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("titles.title_id"), nullable=True
    )

    end_reason: Mapped[EndReason] = mapped_column(
        Enum(EndReason, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("period_num >= 1", name="ck_period_num_positive"),
        CheckConstraint(
            "ended_on IS NULL OR ended_on >= started_on",
            name="ck_period_dates_ordered",
        ),
        CheckConstraint("days_active >= 0", name="ck_days_active_positive"),
        Index("ix_sub_periods_user_period", "user_id", "period_num"),
        Index("ix_sub_periods_started_on", "started_on"),
        Index("ix_sub_periods_trigger_reason", "trigger_reason"),
    )


if __name__ == "__main__":
    import os

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://binge:binge_dev_pw@localhost:5433/binge_intelligence",
    )
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    print(f"subscription_periods table created (or already existed) at {db_url}")