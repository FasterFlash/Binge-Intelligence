"""
scripts/agent_cli.py

Interactive CLI for the Binge Intelligence agent.

Usage:
    # One-shot
    python -m scripts.agent_cli --user-id <uuid> "recommend me something to cry to"

    # Interactive (REPL)
    python -m scripts.agent_cli --user-id <uuid>

    # Resume an existing session
    python -m scripts.agent_cli --user-id <uuid> --session-id <uuid>

    # Pick a user interactively from a list of samples
    python -m scripts.agent_cli --pick-user

Env:
    GROQ_API_KEY        (required if LLM_PROVIDER=groq, the default)
    GEMINI_API_KEY      (required if LLM_PROVIDER=gemini)
    LLM_PROVIDER        "groq" | "gemini"  (default "groq")
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.agent.auth import load_auth_context
from app.agent.orchestrator import Orchestrator
from app.agent.policy_rag import ensure_policies_indexed
from app.agent.providers import get_provider
from app.agent.session import SessionManager
from app.models.title import Base
# Register models with Base.metadata so create_all catches them
from app.models.user import User, Household  # noqa: F401
from app.models.subscription import SubscriptionPeriod  # noqa: F401
from app.models.behavior import (  # noqa: F401
    Session as WatchSession,
    WatchEvent,
    UserSeriesProgress,
)
from app.models.gold import (  # noqa: F401
    UserWatchStats, TitleEngagementStats, CalendarActivity, CohortMetrics,
)
from app.models.agent import AgentSession, AgentMessage, AgentEvent  # noqa: F401


def pick_user_interactive(db: Session) -> uuid.UUID:
    """Show one sample user per archetype and let the operator choose."""
    rows = db.execute(
        text(
            """
            SELECT DISTINCT ON (archetype) user_id, archetype, country, age, cohort_year
            FROM users
            WHERE cohort_year <= 2022
            ORDER BY archetype, user_id
            """
        )
    ).mappings().all()
    print("\nPick a user to log in as:\n")
    for i, r in enumerate(rows, 1):
        print(f"  {i}. {r['archetype']:20s}  {r['country']} age {r['age']}  "
              f"cohort {r['cohort_year']}   {r['user_id']}")
    while True:
        choice = input("\n>>> number: ").strip()
        try:
            idx = int(choice) - 1
            return rows[idx]["user_id"]
        except (ValueError, IndexError):
            print("Invalid choice.")


def ensure_agent_tables(engine) -> None:
    Base.metadata.create_all(engine)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", help="UUID of the user to log in as")
    ap.add_argument("--session-id", help="Resume an existing session by UUID")
    ap.add_argument("--pick-user", action="store_true", help="Pick a user from a list")
    ap.add_argument("--provider", help="Override LLM provider: groq | gemini")
    ap.add_argument("question", nargs="?", help="One-shot question (optional)")
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL)
    ensure_agent_tables(engine)

    with Session(engine, expire_on_commit=False) as db:
        # Make sure policy chunks are indexed (idempotent, fast on re-runs)
        try:
            n = ensure_policies_indexed(db)
            if n:
                print(f"[policies] indexed {n} new/updated chunks")
        except Exception as e:
            print(f"[policies] indexing skipped: {e}")


        # Resolve user
        if args.pick_user or not args.user_id:
            if not args.user_id and not args.pick_user:
                print("--user-id required (or use --pick-user)")
                sys.exit(1)
            user_id = pick_user_interactive(db) if args.pick_user else uuid.UUID(args.user_id)
        else:
            user_id = uuid.UUID(args.user_id)

        ctx = load_auth_context(db, user_id)
        print(f"\nLogged in as: {ctx.archetype} / {ctx.country} / age {ctx.age}")

        # Resolve session
        if args.session_id:
            sm = SessionManager.resume(db, uuid.UUID(args.session_id))
            print(f"Resumed session: {sm.session_id}")
        else:
            existing = SessionManager.latest_for_user(db, user_id)
            if existing is not None:
                sm = existing
                print(f"Resumed latest active session: {sm.session_id}")
            else:
                sm = SessionManager.start_new(db, user_id)
                print(f"Started new session: {sm.session_id}")

        provider = get_provider(args.provider)
        print(f"LLM provider: {provider.name} ({provider.default_model})\n")

        orch = Orchestrator(db, ctx, sm, provider)

        # One-shot mode
        if args.question:
            answer = orch.ask(args.question)
            print(f"\n{answer}\n")
            return

        # REPL mode
        print("Type a question. Ctrl-D or 'exit' to quit.\n")
        try:
            while True:
                try:
                    q = input(">>> ").strip()
                except EOFError:
                    print()
                    break
                if not q:
                    continue
                if q.lower() in {"exit", "quit"}:
                    break
                answer = orch.ask(q)
                print(f"\n{answer}\n")
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main()
