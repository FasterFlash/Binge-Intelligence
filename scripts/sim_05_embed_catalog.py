"""
scripts/sim_05_embed_catalog.py

Embed every title into the `title_embeddings` pgvector table.

Corpus per title (NEW — after Phase B TMDB enrichment):
    [TAGLINE] + [SYNOPSIS] + [KEYWORDS] + [DIRECTOR] + [TOP-5 CAST]

The embedding content_hash includes everything above so changes anywhere
trigger an automatic re-embed. Idempotent + resumable.

Run:
    python -m scripts.sim_05_embed_catalog
    python -m scripts.sim_05_embed_catalog --force       # redo every title
    python -m scripts.sim_05_embed_catalog --limit 100   # test subset
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.agent.content_rag import ensure_schema, get_embedding_model


BATCH_SIZE = 64


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def build_corpus_rows(db: Session, limit: int | None) -> list[dict]:
    """
    One query pulls everything we need per title:
      - synopsis + tagline + keywords   (from titles)
      - director / creator / showrunner (from title_crew)
      - top-5 billed cast               (from title_cast ordered by billing_order)

    If Phase-B tables aren't populated yet, the LEFT JOINs degrade gracefully
    (empty arrays) and the embedding falls back to synopsis-only.
    """
    sql = """
        SELECT
            t.title_id,
            t.title,
            COALESCE(t.synopsis, '')                           AS synopsis,
            COALESCE(t.tagline, '')                            AS tagline,
            COALESCE(t.keywords, ARRAY[]::text[])              AS keywords,
            (
                SELECT COALESCE(ARRAY_AGG(p.name ORDER BY tc.billing_order), ARRAY[]::text[])
                FROM title_cast tc
                JOIN people p ON p.person_id = tc.person_id
                WHERE tc.title_id = t.title_id
                  AND tc.billing_order < 5
            )                                                  AS top_cast,
            (
                SELECT COALESCE(ARRAY_AGG(DISTINCT p.name), ARRAY[]::text[])
                FROM title_crew tc
                JOIN people p ON p.person_id = tc.person_id
                WHERE tc.title_id = t.title_id
                  AND tc.job IN ('Director', 'Creator', 'Showrunner')
            )                                                  AS directors
        FROM titles t
        ORDER BY t.tmdb_popularity DESC NULLS LAST
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in db.execute(text(sql)).mappings().all()]


def build_text(row: dict) -> str:
    """
    Assemble the embedding corpus for a single title. Keeps rough semantic
    blocks separated by blank lines so the sentence transformer can treat
    them as distinct sentences.
    """
    parts: list[str] = []
    title = (row.get("title") or "").strip()
    tagline = (row.get("tagline") or "").strip()
    synopsis = (row.get("synopsis") or "").strip()
    keywords = row.get("keywords") or []
    cast = row.get("top_cast") or []
    directors = row.get("directors") or []

    # Title line gives the model a soft anchor
    if title:
        parts.append(title)
    if tagline:
        parts.append(tagline)
    if synopsis:
        parts.append(synopsis[:2000])
    if keywords:
        parts.append("Themes: " + ", ".join(keywords[:20]))
    if directors:
        parts.append("Directed by " + ", ".join(directors[:3]))
    if cast:
        parts.append("Starring " + ", ".join(cast[:5]))

    return "\n\n".join(parts).strip()[:3500]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="Re-embed every title regardless of hash")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only process the first N titles")
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL)
    with Session(engine) as db:
        print("Ensuring pgvector schema...")
        ensure_schema(db)

        rows = build_corpus_rows(db, limit=args.limit)
        print(f"Catalog size: {len(rows)}")

        # Hashes already in the DB
        existing: dict = {}
        if not args.force:
            existing = {
                r[0]: r[1]
                for r in db.execute(
                    text("SELECT title_id, synopsis_hash FROM title_embeddings")
                ).all()
            }

        to_embed: list[dict] = []
        payloads: dict = {}  # title_id -> (text, hash)
        for row in rows:
            corpus = build_text(row)
            if not corpus:
                continue
            h = _hash(corpus)
            if not args.force and existing.get(row["title_id"]) == h:
                continue
            to_embed.append(row)
            payloads[row["title_id"]] = (corpus, h)

        print(f"Needing embedding: {len(to_embed)}")
        if not to_embed:
            print("All up to date.")
            return

        model = get_embedding_model()
        print(f"Loaded model: {model.__class__.__name__}")

        t0 = time.time()
        for i in range(0, len(to_embed), BATCH_SIZE):
            chunk = to_embed[i : i + BATCH_SIZE]
            texts = [payloads[r["title_id"]][0] for r in chunk]
            vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

            for row, v in zip(chunk, vecs):
                corpus, h = payloads[row["title_id"]]
                vec_str = "[" + ",".join(f"{float(x):.6f}" for x in v) + "]"
                db.execute(
                    text(
                        """
                        INSERT INTO title_embeddings (title_id, embedding, synopsis_hash)
                        VALUES (:tid, CAST(:vec AS vector), :h)
                        ON CONFLICT (title_id) DO UPDATE
                            SET embedding = EXCLUDED.embedding,
                                synopsis_hash = EXCLUDED.synopsis_hash,
                                created_at = NOW()
                        """
                    ),
                    {"tid": row["title_id"], "vec": vec_str, "h": h},
                )
            db.commit()
            done = min(i + BATCH_SIZE, len(to_embed))
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed else 0
            eta = (len(to_embed) - done) / rate if rate else 0
            print(f"  {done}/{len(to_embed)}  elapsed={elapsed:.1f}s  "
                  f"{rate:.1f}/s  ETA {eta:.0f}s")

        print(f"\nDONE. Elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
