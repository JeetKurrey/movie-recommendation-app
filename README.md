# Popcorn Cue

An AI-powered movie recommendation app: describe what you want to watch,
Gemini suggests titles, every title is verified against **OMDb** before
it's shown to you (so you never get a made-up movie), and results render
as a marquee of ticket-stub cards you can save to a watchlist.

This follows the original PRD's architecture and scope, swapped from
TMDB to **OMDb** for metadata, with the frontend and backend split into
two independent projects that talk over a plain HTTP API.

```
now-showing/
├── backend/     # FastAPI + Gemini + OMDb — API only, see backend/README.md
└── frontend/    # Static HTML/CSS/JS — see frontend/README.md
```

## Quick start

**1. Backend** (needs a free Gemini key and a free OMDb key):

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in GEMINI_API_KEY and OMDB_API_KEY
uvicorn app.main:app --reload --port 8000
```

**2. Frontend** (no install needed):

```bash
cd frontend
python3 -m http.server 5500
```

Open http://localhost:5500 — the frontend is already configured to talk
to `http://localhost:8000` in `frontend/js/config.js`.

## Why two separate projects

The backend is a pure JSON API (FastAPI, CORS-enabled, no template
rendering) and the frontend is a static site with zero build tooling —
either can be deployed independently (e.g. backend on Render/Railway,
frontend on Vercel/Netlify/GitHub Pages) and neither needs to know how
the other is hosted beyond one config value (`CORS_ORIGINS` on the
backend, `API_BASE_URL` on the frontend).

## What changed from the original PRD

- **TMDB → OMDb** for all movie metadata (search/verify, poster,
  plot, cast, runtime, rating). See `backend/README.md` §4 for the
  concrete API differences this required handling.
- **No server-side watch-provider lookup** — OMDb's free tier doesn't
  have TMDB's `/watch/providers` equivalent, so the frontend links out
  to a JustWatch search instead of showing (possibly stale/wrong)
  streaming badges.
- **Frontend rebuilt** as a distinct visual design (a cinema-marquee
  theme with ticket-stub recommendation cards) rather than the
  generic dashboard look, and moved into its own top-level folder
  instead of being served as `static/` inside the FastAPI app.

## Full docs

- [`backend/README.md`](backend/README.md) — setup, env vars, testing, deployment, OMDb quirks
- [`frontend/README.md`](frontend/README.md) — design notes, local serving, config
