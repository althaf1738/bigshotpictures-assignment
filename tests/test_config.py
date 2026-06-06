from __future__ import annotations

from app.config import Settings


def test_database_url_normalizes_postgres_scheme_for_async_sqlalchemy():
    settings = Settings(database_url="postgres://user:pass@host:5432/db")
    assert settings.sqlalchemy_database_url == "postgresql+asyncpg://user:pass@host:5432/db"


def test_database_url_normalizes_postgresql_scheme_for_async_sqlalchemy():
    settings = Settings(database_url="postgresql://user:pass@host:5432/db")
    assert settings.sqlalchemy_database_url == "postgresql+asyncpg://user:pass@host:5432/db"


def test_database_url_keeps_sqlite_scheme_unchanged():
    settings = Settings(database_url="sqlite+aiosqlite:///./reviews.db")
    assert settings.sqlalchemy_database_url == "sqlite+aiosqlite:///./reviews.db"
