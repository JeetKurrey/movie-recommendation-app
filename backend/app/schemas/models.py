"""
Pydantic schemas for every request/response body.

  POST /api/recommend  { query, filters }              -> { recommendations: [...] }
  POST /api/similar    { movie_title }                  -> { recommendations: [...] }
  GET  /api/movie/{imdb_id}                             -> MovieDetail
  POST /api/watchlist  { session_id, imdb_id }           -> WatchlistItemOut
  GET  /api/watchlist/{session_id}                       -> { movies: [...] }
"""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class RecommendFilters(BaseModel):
    """Matches the frontend's filter state exactly."""

    genre: Optional[str] = ""
    decade: Optional[str] = ""
    country: Optional[str] = ""
    runtime: Optional[int] = 240  # "no limit" sentinel used by the UI slider
    rating: Optional[float] = 0

    @field_validator("genre", "decade", "country", mode="before")
    @classmethod
    def blank_to_none_str(cls, v):
        return v or ""


class RecommendRequest(BaseModel):
    query: str = Field(default="", max_length=500)
    filters: RecommendFilters = Field(default_factory=RecommendFilters)


class SimilarRequest(BaseModel):
    movie_title: str = Field(..., min_length=1, max_length=300)


class RecommendationOut(BaseModel):
    """One card in the results grid."""

    imdb_id: str
    title: str
    year: Optional[int] = None
    rating: Optional[float] = None
    reason: str
    poster_url: Optional[str] = None


class RecommendationsResponse(BaseModel):
    recommendations: List[RecommendationOut]
    partial: bool = False  # true if we returned fewer than requested after validation


class MovieDetail(BaseModel):
    imdb_id: str
    title: str
    year: Optional[int] = None
    genre: Optional[str] = None
    genres: List[str] = Field(default_factory=list)
    country: Optional[str] = None
    runtime: Optional[int] = None
    rating: Optional[float] = None
    synopsis: Optional[str] = None
    cast: Optional[str] = None
    director: Optional[str] = None
    rated: Optional[str] = None
    extra_ratings: Dict[str, str] = Field(default_factory=dict)
    poster_url: Optional[str] = None


class WatchlistAddRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    imdb_id: str = Field(..., min_length=1, max_length=20)


class WatchlistItemOut(BaseModel):
    imdb_id: str
    title: str
    year: Optional[int] = None
    rating: Optional[float] = None
    poster_url: Optional[str] = None


class WatchlistResponse(BaseModel):
    movies: List[WatchlistItemOut]


class HealthResponse(BaseModel):
    status: str = "ok"
    gemini_configured: bool
    omdb_configured: bool
