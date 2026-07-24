// Single place to point the frontend at the backend.
// Local dev: FastAPI running on port 8000 (uvicorn app.main:app --reload --port 8000).
// Deployed: swap this for your deployed backend's URL (Render/Railway/Fly.io, etc).
window.NOW_SHOWING_CONFIG = {
  API_BASE_URL: "https://popcorn-cue-app.onrender.com",
};
