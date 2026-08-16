import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_recommend_service
from app.schemas.models import RecommendationsResponse, RecommendRequest, SimilarRequest
from app.services.recommend_service import RecommendationService, RecommendationUnavailable

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["recommendations"])

# Config problems (missing/invalid key) are a server setup issue, not
# something a retry fixes, so they get a distinct status from genuine
# transient unavailability -- makes it obvious in the browser network tab
# and in the error message which situation you're actually in.
_STATUS_BY_REASON = {
    "gemini_not_configured": 500,
    "omdb_not_configured": 500,
    "gemini_quota": 503,
    "unavailable": 503,
}


def _raise_from(exc: RecommendationUnavailable) -> None:
    status_code = _STATUS_BY_REASON.get(exc.reason, 503)
    logger.error("Recommendation request failed (reason=%s): %s", exc.reason, exc)
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/recommend", response_model=RecommendationsResponse)
async def recommend(
    body: RecommendRequest,
    service: RecommendationService = Depends(get_recommend_service),
) -> RecommendationsResponse:
    try:
        result = await service.recommend(body.query, body.filters.model_dump())
    except RecommendationUnavailable as exc:
        _raise_from(exc)
    return RecommendationsResponse(**result)


@router.post("/similar", response_model=RecommendationsResponse)
async def similar(
    body: SimilarRequest,
    service: RecommendationService = Depends(get_recommend_service),
) -> RecommendationsResponse:
    try:
        result = await service.similar(body.movie_title)
    except RecommendationUnavailable as exc:
        _raise_from(exc)
    return RecommendationsResponse(**result)
