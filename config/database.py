"""ECONITH :: config.database

Async database engine + session factory. Defaults to SQLite for local state
recovery (per master plan Phase 4) and supports Postgres in production via
``DATABASE_URL``.

P0 REFACTOR (Storage Layer Resiliency & Failover)
-------------------------------------------------
Previously the engine bound lazily to whatever ``DATABASE_URL`` resolved to --
typically the Docker Postgres instance (``postgresql://...@postgres:5432/...``).
When executed locally without that container the connection failed silently or
froze, corrupting the telemetry persistence pipeline.

:func:`init_database` now performs an explicit, bounded connectivity probe
against the primary DSN during startup. On *any* failure (unreachable host, DNS
error, auth failure, timeout, missing driver) it:

  1. Emits a high-visibility ``CRITICAL`` log.
  2. Disposes the half-open primary engine.
  3. Seamlessly re-routes persistence to a local SQLite failover matrix
     (``sqlite:///econith_fallback.db``).

The application therefore always boots to a working persistence layer.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config.environment import get_environment

logger = logging.getLogger("econith.config.database")

# Local, dependency-free failover target. Always reachable on any host.
FALLBACK_URL: str = "sqlite:///econith_fallback.db"

# Bound (seconds) on the primary connectivity probe so a dead host can never
# hang the ASGI lifespan.
_PRIMARY_PROBE_TIMEOUT_S: float = 5.0


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _to_async_url(url: str) -> str:
    """Promote a sync DSN to its async driver variant."""
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):  # heroku-style alias
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def _build_engine(async_url: str) -> AsyncEngine:
    """Instantiate an async engine with sane pooling for the given driver."""
    kwargs: dict[str, object] = {"echo": False, "pool_pre_ping": True}
    if async_url.startswith("postgresql+asyncpg://"):
        # asyncpg honours ``timeout`` in connect_args -> bounded initial connect.
        kwargs["connect_args"] = {"timeout": _PRIMARY_PROBE_TIMEOUT_S}
    return create_async_engine(async_url, **kwargs)


_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_using_fallback: bool = False


async def _probe(engine: AsyncEngine) -> None:
    """Open a real connection and run ``SELECT 1`` under a hard timeout."""
    async def _run() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    await asyncio.wait_for(_run(), timeout=_PRIMARY_PROBE_TIMEOUT_S)


async def init_database() -> AsyncEngine:
    """Verify the primary DB, failing over to local SQLite on any error.

    Safe to call once during ASGI startup. Never raises: a total primary failure
    is intercepted and re-routed to :data:`FALLBACK_URL`.
    """
    global _engine, _session_factory, _using_fallback

    env = get_environment()
    primary_async_url = _to_async_url(env.database_url)

    # SQLite primary needs no probe -- it is inherently local and reliable.
    if primary_async_url.startswith("sqlite"):
        _engine = _build_engine(primary_async_url)
        _session_factory = None
        _using_fallback = False
        logger.info("[DATABASE RUNTIME] using local SQLite engine (%s)", env.database_url)
        return _engine

    candidate: Optional[AsyncEngine] = None
    try:
        candidate = _build_engine(primary_async_url)
        await _probe(candidate)
    except Exception as exc:  # noqa: BLE001 - ANY primary fault must fail over
        logger.critical(
            "[DATABASE RUNTIME] Primary Postgres connection failed "
            "(%s: %s). Deploying local failover instance.",
            type(exc).__name__, exc,
        )
        if candidate is not None:
            try:
                await candidate.dispose()
            except Exception:  # noqa: BLE001 - disposal must never crash startup
                logger.debug("primary engine disposal raised during failover", exc_info=True)
        _engine = _build_engine(_to_async_url(FALLBACK_URL))
        _session_factory = None
        _using_fallback = True
        logger.warning("[DATABASE RUNTIME] persistence re-routed to %s", FALLBACK_URL)
        return _engine

    _engine = candidate
    _session_factory = None
    _using_fallback = False
    logger.info("[DATABASE RUNTIME] primary database connection verified")
    return _engine


def is_fallback() -> bool:
    """True when persistence is running on the SQLite failover matrix."""
    return _using_fallback


def get_engine() -> AsyncEngine:
    """Return the initialised engine.

    If :func:`init_database` has not run yet (e.g. a non-lifespan context), build
    the primary engine lazily. Prefer calling :func:`init_database` at startup so
    the failover probe runs.
    """
    global _engine
    if _engine is None:
        env = get_environment()
        logger.warning(
            "[DATABASE RUNTIME] get_engine() called before init_database(); "
            "building engine lazily without failover probe"
        )
        _engine = _build_engine(_to_async_url(env.database_url))
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a scoped async session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def dispose_database() -> None:
    """Dispose the active engine (call on ASGI shutdown)."""
    global _engine, _session_factory
    if _engine is not None:
        try:
            await _engine.dispose()
        except Exception:  # noqa: BLE001
            logger.debug("engine disposal raised on shutdown", exc_info=True)
    _engine = None
    _session_factory = None
