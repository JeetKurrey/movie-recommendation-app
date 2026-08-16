# Popcorn Cue — Backend (FastAPI + Gemini + OMDb)

The API-only half of the app. Builds prompts, calls Gemini for
recommendations, verifies every title against OMDb (the hallucination
guard), enriches with poster/rating/plot, and serves a small SQLite-backed
watchlist. The frontend is a separate static site in `../frontend` — see
its README for how the two talk to each other.

## 1. Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

| Variable | Where to get it |
|---|---|
| `GEMINI_API_KEY` | Free key at https://aistudio.google.com/apikey |
| `OMDB_API_KEY` | Free key at https://www.omdbapi.com/apikey.aspx (1,000 req/day) |

`CORS_ORIGINS` must include whatever origin the frontend is served from
(defaults already cover `http://localhost:5500` for VS Code's Live Server
and `http://localhost:3000` for a Node static server).

## 2. Run

```bash
uvicorn app.main:app --reload --port 8000
```

- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/api/health

## 3. Test

```bash
pytest
```

Tests mock both Gemini and OMDb via `respx`, so no API keys or network
access are needed to run the suite.

## 4. Why OMDb instead of TMDB

OMDb wraps IMDb's data behind a single simple endpoint — good for an
MVP/portfolio build — but it has different shapes and limits than TMDB,
which shows up in a few places in this codebase:

- **Two-step lookups.** `s=` (search) returns only a title/year/poster
  summary; a second `i=` (by-id) call is needed for plot, cast, runtime,
  and `imdbRating`. `omdb_client.py` and `recommend_service.py` both
  reflect this — recommendations get a poster immediately, then their
  `imdbRating` is filled in with a small concurrent batch of detail calls.
- **200-OK errors.** OMDb never returns a 4xx for "not found" — it
  returns HTTP 200 with `{"Response": "False"}`. `omdb_client.py`
  normalizes this so the rest of the code can treat it like any other
  "no result" case, while still detecting the rate-limit variant of that
  same message so it gets retried instead of treated as a permanent miss.
- **`"N/A"` instead of `null`.** Every OMDb field that's missing comes
  back as the literal string `"N/A"`. `_clean()` in `omdb_client.py`
  normalizes this everywhere so `None` behaves like you'd expect
  downstream (Pydantic, the frontend, etc).
- **No watch-providers endpoint.** TMDB's `/watch/providers` doesn't have
  an OMDb equivalent on the free tier, so streaming availability isn't
  fetched server-side. The frontend instead offers a "Find it to watch"
  outbound link to JustWatch's search page — no API key needed, keeps the
  PRD's "recommend and link out" scope, and doesn't misrepresent
  availability from stale/guessed data.
- **IDs are strings, not ints.** Every `tmdb_id: int` became
  `imdb_id: str` (e.g. `"tt1375666"`) across the schemas, DB model, and
  routes.

## 5. Project layout

```
backend/
├── app/
│   ├── main.py                 # FastAPI app, lifespan, CORS
│   ├── routers/                 # recommend, movies, watchlist
│   ├── services/
│   │   ├── gemini_client.py     # prompt building + JSON-mode call to Gemini
│   │   ├── omdb_client.py       # search/verify + detail enrichment
│   │   ├── recommend_service.py # orchestrates the hallucination-guard flow
│   │   └── cache.py             # zero-infra async TTL+LRU cache
│   ├── schemas/models.py        # Pydantic request/response models
│   ├── models/db.py             # SQLAlchemy watchlist table
│   └── core/                    # settings, logging, DI wiring
├── tests/                        # pytest + respx, no live network needed
├── requirements.txt
└── .env.example
```

## 6. Troubleshooting "high demand" / 503 errors

The API used to collapse every failure into one generic "high demand"
message, which hid real problems (a bad key looked identical to genuine
traffic-based unavailability). It now distinguishes them, both in the
server logs and in the HTTP response:

| What you'll see | Actual cause | Fix |
|---|---|---|
| HTTP 500, "GEMINI_API_KEY is missing or invalid" | Gemini key not set, wrong, or not yet active | Check `.env`, regenerate at https://aistudio.google.com/apikey |
| HTTP 500, "OMDB_API_KEY is missing or invalid" | OMDb key not set, wrong, or unactivated | Check `.env`; click the activation link in OMDb's signup email |
| HTTP 503, "Gemini's free-tier quota is exhausted" | Genuine per-minute or per-day rate limit | Wait a minute (per-minute) or until tomorrow (daily); check https://ai.dev/rate-limit |
| HTTP 200, results marked `"verified": false` | OMDb is down/misconfigured but Gemini still worked | These are AI suggestions that weren't checked against a real movie database — shown only because `ALLOW_UNVERIFIED_FALLBACK=true` (the default); set it to `false` to disable this and fail loudly instead |

**The single biggest quota-saver:** the frontend no longer calls
`/api/recommend` automatically on page load — every reload used to fire a
Gemini request on its own, which adds up fast against a free-tier
per-minute limit during development. The first Gemini call now only
happens when the user searches or taps "Surprise me".

## 7. Deploying

Any ASGI-friendly free host works (Render, Railway, Fly.io). Point
`DATABASE_URL` at Postgres for anything beyond a single instance — the
watchlist table is small and works unchanged with either SQLite or
Postgres. Remember to add your deployed frontend's origin to
`CORS_ORIGINS`.
