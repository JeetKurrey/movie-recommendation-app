"""
Orchestrates the PRD's data flow for a single recommendation request:

  1. check cache (hash of query+filters, or movie title for /similar)
  2. on miss: build prompt -> call Gemini -> parse structured JSON
  3. hallucination guard: verify every title against OMDb search;
     drop anything that doesn't resolve to a real movie
  4. if too few survive, re-prompt once excluding titles already tried
  5. enrich survivors with poster/rating/year from the verified OMDb match
  6. cache the enriched result (TTL ~24h) and return it

If Gemini or OMDb are down/rate-limited, we fall back to the last known
good result for the same query if one exists (NFR: graceful fallback),
otherwise we raise so the router can return a friendly 503.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.core.config import Settings
from app.schemas.models import RecommendationOut
from app.services.cache import TTLCache, make_cache_key
from app.services.gemini_client import GeminiClient, GeminiError, GeminiNotConfigured, GeminiQuotaExceeded
from app.services.omdb_client import OMDbClient, OMDbError, OMDbNotConfigured

logger = logging.getLogger(__name__)


class RecommendationUnavailable(RuntimeError):
    """Raised when we truly have nothing to return (no cache, no live result).

    `reason` lets the router give an honest message instead of a blanket
    "high demand" for every failure mode:
      - "gemini_not_configured" / "omdb_not_configured": a real setup
        problem (missing/invalid key) — not something retrying fixes.
      - "gemini_quota": the free-tier quota is genuinely exhausted.
      - "unavailable": a transient/unknown failure — "try again shortly"
        is the accurate message here.
    """

    def __init__(self, message: str, reason: str = "unavailable"):
        super().__init__(message)
        self.reason = reason


class RecommendationService:
    def __init__(
        self,
        settings: Settings,
        gemini: GeminiClient,
        omdb: OMDbClient,
        fresh_cache: TTLCache,
        fallback_cache: TTLCache,
    ):
        self._settings = settings
        self._gemini = gemini
        self._omdb = omdb
        self._fresh_cache = fresh_cache
        self._fallback_cache = fallback_cache

    async def _verify_candidates(
        self, items: List[Dict[str, Any]], seen_ids: set
    ) -> List[RecommendationOut]:
        async def verify_one(item: Dict[str, Any]) -> Optional[RecommendationOut]:
            try:
                match = await self._omdb.search_and_verify(item["title"], item.get("year"))
            except OMDbNotConfigured:
                # A missing/invalid OMDb key isn't "this one title failed" — it means
                # verification can't work for ANY title. Let it propagate up rather
                # than silently dropping every candidate and looking like "nothing
                # matched" (that was a real bug: config errors and empty results
                # were indistinguishable to the caller).
                raise
            except OMDbError as exc:
                logger.warning("OMDb verification failed for %r: %s", item["title"], exc)
                return None
            if not match:
                logger.info("Dropping hallucinated/unverifiable title: %r", item["title"])
                return None
            imdb_id = match.get("imdbID")
            if not imdb_id or imdb_id in seen_ids:
                return None
            seen_ids.add(imdb_id)
            year_raw = (match.get("Year") or "")[:4]
            year = int(year_raw) if year_raw.isdigit() else item.get("year")
            poster = match.get("Poster")
            poster_url = poster if poster and poster.upper() != "N/A" else None
            return RecommendationOut(
                imdb_id=imdb_id,
                title=match.get("Title") or item["title"],
                year=year,
                rating=None,  # OMDb's search endpoint doesn't include imdbRating; detail call fills it in on demand
                reason=item["reason"],
                poster_url=poster_url,
            )

        results = await asyncio.gather(*(verify_one(i) for i in items))
        return [r for r in results if r is not None]

    async def _enrich_ratings(self, recs: List[RecommendationOut]) -> List[RecommendationOut]:
        """OMDb's search endpoint has no imdbRating, so we fetch it per-title
        (small, cached, and run concurrently) purely to populate the rating
        badge on the results grid."""

        async def fetch_rating(rec: RecommendationOut) -> RecommendationOut:
            try:
                detail = await self._omdb.get_movie_detail(rec.imdb_id)
                rec.rating = detail.get("rating")
            except OMDbError:
                pass
            return rec

        return list(await asyncio.gather(*(fetch_rating(r) for r in recs)))

    def _apply_soft_filters(
        self, recs: List[RecommendationOut], filters: Dict[str, Any]
    ) -> List[RecommendationOut]:
        """Belt-and-suspenders filtering in case Gemini didn't fully honor
        the constraints it was given — applied on data we already fetched,
        so it costs no extra API calls."""
        min_rating = filters.get("rating") or 0
        out = [r for r in recs if (r.rating or 0) >= min_rating]
        return out or recs  # never filter down to nothing over a soft constraint

    async def recommend(self, query: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        cache_key = make_cache_key("recommend", query, sorted(filters.items()))
        cached = await self._fresh_cache.get(cache_key)
        if cached is not None:
            return {"recommendations": cached, "partial": False}

        requested = self._settings.recommend_requested_count
        raw_items: Optional[List[Dict[str, Any]]] = None
        reason = "unavailable"
        try:
            raw_items = await self._gemini.recommend(query, filters, requested)
            seen_ids: set = set()
            verified = await self._verify_candidates(raw_items, seen_ids)

            if len(verified) < self._settings.min_valid_recommendations:
                tried_titles = {i["title"] for i in raw_items}
                logger.info(
                    "Only %s/%s recommendations verified, re-prompting once", len(verified), requested
                )
                retry_query = (
                    f"{query} (do not suggest any of these again: "
                    f"{', '.join(list(tried_titles)[:15])})"
                )
                more_items = await self._gemini.recommend(retry_query, filters, requested)
                more_verified = await self._verify_candidates(more_items, seen_ids)
                verified.extend(more_verified)

            verified = await self._enrich_ratings(verified)
            verified = self._apply_soft_filters(verified, filters)
            partial = len(verified) < self._settings.min_valid_recommendations

            if verified:
                await self._fresh_cache.set(
                    cache_key, verified, self._settings.recommend_cache_ttl_seconds
                )
                await self._fallback_cache.set(cache_key, verified, ttl_seconds=60 * 60 * 24 * 7)
                return {"recommendations": verified, "partial": partial}

        except GeminiNotConfigured as exc:
            logger.error("Gemini is not configured: %s", exc)
            reason = "gemini_not_configured"
        except GeminiQuotaExceeded as exc:
            logger.error("Gemini quota exceeded: %s", exc)
            reason = "gemini_quota"
        except OMDbNotConfigured as exc:
            logger.error("OMDb is not configured: %s", exc)
            reason = "omdb_not_configured"
            if raw_items and self._settings.allow_unverified_fallback:
                # Gemini gave us real suggestions but we have no way to verify
                # them right now. Rather than show nothing, show them clearly
                # marked as unverified — the user can still act on them, they
                # just don't get the "checked against a real database" guarantee.
                unverified = self._to_unverified(raw_items)
                if unverified:
                    return {"recommendations": unverified, "partial": True}
        except (GeminiError, OMDbError) as exc:
            logger.error("Live recommendation failed (%s); trying fallback cache", exc)

        fallback = await self._fallback_cache.get(cache_key)
        if fallback:
            return {"recommendations": fallback, "partial": True}

        raise RecommendationUnavailable(self._unavailable_message(reason), reason=reason)

    async def similar(self, movie_title: str) -> Dict[str, Any]:
        cache_key = make_cache_key("similar", movie_title)
        cached = await self._fresh_cache.get(cache_key)
        if cached is not None:
            return {"recommendations": cached, "partial": False}

        count = 5
        raw_items: Optional[List[Dict[str, Any]]] = None
        reason = "unavailable"
        try:
            raw_items = await self._gemini.similar(movie_title, count)
            seen_ids: set = set()
            verified = await self._verify_candidates(raw_items, seen_ids)
            verified = await self._enrich_ratings(verified)
            partial = len(verified) < min(3, count)

            if verified:
                await self._fresh_cache.set(
                    cache_key, verified, self._settings.recommend_cache_ttl_seconds
                )
                await self._fallback_cache.set(cache_key, verified, ttl_seconds=60 * 60 * 24 * 7)
                return {"recommendations": verified, "partial": partial}

        except GeminiNotConfigured as exc:
            logger.error("Gemini is not configured: %s", exc)
            reason = "gemini_not_configured"
        except GeminiQuotaExceeded as exc:
            logger.error("Gemini quota exceeded: %s", exc)
            reason = "gemini_quota"
        except OMDbNotConfigured as exc:
            logger.error("OMDb is not configured: %s", exc)
            reason = "omdb_not_configured"
            if raw_items and self._settings.allow_unverified_fallback:
                unverified = self._to_unverified(raw_items)
                if unverified:
                    return {"recommendations": unverified, "partial": True}
        except (GeminiError, OMDbError) as exc:
            logger.error("Live 'similar' lookup failed (%s); trying fallback cache", exc)

        fallback = await self._fallback_cache.get(cache_key)
        if fallback:
            return {"recommendations": fallback, "partial": True}

        raise RecommendationUnavailable(self._unavailable_message(reason), reason=reason)

    @staticmethod
    def _to_unverified(raw_items: List[Dict[str, Any]]) -> List[RecommendationOut]:
        """Turn Gemini's raw (unverified) suggestions into recommendations
        with no imdb_id/poster — used only when OMDb itself is unreachable,
        so the user gets *something* instead of a bare error. The frontend
        should visually flag these as unverified."""
        out = []
        for item in raw_items:
            title = (item.get("title") or "").strip()
            if not title:
                continue
            out.append(
                RecommendationOut(
                    imdb_id=f"unverified:{title.lower()}",
                    title=title,
                    year=item.get("year"),
                    rating=None,
                    reason=item.get("reason") or "Suggested by the AI (not yet verified against a movie database).",
                    poster_url=None,
                    verified=False,
                )
            )
        return out

    @staticmethod
    def _unavailable_message(reason: str) -> str:
        return {
            "gemini_not_configured": (
                "The recommendation engine isn't configured yet — GEMINI_API_KEY is missing "
                "or invalid on the server. This needs to be fixed in the backend's .env file, "
                "not by retrying."
            ),
            "gemini_quota": (
                "Gemini's free-tier quota is exhausted right now. Wait a minute (per-minute "
                "limit) or try again tomorrow (daily limit), or check usage at "
                "https://ai.dev/rate-limit."
            ),
            "omdb_not_configured": (
                "Movie verification is unavailable — OMDB_API_KEY is missing or invalid on "
                "the server. If you just created an OMDb key, check your email for the "
                "activation link."
            ),
        }.get(
            reason,
            "High demand right now, or a temporary hiccup reaching Gemini/OMDb — please try again shortly.",
        )
