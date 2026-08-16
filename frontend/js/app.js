(() => {
  "use strict";

  const API_BASE = window.NOW_SHOWING_CONFIG.API_BASE_URL;

  // ---- session id (guest watchlist identity, persisted in localStorage) ---
  const SESSION_KEY = "nowshowing_session_id";
  function getSessionId() {
    let id = localStorage.getItem(SESSION_KEY);
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID() : `sess-${Date.now()}-${Math.random().toString(16).slice(2)}`);
      localStorage.setItem(SESSION_KEY, id);
    }
    return id;
  }
  const sessionId = getSessionId();

  // ---- element refs ---------------------------------------------------------
  const $ = (sel) => document.querySelector(sel);
  const searchForm = $("#searchForm");
  const queryInput = $("#queryInput");
  const similarForm = $("#similarForm");
  const similarInput = $("#similarInput");
  const genreFilter = $("#genreFilter");
  const decadeFilter = $("#decadeFilter");
  const runtimeFilter = $("#runtimeFilter");
  const runtimeValue = $("#runtimeValue");
  const ratingFilter = $("#ratingFilter");
  const ratingValue = $("#ratingValue");
  const resultsGrid = $("#resultsGrid");
  const loadingState = $("#loadingState");
  const idleState = $("#idleState");
  const surpriseMeBtn = $("#surpriseMeBtn");
  const emptyState = $("#emptyState");
  const partialNotice = $("#partialNotice");
  const unverifiedNotice = $("#unverifiedNotice");
  const errorNotice = $("#errorNotice");
  const resultsTitle = $("#resultsTitle");
  const resultsSubtitle = $("#resultsSubtitle");
  const movieModal = $("#movieModal");
  const modalBody = $("#modalBody");
  const watchlistDrawer = $("#watchlistDrawer");
  const watchlistBody = $("#watchlistBody");
  const watchlistCount = $("#watchlistCount");
  const openWatchlistBtn = $("#openWatchlistBtn");
  const toast = $("#toast");

  let watchlistIds = new Set();
  let toastTimer = null;

  // ---- small utilities --------------------------------------------------
  function showToast(message) {
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 2600);
  }

  function currentFilters() {
    const runtime = parseInt(runtimeFilter.value, 10);
    const rating = parseFloat(ratingFilter.value);
    return {
      genre: genreFilter.value || "",
      decade: decadeFilter.value || "",
      country: "",
      runtime: runtime,
      rating: rating,
    };
  }

  async function apiFetch(path, options = {}) {
    const resp = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!resp.ok) {
      let detail = `Request failed (${resp.status})`;
      try {
        const body = await resp.json();
        if (body.detail) detail = body.detail;
      } catch (_) { /* ignore parse errors */ }
      const err = new Error(detail);
      err.status = resp.status;
      throw err;
    }
    if (resp.status === 204) return null;
    return resp.json();
  }

  // ---- rendering: recommendation "ticket" cards --------------------------
  function posterFallbackMarkup(title) {
    return `<div class="ticket__poster-fallback">${escapeHtml(title)}</div>`;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
  }

  function ratingStampMarkup(rating) {
    if (rating === null || rating === undefined) {
      return `<div class="ticket__stamp ticket__stamp--none">N/R</div>`;
    }
    return `<div class="ticket__stamp">${rating.toFixed(1)}</div>`;
  }

  function buildTicketCard(rec) {
    const card = document.createElement("article");
    card.className = "ticket";
    const isSaved = watchlistIds.has(rec.imdb_id);
    const canOpenDetail = rec.verified !== false;

    card.innerHTML = `
      <div class="ticket__poster-wrap" ${canOpenDetail ? `data-open-detail="${rec.imdb_id}"` : ""}>
        ${rec.poster_url
          ? `<img src="${rec.poster_url}" alt="${escapeHtml(rec.title)} poster" loading="lazy">`
          : posterFallbackMarkup(rec.title)}
        ${rec.verified === false
          ? `<div class="ticket__stamp ticket__stamp--none" title="Not checked against a movie database">UNVERIFIED</div>`
          : ratingStampMarkup(rec.rating)}
        ${canOpenDetail ? `
        <button
          class="ticket__watch-btn"
          type="button"
          aria-pressed="${isSaved}"
          aria-label="${isSaved ? "Remove from" : "Add to"} My List"
          data-toggle-watchlist='${JSON.stringify({ imdb_id: rec.imdb_id, title: rec.title, year: rec.year, rating: rec.rating, poster_url: rec.poster_url })}'
        >${isSaved ? "&#10003;" : "&#43;"}</button>` : ""}
      </div>
      <div class="ticket__perforation"></div>
      <div class="ticket__body">
        <div class="ticket__title-row">
          <h3 class="ticket__title" ${canOpenDetail ? `data-open-detail="${rec.imdb_id}"` : ""}>${escapeHtml(rec.title)}</h3>
          <span class="ticket__year">${rec.year ?? ""}</span>
        </div>
        <p class="ticket__reason">${escapeHtml(rec.reason)}</p>
      </div>
    `;
    return card;
  }

  function renderResults(recommendations) {
    idleState.hidden = true;
    resultsGrid.innerHTML = "";
    if (!recommendations.length) {
      emptyState.hidden = false;
      return;
    }
    emptyState.hidden = true;
    const frag = document.createDocumentFragment();
    recommendations.forEach((rec) => frag.appendChild(buildTicketCard(rec)));
    resultsGrid.appendChild(frag);
  }

  function setLoading(isLoading) {
    loadingState.hidden = !isLoading;
    if (isLoading) {
      idleState.hidden = true;
      resultsGrid.innerHTML = "";
      emptyState.hidden = true;
      partialNotice.hidden = true;
      unverifiedNotice.hidden = true;
      errorNotice.hidden = true;
    }
  }

  // ---- recommend / similar flows ------------------------------------------
  async function runRecommend(query) {
    setLoading(true);
    resultsTitle.textContent = query ? "Tonight\u2019s picks" : "A surprise for tonight";
    resultsSubtitle.textContent = query
      ? `Matches for \u201c${query}\u201d`
      : "No prompt given — here\u2019s a well-rounded spread.";
    try {
      const body = { query, filters: currentFilters() };
      const data = await apiFetch("/api/recommend", { method: "POST", body: JSON.stringify(body) });
      renderResults(data.recommendations);
      const anyUnverified = data.recommendations.some((r) => r.verified === false);
      unverifiedNotice.hidden = !anyUnverified;
      partialNotice.hidden = anyUnverified || !data.partial;
    } catch (err) {
      handleRequestError(err);
    } finally {
      setLoading(false);
    }
  }

  async function runSimilar(title) {
    setLoading(true);
    resultsTitle.textContent = `Like \u201c${title}\u201d`;
    resultsSubtitle.textContent = "Similar tone, theme, or style.";
    try {
      const data = await apiFetch("/api/similar", {
        method: "POST",
        body: JSON.stringify({ movie_title: title }),
      });
      renderResults(data.recommendations);
      const anyUnverified = data.recommendations.some((r) => r.verified === false);
      unverifiedNotice.hidden = !anyUnverified;
      partialNotice.hidden = anyUnverified || !data.partial;
    } catch (err) {
      handleRequestError(err);
    } finally {
      setLoading(false);
    }
  }

  function handleRequestError(err) {
    idleState.hidden = true;
    resultsGrid.innerHTML = "";
    emptyState.hidden = true;
    errorNotice.hidden = false;
    // The backend now sends an honest, specific message for config problems
    // (missing/invalid key) and quota exhaustion, not just a blanket "high
    // demand" — surface it directly instead of a generic fallback whenever
    // we have one.
    errorNotice.textContent =
      err.message && err.message !== `Request failed (${err.status})`
        ? err.message
        : "Something went wrong reaching the recommendation engine. Please try again.";
  }

  // ---- movie detail modal --------------------------------------------------
  function openModal() { movieModal.hidden = false; document.body.style.overflow = "hidden"; }
  function closeModal() { movieModal.hidden = true; document.body.style.overflow = ""; }
  function openDrawer() { watchlistDrawer.hidden = false; document.body.style.overflow = "hidden"; }
  function closeDrawer() { watchlistDrawer.hidden = true; document.body.style.overflow = ""; }

  async function openMovieDetail(imdbId) {
    openModal();
    modalBody.innerHTML = `<div class="ticket-skeleton ticket-skeleton--modal"></div>`;
    try {
      const detail = await apiFetch(`/api/movie/${encodeURIComponent(imdbId)}`);
      renderMovieDetail(detail);
    } catch (err) {
      modalBody.innerHTML = `<div class="detail__content"><p class="detail__plot">Couldn't load details for this title right now.</p></div>`;
    }
  }

  function renderMovieDetail(detail) {
    const isSaved = watchlistIds.has(detail.imdb_id);
    const ratingChips = [
      detail.rating != null ? `<span class="rating-chip">IMDb ${detail.rating.toFixed(1)}</span>` : "",
      ...Object.entries(detail.extra_ratings || {}).map(
        ([source, value]) => `<span class="rating-chip">${escapeHtml(source)} ${escapeHtml(value)}</span>`
      ),
    ].join("");

    const justwatchUrl = `https://www.justwatch.com/us/search?q=${encodeURIComponent(detail.title)}`;

    modalBody.innerHTML = `
      <div class="detail__hero">
        ${detail.poster_url
          ? `<img class="detail__poster" src="${detail.poster_url}" alt="${escapeHtml(detail.title)} poster">`
          : `<div class="detail__poster"></div>`}
        <div class="detail__gradient"></div>
      </div>
      <div class="detail__content">
        <h2 class="detail__title">${escapeHtml(detail.title)}</h2>
        <div class="detail__meta">
          ${detail.year ? `<span>${detail.year}</span>` : ""}
          ${detail.rated ? `<span>${escapeHtml(detail.rated)}</span>` : ""}
          ${detail.runtime ? `<span>${detail.runtime} min</span>` : ""}
          ${detail.genres && detail.genres.length ? `<span>${escapeHtml(detail.genres.join(" / "))}</span>` : ""}
        </div>
        ${ratingChips ? `<div class="detail__ratings">${ratingChips}</div>` : ""}
        ${detail.synopsis ? `<p class="detail__plot">${escapeHtml(detail.synopsis)}</p>` : ""}
        ${detail.director ? `<p class="detail__row"><strong>Director:</strong> ${escapeHtml(detail.director)}</p>` : ""}
        ${detail.cast ? `<p class="detail__row"><strong>Cast:</strong> ${escapeHtml(detail.cast)}</p>` : ""}
        ${detail.country ? `<p class="detail__row"><strong>Country:</strong> ${escapeHtml(detail.country)}</p>` : ""}
        <div class="detail__actions">
          <button class="btn btn--amber" type="button" data-toggle-watchlist='${JSON.stringify({
            imdb_id: detail.imdb_id, title: detail.title, year: detail.year, rating: detail.rating, poster_url: detail.poster_url
          })}'>${isSaved ? "Remove from My List" : "Add to My List"}</button>
          <a class="btn btn--teal-outline" href="${justwatchUrl}" target="_blank" rel="noopener">Find where to watch \u2197</a>
        </div>
      </div>
    `;
  }

  // ---- watchlist -----------------------------------------------------------
  function renderWatchlistCount() {
    watchlistCount.textContent = String(watchlistIds.size);
  }

  function watchRowMarkup(item) {
    return `
      <div class="watch-row" data-imdb-id="${item.imdb_id}">
        ${item.poster_url ? `<img src="${item.poster_url}" alt="">` : `<div style="width:46px;height:68px;border-radius:4px;background:var(--navy-700);flex-shrink:0;"></div>`}
        <div class="watch-row__meta">
          <p class="watch-row__title">${escapeHtml(item.title)}</p>
          <span class="watch-row__year">${item.year ?? ""}${item.rating != null ? ` \u00b7 ${item.rating.toFixed(1)}` : ""}</span>
        </div>
        <button class="watch-row__remove" type="button" data-remove-watchlist="${item.imdb_id}" aria-label="Remove ${escapeHtml(item.title)}">&times;</button>
      </div>
    `;
  }

  async function loadWatchlist() {
    try {
      const data = await apiFetch(`/api/watchlist/${encodeURIComponent(sessionId)}`);
      watchlistIds = new Set(data.movies.map((m) => m.imdb_id));
      renderWatchlistCount();
      if (data.movies.length) {
        watchlistBody.innerHTML = data.movies.map(watchRowMarkup).join("");
      } else {
        watchlistBody.innerHTML = `<p class="drawer__empty">Nothing saved yet. Tap the stub on any recommendation to add it here.</p>`;
      }
    } catch (_) {
      // Backend may not be reachable yet (e.g. no API key configured) — fail quietly,
      // the watchlist button just stays empty until the user retries an action.
    }
  }

  async function addToWatchlist(item) {
    watchlistIds.add(item.imdb_id);
    renderWatchlistCount();
    syncWatchButtons(item.imdb_id, true);
    try {
      await apiFetch("/api/watchlist", {
        method: "POST",
        body: JSON.stringify({ session_id: sessionId, imdb_id: item.imdb_id }),
      });
      await loadWatchlist();
      showToast(`Added \u201c${item.title}\u201d to My List`);
    } catch (err) {
      watchlistIds.delete(item.imdb_id);
      renderWatchlistCount();
      syncWatchButtons(item.imdb_id, false);
      showToast("Couldn't save that right now \u2014 try again.");
    }
  }

  async function removeFromWatchlist(imdbId, title) {
    watchlistIds.delete(imdbId);
    renderWatchlistCount();
    syncWatchButtons(imdbId, false);
    try {
      await apiFetch(`/api/watchlist/${encodeURIComponent(sessionId)}/${encodeURIComponent(imdbId)}`, {
        method: "DELETE",
      });
      await loadWatchlist();
      showToast(title ? `Removed \u201c${title}\u201d` : "Removed from My List");
    } catch (_) {
      await loadWatchlist();
    }
  }

  function syncWatchButtons(imdbId, saved) {
    document.querySelectorAll(`[data-toggle-watchlist]`).forEach((btn) => {
      const payload = JSON.parse(btn.getAttribute("data-toggle-watchlist"));
      if (payload.imdb_id !== imdbId) return;
      if (btn.classList.contains("ticket__watch-btn")) {
        btn.setAttribute("aria-pressed", String(saved));
        btn.innerHTML = saved ? "&#10003;" : "&#43;";
      } else {
        btn.textContent = saved ? "Remove from My List" : "Add to My List";
      }
    });
  }

  // ---- event wiring ----------------------------------------------------
  searchForm.addEventListener("submit", (e) => {
    e.preventDefault();
    runRecommend(queryInput.value.trim());
  });

  similarForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const title = similarInput.value.trim();
    if (title) runSimilar(title);
  });

  runtimeFilter.addEventListener("input", () => {
    const v = parseInt(runtimeFilter.value, 10);
    runtimeValue.textContent = v >= 240 ? "No limit" : `${v} min`;
  });
  ratingFilter.addEventListener("input", () => {
    const v = parseFloat(ratingFilter.value);
    ratingValue.textContent = v <= 0 ? "Any" : `${v.toFixed(1)}+`;
  });

  document.addEventListener("click", (e) => {
    const detailTrigger = e.target.closest("[data-open-detail]");
    if (detailTrigger) {
      openMovieDetail(detailTrigger.getAttribute("data-open-detail"));
      return;
    }
    const watchToggle = e.target.closest("[data-toggle-watchlist]");
    if (watchToggle) {
      const payload = JSON.parse(watchToggle.getAttribute("data-toggle-watchlist"));
      if (watchlistIds.has(payload.imdb_id)) {
        removeFromWatchlist(payload.imdb_id, payload.title);
      } else {
        addToWatchlist(payload);
      }
      return;
    }
    const removeTrigger = e.target.closest("[data-remove-watchlist]");
    if (removeTrigger) {
      removeFromWatchlist(removeTrigger.getAttribute("data-remove-watchlist"));
      return;
    }
    if (e.target.closest("[data-close-modal]")) closeModal();
    if (e.target.closest("[data-close-drawer]")) closeDrawer();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!movieModal.hidden) closeModal();
    if (!watchlistDrawer.hidden) closeDrawer();
  });

  openWatchlistBtn.addEventListener("click", () => {
    openDrawer();
    loadWatchlist();
  });

  surpriseMeBtn.addEventListener("click", () => runRecommend(""));

  // ---- init -------------------------------------------------------------
  // NOTE: we deliberately do NOT call runRecommend() automatically here.
  // Gemini's free tier has a real per-minute/per-day quota, and every page
  // load (including plain refreshes during development) used to fire a
  // request on its own — a major, easy-to-miss contributor to "high
  // demand" errors that had nothing to do with actual traffic. The user
  // now triggers the first Gemini call explicitly (search, or "Surprise me").
  loadWatchlist();
})();
