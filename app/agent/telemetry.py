"""
Lightweight observability — append AgentEvent rows for anything worth
replaying later (tool calls, refusals, judge verdicts, errors).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.agent import AgentEvent, EventType


def log_event(
    db: Session,
    event_type: EventType,
    *,
    session_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> None:
    ev = AgentEvent(
        session_id=session_id,
        message_id=message_id,
        event_type=event_type,
        details=details or {},
    )
    db.add(ev)
    db.commit()
