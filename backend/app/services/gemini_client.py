"""
Thin async wrapper around the Gemini API (Google AI Studio), used exactly
as described in the PRD: the backend builds the prompt, asks for strict
JSON output via `response_mime_type`, and the raw text titles get verified
against OMDb downstream (the hallucination guard lives in recommend_service.py,
not here — this module's only job is "ask Gemini, get clean JSON back").
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = (
    "You are a movie recommendation assistant embedded in an app called "
    "'Popcorn Cue'. You recommend real, existing, released movies only — "
    "never invent titles, and never invent sequels or alternate cuts that "
    "don't exist. Respond with ONLY valid JSON, no markdown fences, no "
    "commentary, matching exactly this schema: "
    '[{"title": string, "year": integer, "reason": string}]. '
    "Each 'reason' is a single specific sentence (max ~25 words) explaining "
    "why that film fits the request. Prefer a diverse set of countries of "
    "origin unless the user's request or filters imply otherwise."
)


class GeminiError(RuntimeError):
    """Raised when Gemini can't be reached or returns something unusable."""


class GeminiNotConfigured(GeminiError):
    """Raised when GEMINI_API_KEY is missing entirely — a config problem,
    not a "try again later" problem, so callers should say so plainly."""


class GeminiQuotaExceeded(GeminiError):
    """Raised when Gemini's free-tier quota (per-minute or per-day) is
    genuinely exhausted. Distinguished from other transient 429/5xx errors
    because retrying with backoff won't help — the right move is to stop
    burning the retry budget and tell the user honestly what happened."""


class GeminiClient:
    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None):
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.gemini_timeout_seconds)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.gemini_api_key)

    async def _call(self, user_prompt: str) -> str:
        if not self.is_configured:
            raise GeminiNotConfigured(
                "GEMINI_API_KEY is not set — get a free key at https://aistudio.google.com/apikey"
            )

        url = f"{self._settings.gemini_base_url}/models/{self._settings.gemini_model}:generateContent"
        params = {"key": self._settings.gemini_api_key}
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.7,
                "responseMimeType": "application/json",
            },
        }

        max_retries = self._settings.external_call_max_retries
        backoff = 1.0
        last_error: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                resp = await self._client.post(url, params=params, json=payload)
            except httpx.RequestError as exc:
                last_error = exc
                logger.warning("Gemini request error (attempt %s/%s): %s", attempt, max_retries, exc)
            else:
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 429:
                    # Gemini's free tier returns 429 both for "you're bursting too fast,
                    # try again in a second" AND "your quota is fully exhausted for the
                    # day" — the response body is the only way to tell them apart.
                    # Retrying the second case with growing backoff just adds latency
                    # for no benefit, so we fail fast on it instead of spending the
                    # full retry budget.
                    body_lower = resp.text.lower()
                    if "quota" in body_lower or "billing" in body_lower:
                        logger.warning("Gemini quota exhausted \u2014 not retrying: %s", resp.text[:300])
                        raise GeminiQuotaExceeded(
                            "Gemini's free-tier quota is exhausted right now (per-minute or "
                            "daily limit). Wait a minute and try again, or check your usage at "
                            f"https://ai.dev/rate-limit. Raw response: {resp.text[:300]}"
                        )
                    last_error = GeminiError(f"Gemini returned 429: {resp.text[:300]}")
                    logger.warning("Gemini rate-limited (attempt %s/%s)", attempt, max_retries)
                elif resp.status_code in (500, 502, 503, 504):
                    last_error = GeminiError(f"Gemini returned {resp.status_code}: {resp.text[:300]}")
                    logger.warning(
                        "Gemini transient error %s (attempt %s/%s)", resp.status_code, attempt, max_retries
                    )
                elif resp.status_code in (401, 403):
                    raise GeminiNotConfigured(
                        f"Gemini rejected the API key (HTTP {resp.status_code}) — it's likely "
                        f"invalid, expired, or not yet activated: {resp.text[:300]}"
                    )
                else:
                    # Non-retryable (bad request, etc.)
                    raise GeminiError(f"Gemini returned {resp.status_code}: {resp.text[:300]}")

            if attempt < max_retries:
                await asyncio.sleep(backoff)
                backoff *= 2

        raise GeminiError(f"Gemini unavailable after {max_retries} attempts: {last_error}")

    @staticmethod
    def _extract_text(raw_response: str) -> str:
        data = json.loads(raw_response)
        try:
            candidate = data["candidates"][0]
            parts = candidate["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise GeminiError(f"Unexpected Gemini response shape: {raw_response[:300]}") from exc

    @staticmethod
    def _parse_movie_list(text: str) -> List[Dict[str, Any]]:
        text = text.strip()
        # Defensive: strip stray markdown fences even though we asked for pure JSON.
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GeminiError(f"Gemini did not return valid JSON: {text[:300]}") from exc

        if not isinstance(parsed, list):
            raise GeminiError("Gemini JSON was not a list")

        cleaned = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            year = item.get("year")
            try:
                year = int(year) if year is not None else None
            except (TypeError, ValueError):
                year = None
            reason = str(item.get("reason", "")).strip() or "A strong match for your request."
            cleaned.append({"title": title, "year": year, "reason": reason})
        return cleaned

    async def recommend(self, query: str, filters: Dict[str, Any], count: int) -> List[Dict[str, Any]]:
        constraint_bits = []
        if filters.get("genre"):
            constraint_bits.append(f"genre={filters['genre']}")
        if filters.get("decade"):
            constraint_bits.append(f"decade={filters['decade']}")
        if filters.get("country"):
            constraint_bits.append(f"country={filters['country']}")
        if filters.get("runtime") and int(filters["runtime"]) < 240:
            constraint_bits.append(f"max_runtime={filters['runtime']} minutes")
        if filters.get("rating"):
            constraint_bits.append(f"min_rating={filters['rating']}")
        constraints = ", ".join(constraint_bits) or "none"

        user_prompt = (
            f"Recommend {count} movies for this request: \"{query or 'anything great to watch'}\".\n"
            f"Constraints: {constraints}.\n"
            "Return a diverse, non-obvious mix where possible, not just the most famous titles."
        )
        raw = await self._call(user_prompt)
        text = self._extract_text(raw)
        return self._parse_movie_list(text)

    async def similar(self, movie_title: str, count: int) -> List[Dict[str, Any]]:
        user_prompt = (
            f"Recommend {count} movies that are genuinely similar in tone, theme, or "
            f'style to "{movie_title}". Do not include "{movie_title}" itself in the list.'
        )
        raw = await self._call(user_prompt)
        text = self._extract_text(raw)
        return self._parse_movie_list(text)
