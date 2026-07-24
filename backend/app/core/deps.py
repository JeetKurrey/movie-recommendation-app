"""
Dependency-injection glue. Long-lived objects (HTTP clients, caches, the
recommendation service, the DB session factory) are created once in
main.py's lifespan and stashed on `app.state`; these functions just hand
them to route handlers via FastAPI's Depends().
"""
from typing import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from app.services.cache import TTLCache
from app.services.gemini_client import GeminiClient
from app.services.omdb_client import OMDbClient
from app.services.recommend_service import RecommendationService


def get_gemini_client(request: Request) -> GeminiClient:
    return request.app.state.gemini_client


def get_omdb_client(request: Request) -> OMDbClient:
    return request.app.state.omdb_client


def get_omdb_detail_cache(request: Request) -> TTLCache:
    return request.app.state.omdb_detail_cache


def get_recommend_service(request: Request) -> RecommendationService:
    return request.app.state.recommend_service


def get_db(request: Request) -> Generator[Session, None, None]:
    session_factory = request.app.state.db_session_factory
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
