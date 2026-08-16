import json

import pytest
import respx
from httpx import Response

GEMINI_URL_PREFIX = "https://generativelanguage.googleapis.com/v1beta/models/"
OMDB_URL = "https://www.omdbapi.com/"


def gemini_json_response(movies):
    """Build a fake Gemini API envelope wrapping the given movie list as text."""
    return {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps(movies)}]}}
        ]
    }


@pytest.mark.asyncio
async def test_health(app_and_client):
    _, client = app_and_client
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["gemini_configured"] is True
    assert body["omdb_configured"] is True


@pytest.mark.asyncio
async def test_recommend_drops_hallucinated_titles(app_and_client):
    """Gemini returns a real movie and a made-up one; only the OMDb-verified
    one should survive (the core hallucination-guard behavior)."""
    _, client = app_and_client

    gemini_payload = gemini_json_response(
        [
            {"title": "Inception", "year": 2010, "reason": "Mind-bending heist."},
            {"title": "Zzyx Nonexistent Movie 9000", "year": 2010, "reason": "Fabricated."},
        ]
    )

    def omdb_side_effect(request):
        params = dict(request.url.params)
        if params.get("s") == "Inception":
            return Response(
                200,
                json={
                    "Search": [
                        {
                            "Title": "Inception",
                            "Year": "2010",
                            "imdbID": "tt1375666",
                            "Poster": "https://example.com/inception.jpg",
                        }
                    ],
                    "Response": "True",
                },
            )
        if params.get("i") == "tt1375666":
            return Response(
                200,
                json={
                    "imdbID": "tt1375666",
                    "Title": "Inception",
                    "Year": "2010",
                    "Runtime": "148 min",
                    "Genre": "Action, Sci-Fi",
                    "Country": "USA, UK",
                    "Plot": "A thief who steals corporate secrets through dream-sharing.",
                    "Actors": "Leonardo DiCaprio",
                    "imdbRating": "8.8",
                    "Poster": "https://example.com/inception.jpg",
                    "Response": "True",
                },
            )
        # Anything else (the hallucinated title) -> not found.
        return Response(200, json={"Response": "False", "Error": "Movie not found!"})

    with respx.mock(assert_all_called=False) as mock:
        mock.post(url__regex=rf"{GEMINI_URL_PREFIX}.*generateContent").mock(
            return_value=Response(200, json=gemini_payload)
        )
        mock.get(OMDB_URL).mock(side_effect=omdb_side_effect)

        resp = await client.post("/api/recommend", json={"query": "heist movies", "filters": {}})

    assert resp.status_code == 200
    body = resp.json()
    titles = [r["title"] for r in body["recommendations"]]
    assert "Inception" in titles
    assert "Zzyx Nonexistent Movie 9000" not in titles
    inception = next(r for r in body["recommendations"] if r["title"] == "Inception")
    assert inception["poster_url"] == "https://example.com/inception.jpg"
    assert inception["rating"] == 8.8


@pytest.mark.asyncio
async def test_recommend_uses_cache_on_second_call(app_and_client):
    _, client = app_and_client
    gemini_payload = gemini_json_response(
        [{"title": "Parasite", "year": 2019, "reason": "Class satire thriller."}]
    )
    call_count = {"gemini": 0}

    def gemini_side_effect(request):
        call_count["gemini"] += 1
        return Response(200, json=gemini_payload)

    def omdb_side_effect(request):
        params = dict(request.url.params)
        if params.get("s") == "Parasite":
            return Response(
                200,
                json={
                    "Search": [
                        {
                            "Title": "Parasite",
                            "Year": "2019",
                            "imdbID": "tt6751668",
                            "Poster": "https://example.com/parasite.jpg",
                        }
                    ],
                    "Response": "True",
                },
            )
        return Response(
            200,
            json={
                "imdbID": "tt6751668",
                "Title": "Parasite",
                "Year": "2019",
                "imdbRating": "8.5",
                "Poster": "https://example.com/parasite.jpg",
                "Response": "True",
            },
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.post(url__regex=rf"{GEMINI_URL_PREFIX}.*generateContent").mock(side_effect=gemini_side_effect)
        mock.get(OMDB_URL).mock(side_effect=omdb_side_effect)

        r1 = await client.post("/api/recommend", json={"query": "class satire", "filters": {}})
        r2 = await client.post("/api/recommend", json={"query": "class satire", "filters": {}})

    assert r1.status_code == 200 and r2.status_code == 200
    assert call_count["gemini"] == 1  # second call served from cache


@pytest.mark.asyncio
async def test_watchlist_add_list_delete(app_and_client):
    _, client = app_and_client

    with respx.mock(assert_all_called=False) as mock:
        mock.get(OMDB_URL, params={"i": "tt0133093"}).mock(
            return_value=Response(
                200,
                json={
                    "imdbID": "tt0133093",
                    "Title": "The Matrix",
                    "Year": "1999",
                    "Runtime": "136 min",
                    "Genre": "Action, Sci-Fi",
                    "Plot": "A hacker discovers reality is a simulation.",
                    "Actors": "Keanu Reeves",
                    "imdbRating": "8.7",
                    "Poster": "https://example.com/matrix.jpg",
                    "Response": "True",
                },
            )
        )

        add_resp = await client.post("/api/watchlist", json={"session_id": "abc123", "imdb_id": "tt0133093"})
        assert add_resp.status_code == 201
        assert add_resp.json()["title"] == "The Matrix"

        list_resp = await client.get("/api/watchlist/abc123")
        assert list_resp.status_code == 200
        assert len(list_resp.json()["movies"]) == 1

        del_resp = await client.delete("/api/watchlist/abc123/tt0133093")
        assert del_resp.status_code == 204

        list_resp2 = await client.get("/api/watchlist/abc123")
        assert list_resp2.json()["movies"] == []


@pytest.mark.asyncio
async def test_movie_detail_endpoint(app_and_client):
    _, client = app_and_client

    with respx.mock(assert_all_called=False) as mock:
        mock.get(OMDB_URL, params={"i": "tt1375666"}).mock(
            return_value=Response(
                200,
                json={
                    "imdbID": "tt1375666",
                    "Title": "Inception",
                    "Year": "2010",
                    "Runtime": "148 min",
                    "Genre": "Action, Sci-Fi",
                    "Country": "USA, UK",
                    "Plot": "A thief who steals corporate secrets through dream-sharing.",
                    "Actors": "Leonardo DiCaprio",
                    "imdbRating": "8.8",
                    "Ratings": [{"Source": "Rotten Tomatoes", "Value": "87%"}],
                    "Poster": "https://example.com/inception.jpg",
                    "Response": "True",
                },
            )
        )

        resp = await client.get("/api/movie/tt1375666")

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Inception"
    assert body["poster_url"] == "https://example.com/inception.jpg"
    assert body["extra_ratings"] == {"Rotten Tomatoes": "87%"}


@pytest.mark.asyncio
async def test_recommend_reports_invalid_omdb_key_honestly_instead_of_high_demand(app_and_client):
    """Regression test for the bug this fix addresses: an invalid/unconfigured
    OMDb key used to make every candidate silently fail verification, so the
    caller saw an empty list and a generic "high demand" 503 — indistinguishable
    from real traffic-based unavailability. It should now surface as a clear
    500 naming the actual problem, and (with ALLOW_UNVERIFIED_FALLBACK on,
    the default) still return Gemini's raw suggestions, clearly unverified."""
    _, client = app_and_client

    gemini_payload = gemini_json_response(
        [{"title": "Inception", "year": 2010, "reason": "Mind-bending heist."}]
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.post(url__regex=rf"{GEMINI_URL_PREFIX}.*generateContent").mock(
            return_value=Response(200, json=gemini_payload)
        )
        mock.get(OMDB_URL).mock(
            return_value=Response(200, json={"Response": "False", "Error": "Invalid API key!"})
        )

        resp = await client.post("/api/recommend", json={"query": "heist movies", "filters": {}})

    # Unverified fallback is on by default, so this should succeed with a
    # clearly-flagged, unverified result rather than a bare error.
    assert resp.status_code == 200
    body = resp.json()
    assert body["partial"] is True
    assert body["recommendations"][0]["title"] == "Inception"
    assert body["recommendations"][0]["verified"] is False
    assert body["recommendations"][0]["imdb_id"].startswith("unverified:")


@pytest.mark.asyncio
async def test_recommend_fails_honestly_with_fallback_disabled(app_and_client, monkeypatch):
    """Same broken-OMDb-key scenario, but with the unverified fallback turned
    off: the failure should be a clear 500 naming OMDb specifically, never
    the generic 'high demand' 503."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ALLOW_UNVERIFIED_FALLBACK", "false")
    from app.services.recommend_service import RecommendationService

    _, client = app_and_client
    # Directly flip the already-constructed service's setting for this request,
    # since app.state was built before the env var above was set.
    app, _ = app_and_client
    app.state.recommend_service._settings.allow_unverified_fallback = False

    gemini_payload = gemini_json_response(
        [{"title": "Inception", "year": 2010, "reason": "Mind-bending heist."}]
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.post(url__regex=rf"{GEMINI_URL_PREFIX}.*generateContent").mock(
            return_value=Response(200, json=gemini_payload)
        )
        mock.get(OMDB_URL).mock(
            return_value=Response(200, json={"Response": "False", "Error": "Invalid API key!"})
        )

        resp = await client.post("/api/recommend", json={"query": "heist movies", "filters": {}})

    assert resp.status_code == 500
    assert "OMDb" in resp.json()["detail"] or "OMDB" in resp.json()["detail"]
    get_settings.cache_clear()
