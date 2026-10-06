"""
Thin TMDB REST client.

Scope: just what we need for Phase A (poster + backdrop paths by tmdb_id).
Extensible: `details()` already fetches the full record so a Phase B script can
pull credits, keywords, videos, etc. from the same shape.

TMDB rate limit: ~40 req/sec. We pace to ~25/sec to stay comfortable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.config import TMDB_API_KEY, TMDB_BASE_URL, TMDB_IMAGE_BASE


MIN_INTERVAL = 1.0 / 25.0  # seconds between calls → ~25 req/sec


class TmdbError(Exception):
    pass


@dataclass
class TmdbImages:
    poster_path: str | None     # like "/abc.jpg"; join with TMDB_IMAGE_BASE/<size> to fetch
    backdrop_path: str | None


class TmdbClient:
    """One instance per sync run. Reuses a single httpx.Client."""

    def __init__(self):
        self._client = httpx.Client(timeout=20.0)
        self._last_call = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._client.close()

    # ------------------------------------------------------------------
    def _throttle(self) -> None:
        now = time.time()
        gap = now - self._last_call
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        self._last_call = time.time()

    def _get(self, path: str, params: dict | None = None) -> dict:
        self._throttle()
        full_params = {"api_key": TMDB_API_KEY, **(params or {})}
        for attempt in range(3):
            try:
                r = self._client.get(f"{TMDB_BASE_URL}{path}", params=full_params)
            except httpx.HTTPError as e:
                if attempt == 2:
                    raise TmdbError(f"network error on {path}: {e}") from e
                time.sleep(1.0 * (attempt + 1))
                continue
            if r.status_code == 429:
                # Should be rare given our throttle, but honor Retry-After if it fires
                retry_after = int(r.headers.get("Retry-After", "2"))
                time.sleep(retry_after)
                continue
            if r.status_code == 404:
                return {}
            if r.status_code >= 400:
                raise TmdbError(f"{r.status_code} on {path}: {r.text[:300]}")
            return r.json()
        raise TmdbError(f"exhausted retries on {path}")

    # ------------------------------------------------------------------
    def details(self, tmdb_id: int, content_type: str,
                append: str = "") -> dict:
        """
        Full TMDB record for a title. content_type ∈ {"movie", "series"}.
        Series map to TMDB's /tv/{id} endpoint.

        `append` uses TMDB's `append_to_response` to pull multiple sub-resources
        in a single request — pass "credits,keywords,external_ids" to get
        everything we need for Phase B in one call.
        """
        endpoint = "/movie" if content_type == "movie" else "/tv"
        params = {"append_to_response": append} if append else None
        return self._get(f"{endpoint}/{tmdb_id}", params=params)

    def images(self, tmdb_id: int, content_type: str) -> TmdbImages:
        """Just the image paths (poster + backdrop) for this title."""
        rec = self.details(tmdb_id, content_type)
        return TmdbImages(
            poster_path=rec.get("poster_path"),
            backdrop_path=rec.get("backdrop_path"),
        )

    def credits(self, tmdb_id: int, content_type: str) -> dict:
        """Cast + crew for a title.
        Returns {'cast': [...], 'crew': [...]} shape.
        TV endpoint is /tv/{id}/credits (aggregate across all seasons)."""
        endpoint = "/movie" if content_type == "movie" else "/tv"
        return self._get(f"{endpoint}/{tmdb_id}/credits")

    def keywords(self, tmdb_id: int, content_type: str) -> list[str]:
        """Fine-grained content tags (space, time-travel, found-footage, etc.).
        Movie and TV endpoints wrap the array differently."""
        endpoint = "/movie" if content_type == "movie" else "/tv"
        rec = self._get(f"{endpoint}/{tmdb_id}/keywords")
        kw_list = rec.get("keywords") or rec.get("results") or []
        return [k.get("name") for k in kw_list if k.get("name")]

    # ------------------------------------------------------------------
    def download_image(self, path: str, size: str) -> bytes:
        """Download a TMDB-hosted image. `path` is "/abc.jpg" from details()."""
        self._throttle()
        url = f"{TMDB_IMAGE_BASE}/{size}{path}"
        r = self._client.get(url)
        if r.status_code != 200:
            raise TmdbError(f"image download {url} → {r.status_code}")
        return r.content
