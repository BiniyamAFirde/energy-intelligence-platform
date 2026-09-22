import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://u:p@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from energy_platform.config import Settings  # noqa: E402


def test_settings_load_from_env():
    s = Settings()
    assert s.database_url.startswith("postgresql")
    assert s.jwt_algorithm == "HS256"


def test_cors_origin_list_splits_on_comma():
    s = Settings(cors_origins="http://a.com, http://b.com")
    assert s.cors_origin_list == ["http://a.com", "http://b.com"]
