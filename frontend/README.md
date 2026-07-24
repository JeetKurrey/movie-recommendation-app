# Popcorn Cue — Frontend

A dependency-free static site (HTML/CSS/vanilla JS — no build step) that
talks to the FastAPI backend in `../backend`. No React/Vite/npm required,
so there's nothing to install: open it and it works.

## Design

The theme is a marquee at dusk: deep navy instead of black, warm amber
"bulb" light for primary actions and ratings, a cool teal thread for the
"find similar" feature. Recommendations render as **ticket stubs** —
poster on top, a perforated tear-line, then the AI's one-line reasoning
underneath, with the IMDb rating as a rotated stamp — because a
recommendation is, in spirit, a ticket to go watch something.

## 1. Point it at your backend

Edit `js/config.js`:

```js
window.NOW_SHOWING_CONFIG = {
  API_BASE_URL: "http://localhost:8000",   // or your deployed backend URL
};
```

## 2. Run it locally

Any static file server works — this needs to be served over HTTP (not
opened as a `file://` path) for `fetch()` to behave predictably. Pick one:

```bash
# Python
cd frontend
python3 -m http.server 5500

# Node (npx, no install)
npx serve -l 5500 frontend

# VS Code
# Right-click index.html -> "Open with Live Server" (defaults to :5500)
```

Then open http://localhost:5500. Make sure that origin is in the
backend's `CORS_ORIGINS` (the `.env.example` default already includes
`http://localhost:5500`).

## 3. What's where

```
frontend/
├── index.html        # page structure
├── css/styles.css     # all styling — tokens at the top of the file
├── js/config.js       # the ONE line you edit to point at a backend
├── js/app.js          # fetch calls, rendering, modal/drawer/watchlist logic
└── assets/            # (empty — add your own favicon/OG image here)
```

## 4. Notes

- **Watchlist identity** is a random ID generated once with
  `crypto.randomUUID()` and kept in `localStorage`, matching the PRD's
  "session-based, guest" watchlist model (FR-8/FR-9). No login.
- **"Find where to watch"** links out to a JustWatch search for the
  title rather than calling a streaming-availability API — OMDb's free
  tier doesn't expose watch-provider data, so this keeps the promise of
  "recommend and link out" without guessing at availability.
- No frontend framework/build step on purpose — it's a portfolio-scale
  MVP per the PRD, and a plain static site deploys to Vercel/Netlify/GitHub
  Pages as-is.
