"""
Wikipedia API client — searches for a film/TV page and extracts its plot section.

Uses the public MediaWiki API. Free, no auth. Polite: rate-limited to ~1 req/s
and sends a proper User-Agent as Wikipedia asks.

Two endpoints:
  - action=query&list=search      -> find candidate pages
  - action=parse&prop=sections    -> get section index for a page
  - action=parse&section=N        -> fetch one section's HTML
"""

from __future__ import annotations

import html
import re
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "BingeIntelligence/0.1 (educational portfolio project; contact: github.com/FasterFlash)"

# Section titles that typically contain the plot, in preference order
PLOT_SECTION_TITLES = [
    "plot",
    "synopsis",
    "story",
    "premise",
    "plot summary",
    "storyline",
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


_SESSION = _session()
_SLEEP_BETWEEN_CALLS = 0.5  # 2 req/s, polite to Wikipedia


def _get(params: dict[str, Any]) -> dict:
    params = {**params, "format": "json", "formatversion": "2"}
    resp = _SESSION.get(WIKI_API, params=params, timeout=15)
    resp.raise_for_status()
    time.sleep(_SLEEP_BETWEEN_CALLS)
    return resp.json()


def _strip_html(html_text: str) -> str:
    """Turn Wikipedia HTML section into plain text."""
    # remove reference markers [1], [2], [citation needed] etc.
    text = re.sub(r"\[\d+\]", "", html_text)
    text = re.sub(r"\[[^\]]+\]", "", text)
    # remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # unescape entities
    text = html.unescape(text)
    # collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def search_page(title: str, year: int, kind: str) -> str | None:
    """
    Return the best-guess Wikipedia page title for a movie/TV series, or None.
    kind: "movie" or "tv"
    """
    # try a targeted query first, then a general one
    queries = []
    if kind == "movie":
        queries.append(f"{title} {year} film")
        queries.append(f"{title} film")
    else:
        queries.append(f"{title} {year} TV series")
        queries.append(f"{title} TV series")
    queries.append(title)

    for q in queries:
        try:
            data = _get({"action": "query", "list": "search", "srsearch": q, "srlimit": 5})
        except Exception:
            continue

        hits = data.get("query", {}).get("search", [])
        if not hits:
            continue

        # score candidates: prefer hits whose title contains the year OR the kind hint
        year_str = str(year)
        kind_hints = ("film",) if kind == "movie" else ("series", "TV series", "television")

        best = None
        best_score = -1
        for hit in hits:
            page_title = hit.get("title", "")
            snippet = (hit.get("snippet") or "").lower()
            score = 0

            if year_str in page_title:
                score += 3
            if any(h in page_title for h in kind_hints):
                score += 2
            if any(h in snippet for h in kind_hints):
                score += 1
            # reward exact title prefix match
            if page_title.lower().startswith(title.lower()):
                score += 2

            if score > best_score:
                best_score = score
                best = page_title

        if best and best_score >= 2:
            return best

    return None


def fetch_plot(page_title: str) -> str | None:
    """
    Fetch the plot/synopsis section of a Wikipedia page. Returns plain text
    or None if no plot-like section is found.
    """
    try:
        # 1) get section index
        data = _get({"action": "parse", "page": page_title, "prop": "sections"})
    except Exception:
        return None

    sections = data.get("parse", {}).get("sections", [])
    if not sections:
        return None

    # find the highest-priority plot-like section
    target_index = None
    for wanted in PLOT_SECTION_TITLES:
        for s in sections:
            if (s.get("line") or "").strip().lower() == wanted:
                target_index = s.get("index")
                break
        if target_index:
            break

    if not target_index:
        return None

    # 2) fetch that section's HTML
    try:
        data = _get({"action": "parse", "page": page_title, "section": target_index, "prop": "text"})
    except Exception:
        return None

    raw_html = data.get("parse", {}).get("text")
    if not raw_html:
        return None

    plain = _strip_html(raw_html)
    if len(plain) < 80:  # too short to be a real plot
        return None
    return plain


def get_plot(title: str, year: int, kind: str) -> tuple[str | None, str | None]:
    """
    Convenience: search + fetch plot in one call.
    Returns (plot_text, page_title_used) — either may be None.
    """
    page = search_page(title, year, kind)
    if not page:
        return None, None
    plot = fetch_plot(page)
    return plot, page