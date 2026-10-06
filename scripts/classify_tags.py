"""
scripts/classify_tags.py

Sentence-embedding multi-label classifier for themes and tones.
Deterministic, free, local. Replaces the Gemini paste-workflow entirely.

Pipeline per row:
  synopsis text -> embedding (all-MiniLM-L6-v2)
  cosine similarity vs each theme's descriptive sentence
  cosine similarity vs each tone's descriptive sentence
  keep top-k above SCORE_FLOOR (always at least 1)
  write to mapped/tagged_batch_XXX.csv matching the shape import_tags.py expects

Input:  exports/to_tag_batch_*.csv   (produced by export_with_wiki_plot.py)
Output: mapped/tagged_batch_*.csv    (title_id, tone, theme -- comma-separated)

Run:
    python -m scripts.classify_tags
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from sentence_transformers import SentenceTransformer

from config.vocabularies import THEMES, TONES, TAG_CAPS


EXPORT_DIR = ROOT / "exports"
MAPPED_DIR = ROOT / "mapped"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Anything under this cosine similarity is considered noise and dropped
# UNLESS a row would otherwise have zero tags -- then we keep the top-1 anyway.
SCORE_FLOOR = 0.20


# ---------------------------------------------------------------------------
# Descriptive sentences per label. The classifier scores synopsis embeddings
# against THESE, not the bare tag words -- makes similarity meaningful.
# ---------------------------------------------------------------------------
THEME_DESCRIPTIONS: dict[str, str] = {
    "coming-of-age": "A young protagonist grows up through key life experiences and self-discovery",
    "enemies-to-lovers": "Two characters who begin as adversaries develop romantic feelings for each other",
    "opposites-attract": "Two very different people are drawn together by their differences",
    "forbidden-love": "A romance that must be hidden because society, family, or circumstance forbids it",
    "love-triangle": "Three people caught in overlapping romantic tensions and competing affections",
    "second-chance-romance": "Former lovers reunite and rebuild their relationship after time apart",
    "coming-out": "A person openly reveals their LGBTQ identity to family, friends, or society",
    "found-family": "Unrelated people form deep bonds and become family through shared experience",
    "heist": "A carefully planned robbery or theft executed by a team",
    "whodunit": "A mystery driven by the question of who committed the crime",
    "cat-and-mouse": "A pursuit between hunter and prey where roles shift and stakes escalate",
    "conspiracy": "A hidden plot involving powerful forces working in secret against the protagonist",
    "revenge": "A protagonist seeks violent or calculated payback against those who wronged them",
    "courtroom": "Legal drama centered on trials, lawyers, and the pursuit of justice in court",
    "undercover": "An agent operates in secret identity within a hostile group",
    "redemption": "A flawed character seeks to atone for past wrongs and become better",
    "underdog": "An unlikely hero overcomes overwhelming odds against a stronger opponent",
    "rise-and-fall": "A character achieves great success only to lose it through hubris or misfortune",
    "addiction-recovery": "The struggle to overcome substance abuse and rebuild a life",
    "survival": "A protagonist must endure life-threatening conditions in a hostile environment to stay alive",
    "betrayal": "A trusted person turns against the protagonist, breaking a deep bond",
    "rags-to-riches": "A poor character rises to wealth and status through determination or fortune",
    "dystopia": "A dark future society where control, oppression, or collapse define daily life",
    "post-apocalyptic": "Survivors navigate a world destroyed by disaster, war, or plague",
    "time-travel": "Characters move between past, present, or future timelines",
    "supernatural": "Ghosts, spirits, magic, or otherworldly forces shape the story",
    "chosen-one": "A single protagonist is destined to save the world or fulfill a prophecy",
    "alternate-reality": "The story takes place in a parallel universe or altered version of our world",
    "fish-out-of-water": "A character struggles to adapt to an unfamiliar environment or culture",
    "road-trip": "A journey across places drives the story and transforms the travelers",
    "mentor-protege": "An experienced teacher guides an inexperienced student through challenges",
    "workplace": "The story is centered on a job, office, or professional environment and its dynamics",
    "class-conflict": "Tensions between rich and poor, powerful and powerless, drive the story",
    "immigrant-experience": "A person leaves their homeland to build a new life in a foreign country",
    "war-torn": "Characters live and struggle amid the destruction and chaos of war",
}

TONE_DESCRIPTIONS: dict[str, str] = {
    "swoonworthy": "Romantic, heart-fluttering tension that makes viewers sigh with longing",
    "feel-good": "Uplifting, warm, leaves the viewer happy and hopeful",
    "heartwarming": "Emotionally touching moments of kindness and human connection",
    "cozy": "Comforting, safe, low-stakes atmosphere perfect for relaxation",
    "comforting": "Reassuring and familiar, like a warm blanket",
    "whimsical": "Playful, imaginative, dreamlike, with a light magical quality",
    "campy": "Deliberately over-the-top, theatrical, and self-aware in its excess",
    "satirical": "Sharp mockery of society, politics, or human behavior",
    "bittersweet": "Mixes joy and sadness in equal measure, ending on a wistful note",
    "melancholic": "Sad, reflective, quietly sorrowful throughout",
    "tearjerker": "Deeply emotional story designed to make the viewer cry",
    "thought-provoking": "Raises deep philosophical or moral questions that stay with the viewer",
    "tense": "Sustained anxiety and pressure that grips the viewer throughout",
    "suspenseful": "Uncertainty and dread build toward a critical revelation or moment",
    "dark": "Grim atmosphere with heavy themes and little light or relief",
    "gritty": "Raw, unflinching, realistic with hard consequences and moral ambiguity",
    "disturbing": "Deeply unsettling content that shakes the viewer emotionally",
    "eerie": "Strange, uncanny atmosphere that feels wrong in subtle ways",
    "high-energy": "Fast-paced, kinetic, exhilarating throughout with constant motion",
    "slow-burn": "Deliberately paced, gradually building intensity and payoff over time",
}


def load_export_rows() -> list[dict]:
    if not EXPORT_DIR.exists():
        raise FileNotFoundError(f"{EXPORT_DIR} does not exist. Run export_with_wiki_plot.py first.")

    files = sorted(EXPORT_DIR.glob("to_tag_batch_*.csv"))
    if not files:
        raise FileNotFoundError(f"No to_tag_batch_*.csv in {EXPORT_DIR}")

    rows: list[dict] = []
    for path in files:
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                row["_source"] = path.name
                rows.append(row)
    print(f"Loaded {len(rows)} rows from {len(files)} batch files")
    return rows


def top_k_with_floor(
    scores: np.ndarray, labels: list[str], k_max: int, floor: float
) -> list[str]:
    """
    Return labels sorted by score, keeping only those >= floor, capped at k_max.
    ALWAYS returns at least 1 (top-1 even if it's below the floor -- no empty rows).
    """
    order = np.argsort(-scores)  # descending
    kept: list[str] = []
    for idx in order:
        if len(kept) >= k_max:
            break
        s = float(scores[idx])
        if s < floor and kept:
            break  # once we drop below floor, stop -- unless kept is empty
        kept.append(labels[idx])
    if not kept:
        kept.append(labels[int(order[0])])  # safety: guarantee at least 1
    return kept


def main() -> None:
    started = datetime.utcnow()

    rows = load_export_rows()

    # --- Load model ---
    print(f"\nLoading model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    # --- Embed labels once ---
    print("Embedding label descriptions (one-time)...")
    theme_labels = list(THEME_DESCRIPTIONS.keys())
    theme_texts = [THEME_DESCRIPTIONS[t] for t in theme_labels]
    theme_embs = model.encode(theme_texts, normalize_embeddings=True, show_progress_bar=False)

    tone_labels = list(TONE_DESCRIPTIONS.keys())
    tone_texts = [TONE_DESCRIPTIONS[t] for t in tone_labels]
    tone_embs = model.encode(tone_texts, normalize_embeddings=True, show_progress_bar=False)

    assert set(theme_labels) == set(THEMES), "THEME_DESCRIPTIONS out of sync with vocab"
    assert set(tone_labels) == set(TONES), "TONE_DESCRIPTIONS out of sync with vocab"

    # --- Embed synopses in one batched pass ---
    print(f"Embedding {len(rows)} synopses...")
    synopses = []
    skipped_empty = 0
    valid_rows = []
    for r in rows:
        text = (r.get("synopsis") or "").strip()
        if not text:
            skipped_empty += 1
            continue
        synopses.append(text)
        valid_rows.append(r)

    print(f"  {len(valid_rows)} rows have synopsis text ({skipped_empty} skipped as empty)")

    synopsis_embs = model.encode(
        synopses, normalize_embeddings=True, batch_size=64, show_progress_bar=True
    )

    # --- Score + write per source batch ---
    print("\nClassifying + writing tagged batches...")
    MAPPED_DIR.mkdir(exist_ok=True)
    # clean any stale outputs from previous runs
    for old in MAPPED_DIR.glob("tagged_batch_*.csv"):
        old.unlink()

    theme_max = TAG_CAPS["themes"][1]
    tone_max = TAG_CAPS["tones"][1]

    # group results by source file so output batches mirror input batches
    per_batch: dict[str, list[dict]] = {}

    for i, row in enumerate(valid_rows):
        syn_emb = synopsis_embs[i]

        # cosine similarity via dot product on normalized vectors
        theme_scores = theme_embs @ syn_emb
        tone_scores = tone_embs @ syn_emb

        picked_themes = top_k_with_floor(theme_scores, theme_labels, theme_max, SCORE_FLOOR)
        picked_tones = top_k_with_floor(tone_scores, tone_labels, tone_max, SCORE_FLOOR)

        source = row["_source"]
        # rename to_tag_batch_XXX.csv -> tagged_batch_XXX.csv
        out_name = source.replace("to_tag_batch_", "tagged_batch_")

        per_batch.setdefault(out_name, []).append(
            {
                "title_id": row["tmdb_id"],
                "tone": ", ".join(picked_tones),
                "theme": ", ".join(picked_themes),
            }
        )

    for out_name, out_rows in per_batch.items():
        path = MAPPED_DIR / out_name
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["title_id", "tone", "theme"], quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(out_rows)

    print(f"  wrote {len(per_batch)} tagged batch files to {MAPPED_DIR}/")
    print(f"\nDONE. Elapsed: {datetime.utcnow() - started}")
    print(
        f"\nSpot-check {MAPPED_DIR}/tagged_batch_001.csv against "
        f"exports/to_tag_batch_001.csv BEFORE importing to DB."
    )


if __name__ == "__main__":
    main()