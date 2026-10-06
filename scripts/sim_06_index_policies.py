"""
scripts/sim_06_index_policies.py

Idempotently chunk + embed every markdown file under policies/*.md into
the pgvector-backed `policy_chunks` table.

Run once after adding/updating policies. Safe to re-run.

Usage:
    python -m scripts.sim_06_index_policies
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.agent.policy_rag import POLICIES_DIR, ensure_policies_indexed


def main() -> None:
    engine = create_engine(DATABASE_URL)
    with Session(engine) as db:
        print(f"Indexing policies from: {POLICIES_DIR}")
        n = ensure_policies_indexed(db)
        print(f"(Re)wrote {n} policy chunks.")


if __name__ == "__main__":
    main()
