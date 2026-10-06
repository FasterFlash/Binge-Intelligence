"""
Policy RAG — pgvector-backed retrieval over the `policies/*.md` folder.

- At boot (or when `ensure_policies_indexed` is called), each policy markdown
  file is chunked (by H2) and embedded with the same MiniLM model used for
  the title RAG.
- At query time, retrieve the top-k most-relevant policy chunks for the
  user's question + any tool-context the orchestrator wants to surface.

Why both pgvector and keyword hints:
- Vector catches semantic matches ("my friend watched X" → privacy policy).
- A tiny keyword overlay catches hard-coded cases without waiting for the
  embedding model.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agent.content_rag import EMBEDDING_DIM, embed_text

POLICIES_DIR = Path(
    os.environ.get(
        "POLICIES_DIR",
        str(Path(__file__).resolve().parents[2] / "policies"),
    )
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def ensure_schema(db: Session) -> None:
    db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    db.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS policy_chunks (
                chunk_id SERIAL PRIMARY KEY,
                policy_name TEXT NOT NULL,
                heading TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                embedding vector({EMBEDDING_DIM}),
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (policy_name, heading)
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_policy_chunks_cos
            ON policy_chunks
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 10)
            """
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# Chunk the markdown by H2 sections
# ---------------------------------------------------------------------------
def _chunk_markdown(md: str) -> list[tuple[str, str]]:
    """
    Split on `## ` headings. Returns [(heading, body)].
    Content above the first H2 (e.g. the H1) is attached under "_intro".
    """
    lines = md.splitlines()
    chunks: list[tuple[str, list[str]]] = []
    current_heading = "_intro"
    current_body: list[str] = []
    for ln in lines:
        if ln.startswith("## "):
            if current_body:
                chunks.append((current_heading, current_body))
            current_heading = ln[3:].strip()
            current_body = []
        else:
            current_body.append(ln)
    if current_body:
        chunks.append((current_heading, current_body))
    return [(h, "\n".join(b).strip()) for h, b in chunks if "\n".join(b).strip()]


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def ensure_policies_indexed(db: Session, policies_dir: Path | None = None) -> int:
    """
    Idempotently embed every policy chunk under `policies/*.md`.
    Returns number of chunks (re)written.
    """
    policies_dir = policies_dir or POLICIES_DIR
    if not policies_dir.exists():
        return 0

    ensure_schema(db)
    written = 0

    for md_path in sorted(policies_dir.glob("*.md")):
        name = md_path.stem
        md = md_path.read_text(encoding="utf-8")
        for heading, body in _chunk_markdown(md):
            h = _hash(body)
            existing = db.execute(
                text(
                    """
                    SELECT content_hash FROM policy_chunks
                    WHERE policy_name = :name AND heading = :heading
                    """
                ),
                {"name": name, "heading": heading},
            ).scalar_one_or_none()
            if existing == h:
                continue
            vec = embed_text(f"{heading}\n\n{body}")
            vec_str = "[" + ",".join(str(x) for x in vec) + "]"
            db.execute(
                text(
                    """
                    INSERT INTO policy_chunks (policy_name, heading, body, content_hash, embedding)
                    VALUES (:name, :heading, :body, :h, CAST(:vec AS vector))
                    ON CONFLICT (policy_name, heading) DO UPDATE
                      SET body = EXCLUDED.body,
                          content_hash = EXCLUDED.content_hash,
                          embedding = EXCLUDED.embedding
                    """
                ),
                {"name": name, "heading": heading, "body": body, "h": h, "vec": vec_str},
            )
            written += 1
    db.commit()
    return written


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
_KEYWORD_HINTS: dict[str, list[str]] = {
    "privacy": [
        r"\bfriend\b", r"\bother user\b", r"\banother user\b", r"\busername\b",
        r"\bemail\b", r"\bpassword\b", r"\bwho else\b", r"\bmy roommate\b",
        r"\bmy wife\b", r"\bmy husband\b", r"\bmy kid\b", r"\bmy child\b",
    ],
    "content_safety": [
        r"\b18\+", r"\b16\+", r"\badult\b", r"\bexplicit\b", r"\bgore\b",
        r"\bhorror\b", r"\bspoiler", r"\bending\b", r"\bself.?harm\b",
        r"\bsuicid",
    ],
    "platform": [
        r"\bare you (a )?(human|real|person|bot|ai)\b",
        r"\brefund\b", r"\bbilling\b", r"\bcancel\b", r"\bupgrade\b",
        r"\bcustomer support\b",
    ],
    "response_style": [
        r"\bfeel\b", r"\bsad\b", r"\bhappy\b", r"\bbreakup\b", r"\bboyfriend\b",
        r"\bgirlfriend\b", r"\bkiss\b", r"\bbored\b", r"\bexcited\b",
        r"\bcelebrat", r"\brough day\b",
    ],
}


def _keyword_hits(query: str) -> list[str]:
    q = query.lower()
    hit = []
    for policy, patterns in _KEYWORD_HINTS.items():
        for p in patterns:
            if re.search(p, q):
                hit.append(policy)
                break
    return hit


def retrieve_policies(
    db: Session,
    query: str,
    k: int = 3,
) -> list[dict[str, Any]]:
    """
    Return the top-k relevant policy chunks for `query`, combining:
    - vector similarity
    - keyword overlay (forces inclusion of policies whose hints match)
    """
    qvec = embed_text(query)
    qvec_str = "[" + ",".join(str(x) for x in qvec) + "]"

    sql = """
        SELECT policy_name, heading, body,
               1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
        FROM policy_chunks
        ORDER BY embedding <=> CAST(:qvec AS vector)
        LIMIT :limit
    """
    rows = db.execute(
        text(sql), {"qvec": qvec_str, "limit": max(k, 4)}
    ).mappings().all()
    results = [dict(r) for r in rows]

    # Overlay: forced policies from keyword hints
    forced = _keyword_hits(query)
    if forced:
        existing_names = {r["policy_name"] for r in results}
        for pname in forced:
            if pname in existing_names:
                continue
            extra = db.execute(
                text(
                    """
                    SELECT policy_name, heading, body, 0.0 AS similarity
                    FROM policy_chunks
                    WHERE policy_name = :name
                    ORDER BY chunk_id
                    LIMIT 2
                    """
                ),
                {"name": pname},
            ).mappings().all()
            results.extend(dict(r) for r in extra)

    # De-dup, then trim
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for r in results:
        key = (r["policy_name"], r["heading"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out[:k]


def format_policies_for_prompt(chunks: list[dict[str, Any]]) -> str:
    """Format retrieved policy chunks as a compact block for the system prompt."""
    if not chunks:
        return ""
    lines = ["[Relevant policies for this turn:]"]
    for c in chunks:
        lines.append(f"\n### {c['policy_name']} — {c['heading']}")
        lines.append(c["body"])
    return "\n".join(lines)
