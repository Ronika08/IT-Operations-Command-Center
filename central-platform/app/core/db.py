"""
SQLAlchemy engine / session factory, shared by the whole app.

`get_db` is a FastAPI dependency: it opens one session per request and
guarantees it is closed afterwards, even on exceptions - this is the
standard "session-per-request" pattern and avoids the classic bug of
leaking connections under load (which is exactly the kind of thing this
project's own performance test is designed to catch).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.db import Base

settings = get_settings()

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all tables. In a real production rollout this would be
    replaced by versioned Alembic migrations (see database/migrations for
    the equivalent raw-SQL version used for review purposes)."""
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
