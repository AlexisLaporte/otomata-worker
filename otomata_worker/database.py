"""Database session management for PostgreSQL."""

from contextlib import contextmanager
from sqlalchemy.orm import sessionmaker

from .models import get_engine, init_db as models_init_db

_engine = None
_Session = None


def get_db_engine():
    """Get or create the PostgreSQL database engine."""
    global _engine
    if _engine is None:
        _engine = get_engine()
    return _engine


def get_session_factory():
    """Get or create the session factory."""
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=get_db_engine())
    return _Session


@contextmanager
def get_session():
    """Context manager for database sessions."""
    Session = get_session_factory()
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db():
    """Initialize database tables."""
    engine = get_db_engine()
    models_init_db(engine)
    return engine
