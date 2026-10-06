"""
Agent persistence tables — session state across restarts.

- agent_sessions: one row per conversation session
- agent_messages: every turn (user / assistant / tool_result)
- agent_events:  observability log (tool calls, refusals, errors, judge verdicts)
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.title import Base


class SessionStatus(str, enum.Enum):
    ACTIVE = "active"
    ENDED = "ended"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_RESULT = "tool_result"
    SYSTEM = "system"


class EventType(str, enum.Enum):
    TOOL_CALLED = "tool_called"
    TOOL_FAILED = "tool_failed"
    SQL_FALLBACK = "sql_fallback"
    REFUSAL = "refusal"
    PROMPT_INJECTION_DETECTED = "prompt_injection_detected"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"
    JUDGE_VERDICT = "judge_verdict"
    LLM_CALL = "llm_call"


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=SessionStatus.ACTIVE,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_active_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_agent_sessions_user_last", "user_id", "last_active_at"),
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_sessions.session_id"),
        nullable=False,
        index=True,
    )
    turn_num: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Tool-call fields (populated for assistant messages that call a tool,
    # and for tool_result messages carrying the result)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_args: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tool_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Telemetry
    model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_agent_messages_session_turn", "session_id", "turn_num"),
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_sessions.session_id"),
        nullable=True,
        index=True,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_messages.message_id"), nullable=True
    )
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_agent_events_session_type", "session_id", "event_type"),
    )


if __name__ == "__main__":
    import os

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://binge:binge_dev_pw@localhost:5433/binge_intelligence",
    )
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    print(f"agent tables created at {db_url}")
