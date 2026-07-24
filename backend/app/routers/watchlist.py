import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_omdb_client
from app.models.db import WatchlistItem
from app.schemas.models import WatchlistAddRequest, WatchlistItemOut, WatchlistResponse
from app.services.omdb_client import OMDbClient, OMDbError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["watchlist"])


@router.post("/watchlist", response_model=WatchlistItemOut, status_code=201)
async def add_to_watchlist(
    body: WatchlistAddRequest,
    db: Session = Depends(get_db),
    omdb: OMDbClient = Depends(get_omdb_client),
) -> WatchlistItemOut:
    existing = (
        db.query(WatchlistItem)
        .filter_by(session_id=body.session_id, imdb_id=body.imdb_id)
        .first()
    )
    if existing:
        return WatchlistItemOut(
            imdb_id=existing.imdb_id,
            title=existing.title,
            year=existing.year,
            rating=existing.rating,
            poster_url=existing.poster_url,
        )

    # Pull real metadata so the watchlist always shows a title/poster even
    # if the frontend only sent us an id (e.g. added straight from a detail page).
    try:
        detail = await omdb.get_movie_detail(body.imdb_id)
    except OMDbError as exc:
        logger.error("Could not enrich watchlist add for id %s: %s", body.imdb_id, exc)
        raise HTTPException(status_code=502, detail="Could not verify that movie right now.") from exc

    item = WatchlistItem(
        session_id=body.session_id,
        imdb_id=body.imdb_id,
        title=detail["title"],
        year=detail.get("year"),
        rating=detail.get("rating"),
        poster_url=detail.get("poster_url"),
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # lost a race with a duplicate insert — fetch what's there
        existing = (
            db.query(WatchlistItem)
            .filter_by(session_id=body.session_id, imdb_id=body.imdb_id)
            .first()
        )
        if existing:
            item = existing

    return WatchlistItemOut(
        imdb_id=item.imdb_id, title=item.title, year=item.year, rating=item.rating, poster_url=item.poster_url
    )


@router.get("/watchlist/{session_id}", response_model=WatchlistResponse)
async def get_watchlist(session_id: str, db: Session = Depends(get_db)) -> WatchlistResponse:
    items = db.query(WatchlistItem).filter_by(session_id=session_id).order_by(WatchlistItem.added_at.desc()).all()
    return WatchlistResponse(
        movies=[
            WatchlistItemOut(imdb_id=i.imdb_id, title=i.title, year=i.year, rating=i.rating, poster_url=i.poster_url)
            for i in items
        ]
    )


@router.delete("/watchlist/{session_id}/{imdb_id}", status_code=204)
async def delete_from_watchlist(session_id: str, imdb_id: str, db: Session = Depends(get_db)) -> None:
    deleted = (
        db.query(WatchlistItem)
        .filter_by(session_id=session_id, imdb_id=imdb_id)
        .delete()
    )
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Watchlist item not found.")
