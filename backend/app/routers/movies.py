import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_omdb_client, get_omdb_detail_cache
from app.schemas.models import MovieDetail
from app.services.cache import TTLCache, make_cache_key
from app.services.omdb_client import OMDbClient, OMDbError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["movies"])


@router.get("/movie/{imdb_id}", response_model=MovieDetail)
async def get_movie(
    imdb_id: str,
    omdb: OMDbClient = Depends(get_omdb_client),
    cache: TTLCache = Depends(get_omdb_detail_cache),
) -> MovieDetail:
    key = make_cache_key("movie", imdb_id)
    cached = await cache.get(key)
    if cached is not None:
        return MovieDetail(**cached)

    try:
        detail = await omdb.get_movie_detail(imdb_id)
    except OMDbError as exc:
        logger.error("Failed to fetch OMDb detail for id %s: %s", imdb_id, exc)
        raise HTTPException(status_code=502, detail="Could not fetch movie details right now.") from exc

    await cache.set(key, detail, ttl_seconds=60 * 60 * 24 * 7)
    return MovieDetail(**detail)
