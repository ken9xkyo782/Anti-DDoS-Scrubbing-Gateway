import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

logger = logging.getLogger(__name__)

PostCommitCallback = Callable[[], Awaitable[None]]
_POST_COMMIT_CALLBACKS_KEY = "post_commit_callbacks"


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine

    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory

    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
            await run_post_commit_callbacks(session)
        except Exception:
            discard_post_commit_callbacks(session)
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
            await run_post_commit_callbacks(session)
        except Exception:
            discard_post_commit_callbacks(session)
            await session.rollback()
            raise


def add_post_commit_callback(session: AsyncSession, callback: PostCommitCallback) -> None:
    callbacks = session.info.setdefault(_POST_COMMIT_CALLBACKS_KEY, [])
    callbacks.append(callback)


def discard_post_commit_callbacks(session: AsyncSession) -> None:
    session.info.pop(_POST_COMMIT_CALLBACKS_KEY, None)


async def run_post_commit_callbacks(session: AsyncSession) -> None:
    callbacks = session.info.pop(_POST_COMMIT_CALLBACKS_KEY, [])
    for callback in callbacks:
        try:
            await callback()
        except Exception:
            logger.exception("Post-commit callback failed")


async def dispose_engine() -> None:
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
