"""
Centralized app configuration.

Everything that varies between environments (API keys, model names,
rate-limit knobs, DB location) lives here and is loaded once from
environment variables / a .env file. Nothing else in the codebase
should call os.environ directly.
"""
from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_name: str = "Popcorn Cue API"
    environment: str = Field(default="development")
    # The frontend now lives on its own origin (e.g. a static file server on
    # a different port, or a separate Vercel/Netlify deployment), so CORS
    # must be configured explicitly rather than relying on same-origin.
    cors_origins: List[str] = Field(
        default_factory=lambda: [
            "http://localhost:5500",
            "http://127.0.0.1:5500",
            "http://localhost:3000",
        ]
    )

    # --- Gemini (Google AI Studio) ---
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.0-flash", validation_alias="GEMINI_MODEL")
    gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        validation_alias="GEMINI_BASE_URL",
    )
    gemini_timeout_seconds: float = Field(default=20.0, validation_alias="GEMINI_TIMEOUT_SECONDS")

    # --- OMDb (omdbapi.com) ---
    omdb_api_key: str = Field(default="", validation_alias="OMDB_API_KEY")
    omdb_base_url: str = Field(default="https://www.omdbapi.com/", validation_alias="OMDB_BASE_URL")
    omdb_timeout_seconds: float = Field(default=10.0, validation_alias="OMDB_TIMEOUT_SECONDS")

    # --- Caching / rate-limit behavior ---
    recommend_cache_ttl_seconds: int = Field(default=60 * 60 * 24, validation_alias="RECOMMEND_CACHE_TTL")
    omdb_cache_ttl_seconds: int = Field(default=60 * 60 * 24 * 7, validation_alias="OMDB_CACHE_TTL")
    cache_max_entries: int = Field(default=2000, validation_alias="CACHE_MAX_ENTRIES")
    external_call_max_retries: int = Field(default=3, validation_alias="EXTERNAL_MAX_RETRIES")

    # --- Database ---
    database_url: str = Field(default="sqlite:///./now_showing.db", validation_alias="DATABASE_URL")

    # --- Recommendation engine ---
    min_valid_recommendations: int = Field(default=4, validation_alias="MIN_VALID_RECOMMENDATIONS")
    recommend_requested_count: int = Field(default=8, validation_alias="RECOMMEND_REQUESTED_COUNT")


@lru_cache
def get_settings() -> Settings:
    return Settings()
