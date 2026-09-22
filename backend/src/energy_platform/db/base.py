from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from energy_platform.config import settings


class Base(DeclarativeBase):
    pass


# Created once at import time and reused: an Engine owns a connection pool,
# so recreating it per request/call would defeat pooling entirely.
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
