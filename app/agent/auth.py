"""
AuthContext — the per-request identity + authorization context.

In a real app this would come from a JWT or session cookie. Here it's loaded
from a `user_id` passed to the CLI (acts as the "logged in" user).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


@dataclass
class AuthContext:
    """Who is asking, with the permission info the agent needs."""

    user_id: uuid.UUID
    archetype: str
    age: int
    country: str
    maturity_ceiling: str  # "7+" / "12+" / "16+" / "18+"
    is_minor: bool
    proactive_msg_enabled: bool

    @classmethod
    def from_user(cls, user: User) -> "AuthContext":
        return cls(
            user_id=user.user_id,
            archetype=user.archetype.value,
            age=user.age,
            country=user.country,
            maturity_ceiling=user.maturity_ceiling,
            is_minor=(user.age < 18),
            proactive_msg_enabled=user.proactive_msg_enabled,
        )


def load_auth_context(session: Session, user_id: uuid.UUID) -> AuthContext:
    """Fetch the user and build an AuthContext. Raises if user not found."""
    user = session.scalar(select(User).where(User.user_id == user_id))
    if user is None:
        raise ValueError(f"Unknown user_id: {user_id}")
    return AuthContext.from_user(user)
