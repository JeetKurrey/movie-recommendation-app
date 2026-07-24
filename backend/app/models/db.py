"""
SQLAlchemy setup + the Watchlist table.

SQLite for the MVP, upgradeable to Postgres later — using DATABASE_URL as
the single switch means that upgrade requires no code changes, just an
env var pointed at a Postgres instance.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import Settings


class Base(DeclarativeBase):
    pass


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("session_id", "imdb_id", name="uq_session_movie"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(128), nullable=False, index=True)
    imdb_id = Column(String(20), nullable=False)
    title = Column(String(500), nullable=False)
    year = Column(Integer, nullable=True)
    rating = Column(Float, nullable=True)
    poster_url = Column(String(1000), nullable=True)
    added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def build_engine(settings: Settings):
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    return create_engine(settings.database_url, connect_args=connect_args)


def build_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
