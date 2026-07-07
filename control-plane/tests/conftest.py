from collections.abc import AsyncGenerator, Iterator

import pytest
from alembic.command import upgrade
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def migrated_db() -> None:
    upgrade(Config("alembic.ini"), "head")


@pytest.fixture
async def db_session(migrated_db: None) -> AsyncGenerator[AsyncSession, None]:
    from app.db.session import dispose_engine, get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.begin()
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()
            await dispose_engine()


@pytest.fixture
async def redis_client() -> AsyncGenerator[Redis, None]:
    from app.core.config import get_settings

    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()
