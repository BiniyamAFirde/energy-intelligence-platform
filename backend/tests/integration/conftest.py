"""Integration tests run against a real Postgres database -- a dedicated
`energy_platform_test` database on the same server as the dev DB, so tests
never touch dev/ingested data. Alembic runs the real migration against it
(this doubles as the "migration applies cleanly" test), and each test runs
inside a transaction that's rolled back afterwards so tests stay isolated
and repeatable without needing to drop/recreate the schema every time.
"""

import os
import subprocess

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from energy_platform.config import settings


def _test_database_url() -> str:
    base = settings.database_url
    assert base.rsplit("/", 1)[-1] != "postgres", "refusing to test against the postgres admin db"
    root, _, _ = base.rpartition("/")
    return f"{root}/energy_platform_test"


def _ensure_database_exists(admin_url: str, db_name: str) -> None:
    engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": db_name}
        ).first()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))
    engine.dispose()


@pytest.fixture(scope="session")
def test_db_url() -> str:
    url = _test_database_url()
    admin_url = url.rsplit("/", 1)[0] + "/" + settings.database_url.rsplit("/", 1)[-1]
    db_name = url.rsplit("/", 1)[-1]
    _ensure_database_exists(admin_url, db_name)

    # Run the real Alembic migration against it -- this is also the
    # "migration applies to a clean database" test: if this fails, so does
    # collection of every other integration test. env.py reads DATABASE_URL
    # via energy_platform.config.settings, so overriding it for this
    # subprocess is enough to point the migration at the test DB instead of
    # the dev DB, without touching the in-process settings singleton other
    # tests may rely on.
    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd="/app",
        env={**os.environ, "DATABASE_URL": url},
        check=True,
    )
    return url


@pytest.fixture(scope="session")
def test_engine(test_db_url):
    engine = sa.create_engine(test_db_url)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    connection = test_engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture()
def api_client(db_session):
    """A FastAPI TestClient whose get_db dependency is overridden to reuse
    this test's db_session -- so fixture data set up via db_session (even
    just flushed, not committed) is visible to API calls made through this
    client, and everything rolls back together at teardown."""
    from fastapi.testclient import TestClient

    from energy_platform.api.deps import get_db
    from energy_platform.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
