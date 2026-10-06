"""
Content RAG — pgvector-backed semantic search over title synopses.

- One-time embedding script (sim_05_embed_catalog.py) populates
  `title_embeddings` with vectors of each title's synopsis.
- At query time, embed the user's query and KNN-search for closest titles.

Model: sentence-transformers/all-MiniLM-L6-v2 (local, free, 384-dim).

Spoiler-tier handling: synopses in `titles.synopsis` are TMDB blurb +
Wikipedia plot. We tag the whole thing as S1 for now (coarse but functional).
When the agent surfaces results, it filters/truncates by the user's current
watch progress for series.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

EMBEDDING_MODEL_NAME = os.environ.get(
    "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
EMBEDDING_DIM = 384


@lru_cache(maxsize=1)
def get_embedding_model():
    """Lazy-loaded SBERT model. Returns a callable `encode([texts]) -> list[np.ndarray]`."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def embed_text(text: str) -> list[float]:
    model = get_embedding_model()
    vec = model.encode(text, normalize_embeddings=True)
    return [float(x) for x in vec]


def ensure_schema(db: Session) -> None:
    """Create pgvector extension + title_embeddings table if missing."""
    db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    db.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS title_embeddings (
                title_id UUID PRIMARY KEY REFERENCES titles(title_id),
                embedding vector({EMBEDDING_DIM}),
                synopsis_hash TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    # IVFFlat index for approximate KNN (fast at our scale)
    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_title_embeddings_cos
            ON title_embeddings
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
            """
        )
    )
    db.commit()


def search_titles(
    db: Session,
    query: str,
    limit: int = 10,
    maturity_ceiling: str | None = None,
    country_filter: str | None = None,
) -> list[dict[str, Any]]:
    """
    Semantic search over title synopses with optional hard filters.

    maturity_ceiling: "7+" | "12+" | "16+" | "18+" (results at or below).
    country_filter: filter by title.country (ISO).
    """
    qvec = embed_text(query)
    qvec_str = "[" + ",".join(str(x) for x in qvec) + "]"

    maturity_order = ["7+", "12+", "16+", "18+"]
    allowed = (
        maturity_order[: maturity_order.index(maturity_ceiling) + 1]
        if maturity_ceiling in maturity_order
        else maturity_order
    )

    sql = """
        SELECT t.title_id, t.title, t.release_year, t.content_type,
               t.country, t.genres, t.themes, t.tones, t.synopsis,
               t.fame, t.maturity_rating,
               1 - (te.embedding <=> CAST(:qvec AS vector)) AS similarity
        FROM title_embeddings te
        JOIN titles t ON te.title_id = t.title_id
        WHERE t.maturity_rating = ANY(:allowed)
    """
    params: dict[str, Any] = {"qvec": qvec_str, "allowed": allowed}

    if country_filter:
        sql += " AND t.country = :country"
        params["country"] = country_filter

    sql += " ORDER BY te.embedding <=> CAST(:qvec AS vector) LIMIT :limit"
    params["limit"] = limit

    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]
