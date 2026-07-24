"""
Thin async wrapper around OMDb (omdbapi.com) — the real-metadata source
used in place of TMDB. Handles:
  - title -> verified imdb_id search (the hallucination guard's other half)
  - full movie detail enrichment (poster, plot, cast, runtime, genre, ratings)

OMDb quirks this client works around:
  - Every response is HTTP 200, even "not found" — errors show up as
    {"Response": "False", "Error": "..."} in the body, not as a status code.
  - Search (`s=`) is paginated, ~10 results per page, and only returns a
    *summary* record (title/year/poster/imdbID) — full detail (plot, cast,
    ratings, runtime) requires a second call by imdbID (`i=`).
  - Missing fields come back as the literal string "N/A" instead of being
    omitted, so every field needs an "N/A" -> None normalization pass.
  - There's no first-class "watch providers" endpoint like TMDB's, so we
    don't attempt to resolve streaming availability from OMDb; the frontend
    instead offers an outbound "search on JustWatch" link per PRD's spirit
    of "recommend and link out, don't host content".
"""
import asyncio
import difflib
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class OMDbError(RuntimeError):
    pass


def _clean(value: Any) -> Optional[Any]:
    """OMDb's placeholder for "no data" is the literal string 'N/A'."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().upper() in ("N/A", ""):
        return None
    return value


def _parse_runtime(raw: Optional[str]) -> Optional[int]:
    raw = _clean(raw)
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else None


def _parse_float(raw: Optional[str]) -> Optional[float]:
    raw = _clean(raw)
    if not raw:
        return None
    try:
        return round(float(raw), 1)
    except ValueError:
        return None


def _parse_year(raw: Optional[str]) -> Optional[int]:
    raw = _clean(raw)
    if not raw:
        return None
    digits = "".join(ch for ch in raw[:4] if ch.isdigit())
    return int(digits) if len(digits) == 4 else None


def _parse_list(raw: Optional[str]) -> List[str]:
    raw = _clean(raw)
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


class OMDbClient:
    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None):
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.omdb_base_url, timeout=settings.omdb_timeout_seconds
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.omdb_api_key)

    async def _get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_configured:
            raise OMDbError("OMDB_API_KEY is not set")

        all_params = {"apikey": self._settings.omdb_api_key, **params}
        max_retries = self._settings.external_call_max_retries
        backoff = 1.0
        last_error: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                resp = await self._client.get("", params=all_params)
            except httpx.RequestError as exc:
                last_error = exc
                logger.warning("OMDb request error (attempt %s/%s): %s", attempt, max_retries, exc)
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    # OMDb signals "not found" / bad key / rate-limit inside a 200 body.
                    if data.get("Response") == "False":
                        error = (data.get("Error") or "").lower()
                        if "request limit" in error or "limit reached" in error:
                            last_error = OMDbError(f"OMDb rate limit: {data.get('Error')}")
                            logger.warning(
                                "OMDb rate limit hit (attempt %s/%s): %s", attempt, max_retries, data.get("Error")
                            )
                        else:
                            # Genuine "not found" — not transient, nothing to retry.
                            return {}
                    else:
                        return data
                elif resp.status_code in (429, 500, 502, 503, 504):
                    last_error = OMDbError(f"OMDb returned {resp.status_code}")
                    logger.warning("OMDb transient error %s (attempt %s/%s)", resp.status_code, attempt, max_retries)
                else:
                    raise OMDbError(f"OMDb returned {resp.status_code}: {resp.text[:300]}")

            if attempt < max_retries:
                await asyncio.sleep(backoff)
                backoff *= 2

        raise OMDbError(f"OMDb unavailable after {max_retries} attempts: {last_error}")

    @staticmethod
    def _title_matches(query_title: str, candidate_title: str, threshold: float = 0.72) -> bool:
        a = query_title.strip().lower()
        b = candidate_title.strip().lower()
        if a == b:
            return True
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        return ratio >= threshold

    async def search_and_verify(self, title: str, year: Optional[int]) -> Optional[Dict[str, Any]]:
        """
        The hallucination guard's OMDb half: search for `title` (optionally
        scoped to `year`), and only return a result if a candidate's title
        actually fuzzy-matches what Gemini claimed. Returns None if nothing
        real enough is found — the caller drops the recommendation.
        """
        params: Dict[str, Any] = {"s": title, "type": "movie"}
        if year:
            params["y"] = year

        data = await self._get(params)
        results = data.get("Search") or []

        if not results and year:
            # Retry without the year constraint — Gemini's year can be off by one,
            # and OMDb's year matching on `s=` is stricter than TMDB's.
            data = await self._get({"s": title, "type": "movie"})
            results = data.get("Search") or []

        for candidate in results[:5]:
            candidate_title = candidate.get("Title") or ""
            if self._title_matches(title, candidate_title):
                return candidate

        return None

    async def get_movie_detail(self, imdb_id: str) -> Dict[str, Any]:
        data = await self._get({"i": imdb_id, "plot": "full"})
        if not data:
            raise OMDbError(f"No OMDb movie found for id {imdb_id}")

        genres = _parse_list(data.get("Genre"))
        ratings_raw = data.get("Ratings") or []
        extra_ratings = {
            r.get("Source"): r.get("Value") for r in ratings_raw if r.get("Source") and r.get("Value")
        }

        poster = _clean(data.get("Poster"))

        return {
            "imdb_id": data.get("imdbID", imdb_id),
            "title": _clean(data.get("Title")),
            "year": _parse_year(data.get("Year")),
            "genre": genres[0] if genres else None,
            "genres": genres,
            "country": _clean(data.get("Country")),
            "runtime": _parse_runtime(data.get("Runtime")),
            "rating": _parse_float(data.get("imdbRating")),
            "synopsis": _clean(data.get("Plot")),
            "cast": _clean(data.get("Actors")),
            "director": _clean(data.get("Director")),
            "rated": _clean(data.get("Rated")),
            "extra_ratings": extra_ratings,  # e.g. {"Rotten Tomatoes": "94%", "Metacritic": "82/100"}
            "poster_url": poster,
        }
