"""
TMDB API client — narrow, typed, retry-aware.

Two endpoint families used:
  - /discover/{movie|tv}   -> paginated lists filtered by region + year
  - /{movie|tv}/{id}       -> full detail (runtimes, seasons, ratings)

Also fetches US content rating via:
  - /movie/{id}/release_dates
  - /tv/{id}/content_ratings

Polite: sleeps briefly between calls (TMDB allows ~50 req/s but we don't need
to push it) and retries transient failures.
"""

import time
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.config import TMDB_API_KEY, TMDB_BASE_URL


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


_SESSION = _session()
_SLEEP_BETWEEN_CALLS = 0.05  # 20 req/s, well under limit


def _get(path: str, params: dict[str, Any] | None = None) -> dict:
    params = dict(params or {})
    params["api_key"] = TMDB_API_KEY
    resp = _SESSION.get(f"{TMDB_BASE_URL}{path}", params=params, timeout=15)
    resp.raise_for_status()
    time.sleep(_SLEEP_BETWEEN_CALLS)
    return resp.json()


def discover(
    kind: str,               # "movie" or "tv"
    region: str,             # ISO country code, e.g. "US"
    year: int,
    page: int,
    language: str = "en-US",
) -> dict:
    """One page of discovery results. 20 items per page."""
    year_key = "primary_release_year" if kind == "movie" else "first_air_date_year"
    params = {
        "language": language,
        "sort_by": "popularity.desc",
        "include_adult": "false",
        "page": page,
        "with_origin_country": region,
        year_key: year,
    }
    return _get(f"/discover/{kind}", params)


def get_detail(kind: str, tmdb_id: int) -> dict:
    """Full detail for one title (runtime, seasons, etc)."""
    return _get(f"/{kind}/{tmdb_id}")


def get_us_rating(kind: str, tmdb_id: int) -> str | None:
    """Return the US content rating string (e.g. 'PG-13', 'TV-MA') or None."""
    try:
        if kind == "movie":
            data = _get(f"/movie/{tmdb_id}/release_dates")
            for entry in data.get("results", []):
                if entry.get("iso_3166_1") == "US":
                    for rd in entry.get("release_dates", []):
                        cert = (rd.get("certification") or "").strip()
                        if cert:
                            return cert
        else:  # tv
            data = _get(f"/tv/{tmdb_id}/content_ratings")
            for entry in data.get("results", []):
                if entry.get("iso_3166_1") == "US":
                    cert = (entry.get("rating") or "").strip()
                    if cert:
                        return cert
    except Exception:
        return None
    return None


def iter_discover(
    kind: str,
    region: str,
    years: Iterable[int],
    target_count: int,
) -> list[dict]:
    """
    Fetch discovery results across a set of years for one region until we
    hit target_count. Rotates through years so the catalog isn't top-heavy
    on one year.
    """
    results: list[dict] = []
    seen_ids: set[int] = set()
    years = list(years)
    page_by_year = {y: 1 for y in years}

    while len(results) < target_count:
        made_progress = False
        for year in years:
            if len(results) >= target_count:
                break
            page = page_by_year[year]
            try:
                data = discover(kind, region, year, page)
            except requests.HTTPError:
                continue

            items = data.get("results", [])
            if not items:
                continue

            for item in items:
                tid = item.get("id")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    item["_kind"] = kind
                    item["_region"] = region
                    results.append(item)
                    if len(results) >= target_count:
                        break

            page_by_year[year] = page + 1
            if page < data.get("total_pages", 1):
                made_progress = True

        if not made_progress:
            # exhausted all years for this region
            break

    return results