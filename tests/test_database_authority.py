"""Database authority contract: staging/production never silently fork state.

These tests mock network access. They do not contact Postgres, Binance, or
modify existing SQLite journals and are safe to run in first-party CI.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from config import database as db
from config.environment import AppEnv


class FakeEngine:
    def __init__(self):
        self.disposed = False

    async def dispose(self):
        self.disposed = True


def configure(monkeypatch, *, mode: AppEnv, dsn: str):
    monkeypatch.setattr(
        db, "get_environment", lambda: SimpleNamespace(app_env=mode, database_url=dsn)
    )
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_using_fallback", False)
    monkeypatch.setattr(db, "_session_factory", None)


@pytest.mark.parametrize("mode", [AppEnv.PRODUCTION, AppEnv.STAGING])
def test_strict_environments_reject_sqlite_even_if_writable(monkeypatch, mode):
    configure(monkeypatch, mode=mode, dsn="sqlite:///./datasets/econith.sqlite")

    def forbidden_engine(_url):
        raise AssertionError("strict mode must reject SQLite before creating engine")

    monkeypatch.setattr(db, "_build_engine", forbidden_engine)
    with pytest.raises(RuntimeError, match="PostgreSQL DATABASE_URL is required"):
        asyncio.run(db.init_database())
    assert db._engine is None


@pytest.mark.parametrize("mode", [AppEnv.PRODUCTION, AppEnv.STAGING])
def test_primary_unavailable_never_falls_back_in_strict_mode(monkeypatch, mode, caplog):
    configure(
        monkeypatch,
        mode=mode,
        dsn="postgresql://economy:SECRET_SHOULD_NOT_APPEAR@pg:5432/econith",
    )
    created = []
    candidate = FakeEngine()

    def build(url):
        created.append(url)
        if url.startswith("sqlite"):
            raise AssertionError("SQLite fallback must not be initialized")
        return candidate

    async def unavailable(_engine):
        raise ConnectionError("PostgreSQL connection refused")

    monkeypatch.setattr(db, "_build_engine", build)
    monkeypatch.setattr(db, "_probe", unavailable)
    with caplog.at_level("CRITICAL"):
        with pytest.raises(RuntimeError, match="Primary PostgreSQL database unavailable"):
            asyncio.run(db.init_database())

    assert len(created) == 1
    assert candidate.disposed is True
    assert db._engine is None and not db.is_fallback()
    assert "SECRET_SHOULD_NOT_APPEAR" not in caplog.text


def test_development_retains_offline_sqlite_fallback(monkeypatch):
    configure(monkeypatch, mode=AppEnv.DEVELOPMENT, dsn="postgresql://pg/econith")
    candidate = FakeEngine()
    fallback = FakeEngine()
    urls = []

    def build(url):
        urls.append(url)
        return fallback if url.startswith("sqlite") else candidate

    async def unavailable(_engine):
        raise ConnectionError("offline test")

    monkeypatch.setattr(db, "_build_engine", build)
    monkeypatch.setattr(db, "_probe", unavailable)
    got = asyncio.run(db.init_database())
    assert got is fallback and db.is_fallback()
    assert candidate.disposed
    assert urls[-1] == db._to_async_url(db.FALLBACK_URL)


def test_production_accepts_verified_postgresql_without_fallback(monkeypatch):
    configure(monkeypatch, mode=AppEnv.PRODUCTION, dsn="postgresql://pg/econith")
    engine = FakeEngine()
    monkeypatch.setattr(db, "_build_engine", lambda _url: engine)

    async def success(value):
        assert value is engine

    monkeypatch.setattr(db, "_probe", success)
    assert asyncio.run(db.init_database()) is engine
    assert not db.is_fallback()


def test_production_forbids_lazy_unprobed_engine(monkeypatch):
    configure(monkeypatch, mode=AppEnv.PRODUCTION, dsn="postgresql://pg/econith")
    with pytest.raises(RuntimeError, match="startup probe"):
        db.get_engine()
