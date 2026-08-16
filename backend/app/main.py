"""
FastAPI entrypoint. Run with:

    uvicorn app.main:app --reload

This backend is API-only — the frontend lives in ../frontend as its own
static site with its own dev server/deployment, so CORS is configured
explicitly (see app.core.config.Settings.cors_origins / the CORS_ORIGINS
env var) instead of relying on same-origin serving.

See README.md for setup (env vars, install, run).
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.models.db import Base, build_engine, build_session_factory
from app.routers import movies, recommend, watchlist
from app.schemas.models import HealthResponse
from app.services.cache import TTLCache
from app.services.gemini_client import GeminiClient
from app.services.omdb_client import OMDbClient
from app.services.recommend_service import RecommendationService

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY is not set — /api/recommend and /api/similar will fail.")
    if not settings.omdb_api_key:
        logger.warning("OMDB_API_KEY is not set — metadata endpoints will fail.")

    gemini_client = GeminiClient(settings)
    omdb_client = OMDbClient(settings)
    fresh_cache = TTLCache(max_entries=settings.cache_max_entries)
    fallback_cache = TTLCache(max_entries=settings.cache_max_entries)
    omdb_detail_cache = TTLCache(max_entries=settings.cache_max_entries)
    recommend_service = RecommendationService(
        settings=settings,
        gemini=gemini_client,
        omdb=omdb_client,
        fresh_cache=fresh_cache,
        fallback_cache=fallback_cache,
    )

    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    session_factory = build_session_factory(engine)

    app.state.settings = settings
    app.state.gemini_client = gemini_client
    app.state.omdb_client = omdb_client
    app.state.omdb_detail_cache = omdb_detail_cache
    app.state.recommend_service = recommend_service
    app.state.db_session_factory = session_factory

    logger.info("%s starting up (env=%s)", settings.app_name, settings.environment)
    yield

    await gemini_client.aclose()
    await omdb_client.aclose()
    engine.dispose()
    logger.info("%s shut down cleanly", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # No cookies/auth headers are used (session_id travels explicitly in
        # the request body/path), so credentials stay off — this also lets
        # allow_origins=["*"] work, which browsers reject when paired with
        # allow_credentials=True.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(recommend.router)
    app.include_router(movies.router)
    app.include_router(watchlist.router)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        s = get_settings()
        return HealthResponse(
            status="ok",
            gemini_configured=bool(s.gemini_api_key),
            omdb_configured=bool(s.omdb_api_key),
        )

    @app.get("/")
    async def root() -> dict:
        return {
            "service": settings.app_name,
            "docs": "/docs",
            "frontend": "served separately — see ../frontend",
        }

    return app


app = create_app()
