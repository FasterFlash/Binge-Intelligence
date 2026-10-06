"""
People model — the cast + crew side of the catalog.

Three tables:
    people          — one row per unique person (dedup by tmdb_person_id)
    title_cast      — actor associations (character + billing order)
    title_crew      — director / writer / creator / etc.

Headshots are stored in MinIO under cast/<person_id>.jpg and the photo_url
column holds the public URL (parallel to titles.poster_url).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
    create_engine, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.title import Base


class Person(Base):
    __tablename__ = "people"

    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tmdb_person_id: Mapped[int] = mapped_column(
        Integer, nullable=False, unique=True, index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Soft metadata TMDB gives us
    known_for_department: Mapped[str | None] = mapped_column(String(64), nullable=True)
    popularity: Mapped[float | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tmdb_person_id", name="uq_people_tmdb_person_id"),
        Index("ix_people_name_lower", "name"),
    )


class TitleCast(Base):
    __tablename__ = "title_cast"

    cast_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("titles.title_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.person_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    character_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_order: Mapped[int] = mapped_column(Integer, nullable=False, default=999)

    __table_args__ = (
        UniqueConstraint("title_id", "person_id", "character_name",
                         name="uq_title_cast_tpc"),
        Index("ix_title_cast_order", "title_id", "billing_order"),
    )


class TitleCrew(Base):
    __tablename__ = "title_crew"

    crew_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("titles.title_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.person_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    job: Mapped[str] = mapped_column(String(128), nullable=False)
    department: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("title_id", "person_id", "job",
                         name="uq_title_crew_tpj"),
        Index("ix_title_crew_job", "title_id", "job"),
    )


def ensure_cast_tables(engine) -> None:
    """Create cast tables + extend titles with Phase-B columns. Idempotent."""
    Base.metadata.create_all(engine, tables=[
        Person.__table__, TitleCast.__table__, TitleCrew.__table__,
    ])
    with engine.begin() as conn:
        # Extra factual + metadata columns on titles — nullable so existing rows survive
        conn.execute(text(
            "ALTER TABLE titles ADD COLUMN IF NOT EXISTS tagline     TEXT"
        ))
        conn.execute(text(
            "ALTER TABLE titles ADD COLUMN IF NOT EXISTS imdb_id     TEXT"
        ))
        conn.execute(text(
            "ALTER TABLE titles ADD COLUMN IF NOT EXISTS tmdb_rating REAL"
        ))
        conn.execute(text(
            "ALTER TABLE titles ADD COLUMN IF NOT EXISTS keywords    TEXT[]"
        ))
        # Server-side defaults so raw INSERT ... VALUES statements work
        # without supplying the PK or timestamp (SQLAlchemy's Python-side
        # defaults don't fire for `db.execute(text(...))`).
        # pgcrypto gives us gen_random_uuid() on all modern Postgres versions.
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        conn.execute(text(
            "ALTER TABLE people     ALTER COLUMN person_id  SET DEFAULT gen_random_uuid()"
        ))
        conn.execute(text(
            "ALTER TABLE title_cast ALTER COLUMN cast_id    SET DEFAULT gen_random_uuid()"
        ))
        conn.execute(text(
            "ALTER TABLE title_crew ALTER COLUMN crew_id    SET DEFAULT gen_random_uuid()"
        ))
        # created_at — ORM-side default=datetime.utcnow doesn't fire for raw
        # inserts either, and the column is NOT NULL on the people table.
        conn.execute(text(
            "ALTER TABLE people     ALTER COLUMN created_at SET DEFAULT NOW()"
        ))


if __name__ == "__main__":
    import os
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://binge:binge_dev_pw@localhost:5433/binge_intelligence",
    )
    engine = create_engine(db_url)
    ensure_cast_tables(engine)
    print(f"cast tables + Phase-B columns ready at {db_url}")
