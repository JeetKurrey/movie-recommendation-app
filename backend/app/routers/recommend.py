import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_recommend_service
from app.schemas.models import RecommendationsResponse, RecommendRequest, SimilarRequest
from app.services.recommend_service import RecommendationService, RecommendationUnavailable

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["recommendations"])


@router.post("/recommend", response_model=RecommendationsResponse)
async def recommend(
    body: RecommendRequest,
    service: RecommendationService = Depends(get_recommend_service),
) -> RecommendationsResponse:
    try:
        result = await service.recommend(body.query, body.filters.model_dump())
    except RecommendationUnavailable as exc:
        logger.error("recommend() exhausted all options: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="High demand right now — please try again shortly.",
        ) from exc
    return RecommendationsResponse(**result)


@router.post("/similar", response_model=RecommendationsResponse)
async def similar(
    body: SimilarRequest,
    service: RecommendationService = Depends(get_recommend_service),
) -> RecommendationsResponse:
    try:
        result = await service.similar(body.movie_title)
    except RecommendationUnavailable as exc:
        logger.error("similar() exhausted all options: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="High demand right now — please try again shortly.",
        ) from exc
    return RecommendationsResponse(**result)
