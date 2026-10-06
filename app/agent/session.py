"""
Session manager — Postgres-backed conversation state.

Loads/saves AgentSession, AgentMessage. Survives across CLI invocations
(sessions can be resumed by session_id).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import (
    AgentMessage,
    AgentSession,
    MessageRole,
    SessionStatus,
)


class SessionManager:
    """Thin wrapper over an SQLAlchemy session for persistence."""

    def __init__(self, db: Session, session_id: uuid.UUID):
        self.db = db
        self.session_id = session_id

    @classmethod
    def start_new(cls, db: Session, user_id: uuid.UUID, label: str | None = None) -> "SessionManager":
        s = AgentSession(user_id=user_id, label=label)
        db.add(s)
        db.commit()
        return cls(db, s.session_id)

    @classmethod
    def resume(cls, db: Session, session_id: uuid.UUID) -> "SessionManager":
        s = db.scalar(select(AgentSession).where(AgentSession.session_id == session_id))
        if s is None:
            raise ValueError(f"Unknown session_id: {session_id}")
        if s.status == SessionStatus.ENDED:
            raise ValueError(f"Session {session_id} has ended; start a new one.")
        return cls(db, session_id)

    @classmethod
    def latest_for_user(
        cls, db: Session, user_id: uuid.UUID
    ) -> "SessionManager | None":
        """Return the most-recent ACTIVE session for a user, or None."""
        s = db.scalar(
            select(AgentSession)
            .where(AgentSession.user_id == user_id)
            .where(AgentSession.status == SessionStatus.ACTIVE)
            .order_by(AgentSession.last_active_at.desc())
            .limit(1)
        )
        if s is None:
            return None
        return cls(db, s.session_id)

    # ------------------------------------------------------------------
    # Message I/O
    # ------------------------------------------------------------------
    def next_turn_num(self) -> int:
        existing = self.db.scalar(
            select(AgentMessage.turn_num)
            .where(AgentMessage.session_id == self.session_id)
            .order_by(AgentMessage.turn_num.desc())
            .limit(1)
        )
        return (existing or 0) + 1

    def add_message(
        self,
        role: MessageRole,
        content: str,
        *,
        turn_num: int | None = None,
        tool_name: str | None = None,
        tool_args: dict | None = None,
        tool_result: dict | None = None,
        model_used: str | None = None,
        tokens_used: int | None = None,
        latency_ms: int | None = None,
    ) -> AgentMessage:
        if turn_num is None:
            turn_num = self.next_turn_num()
        msg = AgentMessage(
            session_id=self.session_id,
            turn_num=turn_num,
            role=role,
            content=content,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            model_used=model_used,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
        )
        self.db.add(msg)
        # Touch the session
        s = self.db.scalar(
            select(AgentSession).where(AgentSession.session_id == self.session_id)
        )
        if s is not None:
            s.last_active_at = datetime.utcnow()
            s.turn_count = turn_num
        self.db.commit()
        return msg

    def recent_messages(self, limit: int = 20) -> list[AgentMessage]:
        """Return the latest N messages in chronological order."""
        rows = self.db.scalars(
            select(AgentMessage)
            .where(AgentMessage.session_id == self.session_id)
            .order_by(AgentMessage.turn_num.desc())
            .limit(limit)
        ).all()
        return list(reversed(rows))

    def end(self) -> None:
        s = self.db.scalar(
            select(AgentSession).where(AgentSession.session_id == self.session_id)
        )
        if s is not None:
            s.status = SessionStatus.ENDED
            s.ended_at = datetime.utcnow()
            self.db.commit()
