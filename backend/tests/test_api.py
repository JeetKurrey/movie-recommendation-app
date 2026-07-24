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
